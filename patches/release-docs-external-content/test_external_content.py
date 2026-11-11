"""External baseline regression: disposable Git repositories; no models or messages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.environ.get('RELEASE_DOCS_ROOT', '/Users/yjl/.local/share/fastgpt-release-docs'))
import release_docs as docs


class ExternalContentTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.wt=self.root/'repo'; self.wt.mkdir()
        self.git('init','-q'); self.git('config','user.name','fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.page='document/content/self-host/upgrading/4-18/4180.mdx'
        (self.wt/self.page).parent.mkdir(parents=True)
        (self.wt/self.page).write_text('base notes\n')
        (self.wt/'app.ts').write_text('base\n')
        (self.wt/docs.APP_PACKAGE).parent.mkdir(parents=True)
        (self.wt/docs.APP_PACKAGE).write_text(json.dumps({'version':'1','dependencies':{'x':'1'}}))
        self.git('add','.'); self.git('commit','-qm','base')
        self.target=self.git('rev-parse','HEAD')
        self.run={'key':'fixture','version':'v4.18.0','target':self.target,'folder':str(self.root/'run')}
        self.config={'version':'v4.18.0','worktree':str(self.wt),'repo_root':str(self.wt)}
        self.save=patch.object(docs,'save_run'); self.save.start();self.addCleanup(self.save.stop)

    def git(self,*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false',
            '-C',str(self.wt),*args],text=True,stderr=subprocess.PIPE).strip()

    def capture(self):
        docs.preserve_external_content(self.config,self.run,capture=True)

    def test_committed_staged_unstaged_untracked_and_deleted_external_content_is_frozen(self):
        (self.wt/'app.ts').write_text('external commit\n')
        self.git('add','app.ts'); self.git('commit','-qm','manual external commit')
        (self.wt/'staged.txt').write_text('staged\n'); self.git('add','staged.txt')
        (self.wt/'app.ts').write_text('external unstaged\n')
        (self.wt/'scratch.txt').write_text('external untracked\n')
        self.capture()
        before=self.git('status','--porcelain')
        self.assertEqual(docs.scoped_changes(self.config,self.run),[])
        self.assertEqual(self.git('status','--porcelain'),before)
        for name in ['app.ts','staged.txt','scratch.txt']:
            p=self.wt/name; old=p.read_bytes(); p.unlink()
            with self.subTest(path=name),self.assertRaisesRegex(RuntimeError,'本轮改变了'):
                docs.scoped_changes(self.config,self.run)
            p.write_bytes(old)
        docs.scoped_changes(self.config,self.run)

    def test_new_external_change_and_hidden_staged_change_cannot_be_recaptured_on_retry(self):
        self.capture()
        (self.wt/'app.ts').write_text('model changed\n');self.git('add','app.ts')
        (self.wt/'app.ts').write_text('base\n')
        for call in [lambda:docs.scoped_changes(self.config,self.run),
                     lambda:docs.preserve_external_content(self.config,self.run,capture=True)]:
            with self.assertRaisesRegex(RuntimeError,'本轮改变了'):
                call()
        self.assertNotIn('app.ts',self.run['external_baseline']['files'])

    def test_package_existing_dependencies_are_allowed_but_only_version_can_change_later(self):
        p=self.wt/docs.APP_PACKAGE
        p.write_text(json.dumps({'version':'2','dependencies':{'x':'external'}}))
        self.git('add',docs.APP_PACKAGE);self.capture()
        p.write_text(json.dumps({'version':'3','dependencies':{'x':'external'}}))
        self.git('add',docs.APP_PACKAGE);docs.scoped_changes(self.config,self.run)
        p.write_text(json.dumps({'version':'3','dependencies':{'x':'model change'}}))
        with self.assertRaisesRegex(RuntimeError,'本轮改变了'):
            docs.scoped_changes(self.config,self.run)

    def test_new_baseline_is_not_allowed_after_model_execution(self):
        self.run['model_completed']=True
        with self.assertRaisesRegex(RuntimeError,'模型已执行'):
            self.capture()

    def test_publish_stages_only_docs_and_keeps_external_staging_and_unstaged_files(self):
        (self.wt/'staged.txt').write_text('external staged\n');self.git('add','staged.txt')
        (self.wt/'app.ts').write_text('external unstaged\n')
        (self.wt/'scratch.txt').write_text('external untracked\n')
        self.capture();(self.wt/self.page).write_text('updated notes\n')
        original=docs.git
        def stop(repo,args):
            if args[0]=='push': raise RuntimeError('fixture stops before network')
            return original(repo,args)
        self.run.update(branch='docs',base_tag='v4.17.4',base=self.target)
        with patch.object(docs,'latest_pr',return_value={'state':'OPEN'}),patch.object(docs,'gh',return_value=[[{'tag_name':'v4.17.4'}]]),patch.object(docs,'git',side_effect=stop),patch.dict(os.environ,{'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'commit.gpgsign','GIT_CONFIG_VALUE_0':'false'}):
            with self.assertRaisesRegex(RuntimeError,'fixture stops before network'):
                docs.publish(self.config,{},self.run)
        committed=self.git('show','--format=','--name-only','HEAD').splitlines()
        self.assertEqual(set(committed),{self.page,'staged.txt'})
        self.assertEqual((self.wt/'app.ts').read_text(),'external unstaged\n')
        self.assertEqual((self.wt/'scratch.txt').read_text(),'external untracked\n')
        self.assertIn('M app.ts',self.git('status','--porcelain'))
        docs.scoped_changes(self.config,self.run)

    def test_pro_divergent_gitlinks_keep_existing_branch_pointer_and_record_both(self):
        base=self.target
        self.git('checkout','-qb','pro-a');(self.wt/'app.ts').write_text('a\n')
        self.git('add','app.ts');self.git('commit','-qm','a');a=self.git('rev-parse','HEAD')
        self.git('checkout','-qb','pro-b',base);(self.wt/'app.ts').write_text('b\n')
        self.git('add','app.ts');self.git('commit','-qm','b');b=self.git('rev-parse','HEAD')
        self.git('checkout','-qb','docbranch',base)
        (self.wt/'pro').mkdir()
        rows=''.join(f'160000 {sha} {stage}\tpro\n' for stage,sha in [(1,base),(2,a),(3,b)])
        self.git('update-index','--force-remove','--','pro')
        subprocess.run(['git','-C',str(self.wt),'update-index','--index-info'],input=rows,text=True,check=True)
        docs.preserve_pro_merge(self.wt,self.run)
        self.assertEqual(self.git('ls-files','--stage','--','pro'),f'160000 {a} 0\tpro')
        self.assertEqual(self.run['preserved_pro_merge']['incoming'],b)
        self.capture();docs.scoped_changes(self.config,self.run)

    def test_external_real_content_conflict_is_reported_as_conflict(self):
        base=self.target
        self.git('checkout','-qb','incoming');(self.wt/'app.ts').write_text('incoming\n')
        self.git('add','app.ts');self.git('commit','-qm','incoming');incoming=self.git('rev-parse','HEAD')
        self.git('checkout','-qb','docbranch',base);(self.wt/'app.ts').write_text('ours\n')
        self.git('add','app.ts');self.git('commit','-qm','ours')
        subprocess.run(['git','-C',str(self.wt),'merge','--no-commit',incoming],capture_output=True)
        self.run['target']=incoming
        with self.assertRaisesRegex(RuntimeError,'未解决的合并冲突'):
            self.capture()
        self.assertIn('<<<<<<<',(self.wt/'app.ts').read_text())

if __name__=='__main__':unittest.main()
