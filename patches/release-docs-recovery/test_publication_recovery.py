import contextlib
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock
import release_docs as docs
import test_release_docs as fixtures


class PublicationRecoveryTest(unittest.TestCase):
    setUp = fixtures.MergeRecoveryTest.setUp
    git = fixtures.MergeRecoveryTest.git
    prepare = fixtures.MergeRecoveryTest.prepare
    def test_merge_deleted_product_paths_are_not_added_again(self):
        self.prepare()
        self.git('commit', '-qm', 'finish initial merge')
        self.git('checkout', '-qb', 'deletion')
        self.git('rm', 'app.ts')
        self.git('commit', '-qm', 'delete product file')
        self.target = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', 'docs')
        (self.wt/self.page).write_text('parallel document edit\n')
        self.git('add',self.page)
        self.git('commit','-qm','parallel notes')
        self.git('merge', '--no-commit', self.target)
        (self.wt/self.page).write_text('updated notes\n')
        run = {**self.run, 'target':self.target,'branch':'docs'}
        def stop_after_commit(repo,args):
            if args[0] == 'push':
                raise RuntimeError('fixture stops before network')
            return original(repo,args)
        original=docs.git
        with patch.object(docs,'latest_pr',return_value={'state':'OPEN'}), patch.object(docs,'gh',return_value=[[{'tag_name':'v4.17.4'}]]), patch.object(docs,'save_run'), patch.object(docs,'git',side_effect=stop_after_commit), patch.dict('os.environ',{'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'commit.gpgsign','GIT_CONFIG_VALUE_0':'false'}):
            with self.assertRaisesRegex(RuntimeError,'fixture stops before network'):
                docs.publish(self.config,{},run)
        self.assertEqual(self.git('status','--porcelain'),'')
        self.assertEqual(self.git('diff','--name-only',self.target),self.page)
        self.assertFalse((self.wt/'app.ts').exists())
        self.assertEqual(self.git('rev-parse','HEAD^2'),self.target)

    def test_hidden_staged_product_change_is_rejected(self):
        self.prepare()
        (self.wt/'app.ts').write_text('hidden staged change\n')
        self.git('add','app.ts')
        (self.wt/'app.ts').write_text('upstream product\n')
        with patch.object(docs,'latest_pr',return_value={'state':'OPEN'}), patch.object(docs,'gh',return_value=[[{'tag_name':'v4.17.4'}]]):
            with self.assertRaisesRegex(RuntimeError,'文档范围外'):
                docs.publish(self.config,{},dict(self.run,branch='docs'))
        self.assertIn('hidden staged change',self.git('show',':app.ts'))


class BaselineRecoveryTest(unittest.TestCase):
    setUp = fixtures.SchedulingTest.setUp
    git = fixtures.SchedulingTest.git
    def test_new_release_does_not_replace_exhausted_pending_base(self):
        folder=self.root/'runs'/'old'
        run={'key':'old','version':'v4.18.0','folder':str(folder),'status':'error','attempts':5,
             'base':'old-base','base_tag':'v4.17.3','target':'old-target'}
        docs.save(folder/'state.json',run)
        docs.save(docs.STATE,{'version':'v4.18.0','active_run':str(folder)})
        def git(repo,args):
            if args == ['rev-parse','v4.17.4^{commit}']:
                raise AssertionError('must not resolve a new base for pending run')
            return self.git(repo,args)
        with patch.object(docs,'gh',return_value=[[{'tag_name':'v4.17.4'}]]), patch.object(docs,'git',side_effect=git):
            docs.scan(type('Options',(),{'check_only':False})())
        self.assertEqual(docs.read(folder/'state.json'),run)
        self.generate.assert_not_called()

    def test_maintenance_release_uses_release_sha_not_common_ancestor(self):
        calls=[]
        def git(repo,args):
            calls.append(args)
            if args[0]=='merge-base': return 'common-ancestor'
            return self.git(repo,args)
        out=io.StringIO()
        with patch.object(docs,'git',side_effect=git), contextlib.redirect_stdout(out):
            docs.scan(type('Options',(),{'check_only':True})())
        self.assertIn(['merge-base','base','target'],calls)
        self.assertEqual(json.loads(out.getvalue())['event'],'docs_pending')
        self.generate.assert_not_called()

    def test_publication_failure_keeps_completed_model_receipt(self):
        with patch.object(docs,'prepare_worktree'), patch.object(docs,'validate'), patch.object(docs,'publish',side_effect=RuntimeError('publish failed')), patch.object(docs,'generate',new_callable=AsyncMock) as generate:
            async def finish(config,state,run):
                run['model_completed']=True
            generate.side_effect=finish
            with self.assertRaisesRegex(RuntimeError,'publish failed'):
                docs.scan(type('Options',(),{'check_only':False})())
        run=docs.read(Path(docs.read(docs.STATE)['active_run'])/'state.json')
        self.assertTrue(run['model_completed'])
        self.assertEqual(run['attempts'],1)

if __name__ == '__main__':
    unittest.main()
