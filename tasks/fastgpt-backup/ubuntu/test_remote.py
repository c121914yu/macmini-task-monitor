"""Transport and retention boundaries, using only temporary fixture backups."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import docker_bridge as bridge
import verify_remote as verifier
import backup


class RemoteTests(unittest.TestCase):
    def test_streams_only_source_reads_over_ssh(self):
        config={'ssh':['ssh','source']}
        for args in [['inspect','--format','{{json .State}}','mongo'],['exec','pg','sh','-eu','-c','dump']]:
            command=bridge.route(args,config)
            self.assertEqual(command[:2],config['ssh'])
            self.assertNotIn('--',command)
        self.assertIn('/usr/bin/docker',bridge.route(['exec','-w','/export','client','mc'],config))

    def test_client_uses_local_host_network(self):
        command=bridge.route(['run','--network','container:fastgpt-rustfs','image'],{'ssh':['ssh','source']})
        self.assertEqual(command[command.index('--network')+1],'host')
        with self.assertRaises(ValueError):
            bridge.route(['run','--network','untrusted','image'],{'ssh':['ssh','source']})

    def fixture(self,root):
        content=b'fixture'
        (root/'data').write_bytes(content)
        manifest={'owner':verifier.OWNER,'status':'complete','date':'2026-11-08','files':[
            {'path':'data','bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}]}
        (root/'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_remote_hash_and_symlink_verification(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);self.fixture(root)
            self.assertEqual(verifier.verify(root)['status'],'complete')
            (root/'data').write_bytes(b'corrupt')
            with self.assertRaises(RuntimeError):verifier.verify(root)
            (root/'data').unlink();(root/'data').symlink_to('/etc/hostname')
            with self.assertRaises(RuntimeError):verifier.verify(root)

    def test_manifest_traversal_or_extra_files_cannot_pass(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);manifest=self.fixture(root)
            manifest['files'][0]['path']='../data'
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(RuntimeError):verifier.verify(root)
            self.fixture(root);(root/'unexpected').write_text('extra')
            with self.assertRaises(RuntimeError):verifier.verify(root)

    def test_seven_calendar_days_and_ownership_are_preserved(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)
            for date,owner in [('2026-11-01',verifier.OWNER),('2026-11-02',verifier.OWNER),
                                ('2026-10-01','other')]:
                path=root/date;path.mkdir()
                (path/'manifest.json').write_text(json.dumps({'owner':owner,'status':'complete'}))
            backup.prune_backups(root,dt.date(2026,11,8))
            self.assertFalse((root/'2026-11-01').exists())
            self.assertTrue((root/'2026-11-02').exists())
            self.assertTrue((root/'2026-10-01').exists())


if __name__=='__main__':
    unittest.main()
