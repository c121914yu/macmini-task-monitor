"""人工冲突任务的归属与回收边界，使用临时 Git 仓库。"""
import fcntl
from pathlib import Path
import unittest
from unittest.mock import patch
import test_workspace_lifecycle as fixtures
import workspace_lifecycle as life
import workspace_gc as gc


class ManualWorkspaceTests(unittest.TestCase):
    setUp = fixtures.LifecycleTests.setUp
    git = fixtures.LifecycleTests.git
    workspace = fixtures.LifecycleTests.workspace
    collect = fixtures.LifecycleTests.collect

    def manual(self):
        job, wt = self.workspace()
        old = Path(job['folder'])
        folder = old.parent/f'{job["number"]}-conflict-resolution-{job["key"][:12]}'
        old.rename(folder)
        job.update(folder=str(folder),purpose='manual conflict resolution requested by user')
        life.save(folder/'job.json',job)
        life.update(self.root,wt,job_folder=str(folder))
        return job, wt

    def test_valid_manual_task_reclaims_with_checkpoint_and_keeps_evidence(self):
        job, wt = self.manual()
        folder=Path(job['folder'])
        (folder/'publication.json').write_text('{"status":"completed"}')
        (wt/'file.txt').write_text('preserve unpublished evidence')
        self.assertEqual(self.collect(),'deleted')
        self.assertFalse(wt.exists())
        self.assertTrue((folder/'publication.json').exists())
        saved=life.read(life.rows(self.root)[0]['checkpoint'])
        self.assertTrue(saved['verified'])
        self.assertEqual(self.git('show',saved['ref']+':file.txt').strip(),'preserve unpublished evidence')
        self.assertFalse(life.admission(self.root)['paused'])

    def test_mismatched_job_and_lease_or_unknown_purpose_stay_protected(self):
        job,wt=self.manual()
        record=life.rows(self.root)[0]
        for change in ({'key':'b'*64},{'pr':2},{'path':str(wt.parent/'pr-2')},{'head':'b'*40}):
            with self.subTest(change=change):
                self.assertFalse(gc.folder_valid(self.root,Path(job['folder']),{**record,**change}))
        for change in ({'purpose':'unknown'},{'worktree':str(self.repo)},{'folder':str(self.root/'other')}):
            with self.subTest(change=change):
                life.save(Path(job['folder'])/'job.json',{**job,**change})
                with self.assertRaisesRegex(RuntimeError,'provenance'):
                    self.collect()
                self.assertTrue(wt.exists())

    def test_manual_task_lock_and_cwd_still_prevent_deletion(self):
        job,wt=self.manual()
        with (Path(job['folder'])/'job.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.assertEqual(self.collect(),'active')
        record=life.rows(self.root)[0]
        self.assertEqual(gc.collect_one(self.root,self.repo,record,processes={},paths=[wt/'file.txt']),'pinned')
        self.assertTrue(wt.exists())

    def test_symlinked_evidence_never_grants_cleanup_authority(self):
        job,wt=self.manual()
        folder=Path(job['folder'])
        original=folder/'job.json'
        saved=self.root/'evidence.json'
        original.rename(saved)
        original.symlink_to(saved)
        self.assertFalse(gc.folder_valid(self.root,folder))
        original.unlink();saved.rename(original)
        alias=self.root/'runs'/('2-conflict-resolution-'+'a'*12)
        alias.symlink_to(folder,target_is_directory=True)
        self.assertFalse(gc.folder_valid(self.root,alias))
        self.assertTrue(wt.exists())


if __name__ == '__main__':
    unittest.main()
