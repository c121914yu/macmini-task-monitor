"""Model-input aggregation contracts; temporary fixtures only, no network, AI or notifications."""
import collections
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import run
import signoz_digest as digest


class SigNozDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root/'runs/signoz-report/2026-11-08'
        self.folder.mkdir(parents=True)
        self.start, self.end = common.window('signoz-report', '2026-11-08', 0)
        self.ns = int(self.start.timestamp())*1_000_000_000
        self.data = {day: {kind: {'complete': True, 'total': 0, 'rows': []}
                          for kind in digest.KINDS} for day in ('current', 'previous')}
        self.data['http_correlations'] = []

    def llm(self, index, message='download failed', *, model='model-a', status=400,
            code='download_error', trace='', request='', stage='LLM response error'):
        error = {'message': message, 'status': status, 'code': code,
                 'headers': {'Authorization': 'Bearer PRIVATE_AUTH_FIXTURE'},
                 'request': {'messages': ['PRIVATE_USER_INPUT_FIXTURE']}}
        return [self.ns+index, f'private-log-id-{index}', trace, request,
                json.dumps(error), model, '', '', stage, 'vendor', '']

    def put(self, kind, rows, day='current'):
        self.data[day][kind] = {'complete': True, 'total': len(rows), 'rows': rows}

    def prepare(self):
        common.save(self.folder/'signoz.json', self.data)
        stats = digest.prepare_digest(self.folder, self.start, self.end)
        return json.loads((self.folder/'signoz-digest.json').read_text()), stats

    def test_dynamic_errors_merge_across_models_but_keep_counts_and_bounded_examples(self):
        self.put('llm', [self.llm(i, f'download https://private.example/{i}?token=PRIVATE_QUERY_FIXTURE failed',
                                   model=f'model-{i%3}') for i in range(1000)])
        output, stats = self.prepare()
        item = output['current']['llm']
        self.assertEqual(item['total'], 1000)
        self.assertEqual(item['group_count'], 1)
        group = item['groups'][0]
        self.assertEqual(group['count'], 1000)
        self.assertLessEqual(len(group['examples']), 2)
        self.assertEqual(sum(v['count'] for v in group['models']['top']), 1000)
        self.assertEqual(group['requests_approx'], 1000)
        self.assertEqual(group['missing_request_identifier_logs'], 1000)
        self.assertLess(stats['digest_bytes'], stats['raw_bytes']//10)
        text = (self.folder/'signoz-digest.json').read_text()
        for secret in ('PRIVATE_AUTH_FIXTURE', 'PRIVATE_USER_INPUT_FIXTURE', 'PRIVATE_QUERY_FIXTURE',
                       'private.example', 'private-log-id-', '"rows"', '"headers"'):
            self.assertNotIn(secret, text)

    def test_status_code_error_code_and_distinct_unknown_semantics_do_not_collapse(self):
        rows = [self.llm(1, 'request timed out', status=400, code='timeout_a'),
                self.llm(2, 'request timed out', status=500, code='timeout_a'),
                self.llm(3, 'request timed out', status=400, code='timeout_b'),
                self.llm(4, 'opaque condition A https://example.invalid/a', code='opaque'),
                self.llm(5, 'opaque condition A https://example.invalid/b', code='opaque'),
                self.llm(6, 'opaque condition B PRIVATE_MESSAGE_FIXTURE', code='opaque')]
        self.put('llm', rows)
        output, _ = self.prepare()
        self.assertEqual(output['current']['llm']['group_count'], 5)
        unknown = [g for g in output['current']['llm']['groups'] if g['signature']['family']=='unclassified']
        self.assertEqual(sorted(g['count'] for g in unknown), [1, 2])
        self.assertNotIn('PRIVATE_MESSAGE_FIXTURE', json.dumps(output))

    def test_request_aliases_and_ambiguous_routes_remain_conservative(self):
        trace = 'a'*32
        self.put('llm', [self.llm(1, trace=trace, request='request-one'),
                         self.llm(2, trace=trace)])
        self.data['http_correlations'] = [[trace, '/api/a', ''], [trace, '/api/b', '']]
        output, _ = self.prepare()
        group = output['current']['llm']['groups'][0]
        self.assertEqual(group['count'], 2)
        self.assertEqual(group['requests_approx'], 1)
        self.assertEqual(group['routes']['top'], [{'value': '接口未知', 'count': 2}])
        self.assertEqual(digest.safe_route('https://private.example/api/chat/123?secret=x'), '/api/chat/:id')

    def test_zod_array_indices_merge_but_constraints_stay_distinct_without_raw_values(self):
        rows = []
        for index, minimum in enumerate([3, 3, 5]):
            issue = {'code': 'too_small', 'path': ['items', index, 'name'], 'minimum': minimum,
                     'expected': 'string', 'received': 'PRIVATE_RECEIVED_FIXTURE',
                     'message': 'PRIVATE_ZOD_MESSAGE_FIXTURE', 'input': 'PRIVATE_ZOD_INPUT_FIXTURE'}
            rows.append([self.ns+index, f'zod-{index}', '', '', json.dumps([issue]), 'Zod validation error'])
        self.put('zod', rows)
        output, _ = self.prepare()
        self.assertEqual(sorted(g['count'] for g in output['current']['zod']['groups']), [1, 2])
        self.assertNotIn('PRIVATE_', json.dumps(output))

    def test_plugin_identity_version_and_previous_day_comparison_are_preserved(self):
        rows = [[self.ns+i, f'plugin-{i}', '', '', 'system.search', '', '', 'system',
                 json.dumps(version), json.dumps(True), 'Tool Detail Error'] for i,version in enumerate(['1', '2'])]
        self.put('plugin', rows)
        old = self.llm(3)
        old[0] -= 86400*1_000_000_000
        self.put('llm', [old], 'previous')
        self.put('llm', [self.llm(4), self.llm(5)])
        output, _ = self.prepare()
        plugin = output['current']['plugin']['groups'][0]
        self.assertEqual(plugin['count'], 2)
        self.assertEqual(plugin['versions']['distinct'], 2)
        self.assertEqual(plugin['fallback_true_logs'], 2)
        self.assertEqual(output['current']['llm']['groups'][0]['previous_count'], 1)

    def test_size_budget_and_long_tail_keep_every_log_in_the_total(self):
        self.put('llm', [self.llm(i, f'opaque error variant {i}', code='opaque') for i in range(100)])
        with patch.object(digest, 'MAX_DIGEST_BYTES', 7000):
            output, stats = self.prepare()
        item = output['current']['llm']
        self.assertEqual(item['group_count'], 100)
        self.assertLess(len(item['groups']), 20)
        self.assertEqual(sum(g['count'] for g in item['groups'])+item['other']['count'], 100)
        self.assertEqual(item['other']['group_count']+len(item['groups']), 100)
        self.assertLessEqual((self.folder/'signoz-digest.json').stat().st_size, 7000)
        self.assertEqual(stats['digest_bytes'], (self.folder/'signoz-digest.json').stat().st_size)

    def test_incomplete_duplicate_and_out_of_window_logs_fail_before_model(self):
        cases = []
        incomplete = {'complete': False, 'total': 0, 'rows': []}
        row = self.llm(1)
        cases.extend([incomplete, {'complete': True, 'total': 2, 'rows': [row, row]},
                      {'complete': True, 'total': 1, 'rows': [[self.ns-1, *row[1:]]]},
                      {'complete': True, 'total': 99, 'rows': [row]}])
        for item in cases:
            with self.subTest(item=item['total']):
                self.data['current']['llm'] = item
                with self.assertRaises(digest.DigestError):
                    self.prepare()
                self.assertFalse((self.folder/'signoz-digest.json').exists())

    def test_runner_builds_digest_before_model_and_invalid_input_never_starts_ai(self):
        common.save(self.folder/'signoz.json', self.data)
        async def execute(*args):
            self.assertTrue((self.folder/'signoz-digest.json').is_file())
            return {'status': 'complete', 'summary': 'fixture complete'}
        with patch.object(run, 'ROOT', self.root), patch.object(common, 'ROOT', self.root), \
                patch.object(run, 'check_config', return_value={'enabled': True}), \
                patch.object(run, 'native_paused', return_value=True), \
                patch.object(run, 'period', return_value='2026-11-08'), \
                patch.dict(run.COLLECTORS, {'signoz-report': lambda *args: {}}), \
                patch.object(run, 'execute_model', side_effect=execute) as model:
            self.assertEqual(run.run('signoz-report'), 0)
            self.assertEqual(model.call_count, 1)
            (self.folder/'state.json').unlink()
            self.data['current']['llm']['complete'] = False
            common.save(self.folder/'signoz.json', self.data)
            model.reset_mock()
            self.assertEqual(run.run('signoz-report'), 1)
            model.assert_not_called()
        state = common.read(self.folder/'state.json')
        self.assertEqual(state['result_error']['category'], 'input_aggregation')


if __name__ == '__main__':
    unittest.main()
