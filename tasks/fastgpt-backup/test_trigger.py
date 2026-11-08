"""Mac trigger receipts use mocks; no SSH or backup is executed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('trigger',Path(__file__).with_name('backup.py'))
trigger=importlib.util.module_from_spec(spec);spec.loader.exec_module(trigger)


class TriggerTests(unittest.TestCase):
    def test_wrong_remote_root_does_not_replace_cached_metadata(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);p=root/'remote-summary.json';p.write_text('original')
            response=subprocess.CompletedProcess([],0,json.dumps({'storage_root':'/unexpected'}),'')
            with patch.object(trigger,'ROOT',root),patch.object(trigger.subprocess,'run',return_value=response):
                with self.assertRaises(RuntimeError):trigger.refresh_summary()
            self.assertEqual(p.read_text(),'original')

    def test_remote_failure_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory()as tmp:
            with patch.object(trigger,'ROOT',Path(tmp)),patch.object(trigger.subprocess,'run',
                 return_value=subprocess.CompletedProcess([],1)),patch.object(trigger,'refresh_summary',
                 return_value={'backups':[{'path':'old'}]}):
                with self.assertRaises(RuntimeError):trigger.main()

    def test_stale_backup_cannot_be_reported_as_today_success(self):
        with tempfile.TemporaryDirectory()as tmp:
            with patch.object(trigger,'ROOT',Path(tmp)),patch.object(trigger.subprocess,'run',
                 return_value=subprocess.CompletedProcess([],0)),patch.object(trigger,'refresh_summary',
                 return_value={'backups':[{'path':'remote/old','date':'2000-01-01'}]}):
                with self.assertRaisesRegex(RuntimeError,'today'):trigger.main()

    def test_only_private_metadata_is_saved_locally(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)
            data={'storage_root':'/home/archer/backups/fastgpt','backups':[{'path':'remote/day'}]}
            response=subprocess.CompletedProcess([],0,json.dumps(data),'')
            with patch.object(trigger,'ROOT',root),patch.object(trigger.subprocess,'run',return_value=response):
                self.assertEqual(trigger.refresh_summary(),data)
            self.assertEqual([p.name for p in root.iterdir()],['remote-summary.json'])
            self.assertEqual((root/'remote-summary.json').stat().st_mode & 0o777,0o600)


if __name__=='__main__':unittest.main()
