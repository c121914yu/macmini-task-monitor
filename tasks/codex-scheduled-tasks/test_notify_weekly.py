"""Weekly author coverage regressions; no credentials, network or real delivery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import common
import notify


class WeeklyDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'runs/pr-weekly-report/2026-11-06'
        self.folder.mkdir(parents=True)
        self.pr_url = 'https://github.com/example/project/pull/1'
        self.items = [{'url': self.pr_url, 'author': {'login': 'app/dependabot', 'is_bot': True}}]
        self.payloads = [{'msg_type': 'interactive', 'card': {
            'schema': '2.0', 'body': {'elements': []}}}]
        self.batch = self.folder / 'notifications.json'
        common.save(self.batch, {'payloads': self.payloads})

    def write_document(self, body, items=None):
        """Save synthetic PR metadata and the exact XML returned by docs fetch."""
        common.save(self.folder / 'prs.json', {'example/project': items or self.items})
        (self.folder / 'document-readback.xml').write_text(body)

    def entry(self, author_url, pr_url=None):
        return (f'<li><b>Summary</b><br/><a href="{pr_url or self.pr_url}">PR</a> '
                f'<a href="{author_url}">Author</a></li>')

    def validate(self):
        notify.validate_business('pr-weekly-report', '2026-11-06', self.folder, self.payloads)

    def test_graphql_app_actor_accepts_canonical_app_profile(self):
        self.write_document('<ul>' + self.entry('https://github.com/apps/dependabot') + '</ul>')
        self.validate()

    def test_rest_bot_login_accepts_canonical_app_profile(self):
        self.items[0]['author'] = {'login': 'dependabot[bot]', 'is_bot': True}
        self.write_document('<ul>' + self.entry('https://github.com/apps/dependabot') + '</ul>')
        self.validate()

    def test_regular_author_keeps_user_profile(self):
        self.items[0]['author'] = {'login': 'maintainer', 'is_bot': False}
        self.write_document('<ul>' + self.entry('https://github.com/maintainer') + '</ul>')
        self.validate()

    def test_other_app_actor_is_not_dependabot_specific(self):
        self.items[0]['author'] = {'login': 'app/github-actions', 'is_bot': True}
        self.write_document('<ul>' + self.entry('https://github.com/apps/github-actions') + '</ul>')
        self.validate()

    def test_old_synthesized_bot_profile_is_rejected(self):
        self.write_document('<ul>' + self.entry('https://github.com/app/dependabot') + '</ul>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_profile_prefix_does_not_match_different_author(self):
        self.items[0]['author'] = {'login': 'maintainer'}
        self.write_document('<ul>' + self.entry('https://github.com/maintainer-other') + '</ul>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_swapped_authors_in_different_prs_are_rejected(self):
        items = [
            {'url': self.pr_url, 'author': {'login': 'alice'}},
            {'url': self.pr_url + '2', 'author': {'login': 'bob'}}]
        self.write_document('<ul>' + self.entry('https://github.com/bob') +
                            self.entry('https://github.com/alice', self.pr_url + '2') + '</ul>', items)
        with self.assertRaises(ValueError):
            self.validate()

    def test_author_link_outside_pr_item_does_not_count(self):
        self.write_document('<ul>' + self.entry('https://github.com/wrong') + '</ul>'
                            '<p><a href="https://github.com/apps/dependabot">Bot</a></p>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_nested_item_does_not_supply_parent_author(self):
        self.write_document('<ul><li><a href="' + self.pr_url + '">PR</a><ul><li>'
                            '<a href="https://github.com/apps/dependabot">Bot</a>'
                            '</li></ul></li></ul>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_urls_in_plain_text_are_not_links(self):
        self.write_document('<p>' + self.pr_url + ' https://github.com/apps/dependabot</p>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_pr_is_rejected(self):
        self.write_document('<ul>' + self.entry('https://github.com/apps/dependabot', self.pr_url + '2') + '</ul>')
        with self.assertRaises(ValueError):
            self.validate()

    def test_malformed_xml_is_rejected(self):
        self.write_document('<ul><li>Unclosed')
        with self.assertRaises(ValueError):
            self.validate()

    def test_validated_bot_delivers_once_with_mock_sender(self):
        self.write_document('<ul>' + self.entry('https://github.com/apps/dependabot') + '</ul>')
        sender = MagicMock(return_value={'status': 'sent'})
        original_read = common.read

        def fake_config(path, default=None):
            # No real or synthetic private.json is opened during this test.
            if Path(path) == self.root / 'private.json':
                return {'pr-weekly-report': {'webhook': 'https://example.invalid/fixture'}}
            return original_read(path, default)

        with patch.object(common, 'ROOT', self.root), patch.object(notify, 'ROOT', self.root), \
                patch.object(notify, 'read', side_effect=fake_config):
            first = notify.deliver('pr-weekly-report', '2026-11-06', self.batch, sender)
            second = notify.deliver('pr-weekly-report', '2026-11-06', self.batch, sender)
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'sent')
        sender.assert_called_once()

    def test_invalid_coverage_stops_before_config_or_sender(self):
        self.write_document('<ul>' + self.entry('https://github.com/wrong') + '</ul>')
        sender = MagicMock()
        original_read = common.read

        def forbid_private(path, default=None):
            self.assertNotEqual(Path(path).name, 'private.json')
            return original_read(path, default)

        with patch.object(common, 'ROOT', self.root), patch.object(notify, 'ROOT', self.root), \
                patch.object(notify, 'read', side_effect=forbid_private):
            with self.assertRaises(ValueError):
                notify.deliver('pr-weekly-report', '2026-11-06', self.batch, sender)
        sender.assert_not_called()
        self.assertFalse((self.folder / 'notification.json').exists())


if __name__ == '__main__':
    unittest.main()
