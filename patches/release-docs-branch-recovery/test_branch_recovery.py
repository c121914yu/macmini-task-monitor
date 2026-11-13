"""Git/lifecycle regression in isolated repositories; no models, messages or real remotes."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.environ.get('RELEASE_DOCS_ROOT','/Users/yjl/.local/share/fastgpt-release-docs'))
import release_docs as docs
import test_external_content as external
import test_release_docs as fixtures


class BranchRecoveryTest(unittest.TestCase):
    git=external.ExternalContentTest.git

    def setUp(self):
        external.ExternalContentTest.setUp(self)
        self.git('config','commit.gpgsign','false')
        self.state_patch=patch.object(docs,'STATE',self.root/'state.json');self.state_patch.start();self.addCleanup(self.state_patch.stop)
        bare=self.root/'origin.git'
        subprocess.run(['git','init','--bare','-q',str(bare)],check=True,capture_output=True)
        self.git('remote','add','origin',str(bare))
        self.state={'version':'v4.18.0','branch':'docs','pr_number':123,'active_run':self.run['folder'],
                    'checked_sha':'previous','last_success':'previous'}

    def diverge(self, conflict=False):
        self.git('checkout','-qb','remote-edit')
        (self.wt/(self.page if conflict else 'remote.txt')).write_text('remote content\n')
        self.git('add','.');self.git('commit','-qm','remote edit')
        self.remote=self.git('rev-parse','HEAD')
        self.git('push','-q','origin','HEAD:refs/heads/docs')
        self.git('checkout','-qb','docs',self.target)
        (self.wt/self.page).write_text('local notes\n');self.git('add',self.page)
        self.git('commit','-qm','local document edit');self.local=self.git('rev-parse','HEAD')

    def prepare(self):
        with patch.object(docs,'latest_pr',return_value={'state':'OPEN'}):
            docs.prepare_worktree(self.config,self.state,self.run)

    def test_clean_divergence_merges_both_histories_and_preserves_both_contents(self):
        self.diverge();self.prepare()
        self.assertEqual(self.git('merge-base',self.local,'HEAD'),self.local)
        self.assertEqual(self.git('merge-base',self.remote,'HEAD'),self.remote)
        self.assertEqual((self.wt/'remote.txt').read_text(),'remote content\n')
        self.assertEqual((self.wt/self.page).read_text(),'local notes\n')
        self.assertEqual(self.run['remote_sync']['head'],self.remote)
        docs.scoped_changes(self.config,self.run)

    def test_document_conflict_resumes_same_remote_merge_without_refetch(self):
        self.diverge(conflict=True);self.prepare()
        self.assertEqual(self.git('rev-parse','MERGE_HEAD'),self.remote)
        self.assertIn('<<<<<<<',(self.wt/self.page).read_text())
        original=docs.git
        def no_fetch(repo,args):
            if args[0]=='fetch':raise AssertionError('must resume pinned merge without a new fetch')
            return original(repo,args)
        with patch.object(docs,'git',side_effect=no_fetch):self.prepare()
        (self.wt/self.page).write_text('resolved: local and remote notes\n')
        docs.stage_resolved_conflicts(self.config)
        docs.scoped_changes(self.config,self.run)
        self.git('commit','-qm','resolve pinned merge')
        self.assertEqual(self.git('rev-parse','HEAD^2'),self.remote)

    def test_dirty_local_content_blocks_remote_merge_without_overwriting_files(self):
        self.diverge();(self.wt/self.page).write_text('unfinished local notes\n')
        with self.assertRaisesRegex(RuntimeError,'本地已有未完成内容'):
            self.prepare()
        self.assertEqual(self.git('rev-parse','HEAD'),self.local)
        self.assertEqual((self.wt/self.page).read_text(),'unfinished local notes\n')

    def test_existing_baseline_or_model_receipt_cannot_be_laundered_by_remote_sync(self):
        self.diverge()
        docs.preserve_external_content(self.config,self.run,capture=True)
        with self.assertRaisesRegex(RuntimeError,'生成凭据'):
            self.prepare()
        self.run.pop('external_baseline');self.run['model_completed']=True
        with self.assertRaisesRegex(RuntimeError,'生成凭据'):
            self.prepare()
        self.assertEqual(self.git('rev-parse','HEAD'),self.local)

    def merged_pr(self):
        self.git('checkout','-qb','docs')
        (self.wt/'merged.txt').write_text('merged PR\n');self.git('add','merged.txt');self.git('commit','-qm','merged')
        merged=self.git('rev-parse','HEAD');self.git('update-ref','refs/remotes/upstream/main',merged)
        self.run.update(status='error',attempts=5,error='previous ff-only failure')
        return {'number':123,'state':'MERGED','headRefName':'docs','merge_commit_sha':merged}

    def test_merged_pr_retires_exhausted_run_without_resetting_budget_or_advancing_coverage(self):
        pr=self.merged_pr();head=self.git('rev-parse','HEAD')
        with patch.object(docs,'latest_pr',return_value=pr):
            self.assertTrue(docs.reconcile_merged_pr(self.config,self.state,self.run))
        self.assertEqual(self.run['status'],'superseded');self.assertEqual(self.run['attempts'],5)
        self.assertEqual(self.run['error'],'previous ff-only failure')
        self.assertNotIn('model_completed',self.run);self.assertNotIn('active_run',self.state)
        self.assertEqual(self.state['checked_sha'],'previous');self.assertEqual(self.state['last_success'],'previous')
        self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assertEqual(docs.read(docs.STATE)['status'],'idle')

    def test_merged_pr_with_dirty_worktree_preserves_the_unfinished_content(self):
        pr=self.merged_pr();(self.wt/self.page).write_text('unfinished\n')
        with patch.object(docs,'latest_pr',return_value=pr),self.assertRaisesRegex(RuntimeError,'有未提交内容'):
            docs.reconcile_merged_pr(self.config,self.state,self.run)
        self.assertEqual(self.run['status'],'error');self.assertIn('active_run',self.state)
        self.assertEqual((self.wt/self.page).read_text(),'unfinished\n')

    def test_uncertain_model_turn_is_not_retired_without_reconciliation(self):
        pr=self.merged_pr();self.run['turn_id']='uncertain-turn'
        with patch.object(docs,'latest_pr',return_value=pr),self.assertRaisesRegex(RuntimeError,'原模型轮次结果尚未核对'):
            docs.reconcile_merged_pr(self.config,self.state,self.run)
        self.assertEqual(self.run['status'],'error');self.assertIn('active_run',self.state)

    def test_unverified_merge_or_wrong_branch_does_not_retire_the_run(self):
        pr=self.merged_pr()
        for value in [{**pr,'headRefName':'other-branch'}, {**pr,'merge_commit_sha':None}]:
            with self.subTest(value=value),patch.object(docs,'latest_pr',return_value=value),self.assertRaises(RuntimeError):
                docs.reconcile_merged_pr(self.config,self.state,self.run)
        self.git('update-ref','refs/remotes/upstream/main',self.target)
        with patch.object(docs,'latest_pr',return_value=pr),self.assertRaisesRegex(RuntimeError,'未包含在当前 main'):
            docs.reconcile_merged_pr(self.config,self.state,self.run)
        self.assertEqual(self.run['status'],'error');self.assertEqual(self.run['attempts'],5)


class ScanReconciliationTest(unittest.TestCase):
    setUp=fixtures.SchedulingTest.setUp
    git=fixtures.SchedulingTest.git

    def pending(self):
        folder=self.root/'runs'/'old'
        run={'key':'old','version':'v4.18.0','folder':str(folder),'status':'error','attempts':5,
             'base':'base','base_tag':'v4.17.3','target':'old-target','error':'ff-only failed'}
        docs.save(folder/'state.json',run)
        docs.save(docs.STATE,{'version':'v4.18.0','active_run':str(folder),'branch':'docs','pr_number':123,
                             'checked_sha':'previous','last_success':'previous'})
        return folder

    def test_scan_checks_merged_pr_before_retry_limit_and_does_not_start_model(self):
        folder=self.pending()
        def git(repo,args):
            if args[0]=='merge-base':return args[1]
            return self.git(repo,args)
        with patch.object(docs,'latest_pr',return_value={'state':'MERGED','headRefName':'docs','merge_commit_sha':'merge'}),patch.object(docs,'git',side_effect=git),patch.object(docs,'prepare_worktree') as prepare:
            docs.scan(type('Options',(),{'check_only':False})())
        self.generate.assert_not_called();prepare.assert_not_called()
        self.assertEqual(docs.read(folder/'state.json')['status'],'superseded')
        self.assertEqual(docs.read(folder/'state.json')['attempts'],5)
        self.assertEqual(docs.read(docs.STATE)['checked_sha'],'previous')

    def test_open_or_closed_unmerged_pr_keeps_exhausted_budget(self):
        folder=self.pending()
        for state in ['OPEN','CLOSED']:
            with patch.object(docs,'latest_pr',return_value={'state':state}):
                docs.scan(type('Options',(),{'check_only':False})())
            self.assertEqual(docs.read(folder/'state.json')['status'],'error')
            self.assertEqual(docs.read(folder/'state.json')['attempts'],5)
        self.generate.assert_not_called()

    def test_check_only_does_not_mutate_or_reconcile(self):
        folder=self.pending();before=(folder/'state.json').read_bytes()
        with patch.object(docs,'reconcile_merged_pr') as reconcile:
            docs.scan(type('Options',(),{'check_only':True})())
        reconcile.assert_not_called();self.assertEqual((folder/'state.json').read_bytes(),before)
        self.generate.assert_not_called()

if __name__=='__main__':unittest.main()
