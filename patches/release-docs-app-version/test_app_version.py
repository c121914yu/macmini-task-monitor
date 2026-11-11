"""Version metadata scope regression: temporary Git only, no models, PRs or notifications."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.environ.get('RELEASE_DOCS_ROOT','/Users/yjl/.local/share/fastgpt-release-docs'))
import release_docs as docs


class AppVersionTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.wt=Path(temp.name);self.app=self.wt/docs.APP_PACKAGE
        self.app.parent.mkdir(parents=True)
        self.original={'name':'@fastgpt/app','version':'4.18.0.1','scripts':{'dev':'next dev'},
                       'dependencies':{'example':'1.0.0'},'testMetadata':1}
        self.write(self.original)
        self.git('init','-q');self.git('config','user.name','fixture');self.git('config','user.email','fixture@example.invalid')
        self.git('add','.');self.git('commit','-qm','baseline')
        self.base=self.git('rev-parse','HEAD')
        self.config={'version':'v4.18.0','worktree':str(self.wt)}

    def git(self,*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false',
            '-C',str(self.wt),*args],text=True,stderr=subprocess.PIPE).strip()

    def write(self,value):
        self.app.write_text(json.dumps(value,indent=2)+'\n')

    def test_version_only_unstaged_staged_and_committed_against_fixed_target_are_allowed(self):
        self.write({**self.original,'version':'4.18.0.2'})
        docs.scoped_changes(self.config,{'target':self.base})
        self.git('add','--',docs.APP_PACKAGE)
        docs.scoped_changes(self.config,{'target':self.base})
        self.git('commit','-qm','version metadata')
        before=self.app.read_bytes()
        docs.scoped_changes(self.config,{'target':self.base})
        self.assertEqual(self.app.read_bytes(),before)
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_dependency_script_and_type_changes_are_not_accepted_as_metadata(self):
        for change in [{'dependencies':{'example':'2.0.0'}},{'scripts':{'dev':'other command'}},{'testMetadata':True}]:
            with self.subTest(change=change):
                self.write({**self.original,'version':'4.18.0.2',**change})
                before=self.app.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'仅允许修改 version'):
                    docs.scoped_changes(self.config,{'target':self.base})
                self.assertEqual(self.app.read_bytes(),before)

    def test_worktree_cannot_hide_a_staged_non_version_change(self):
        self.write({**self.original,'version':'4.18.0.2','dependencies':{'example':'2.0.0'}})
        self.git('add','--',docs.APP_PACKAGE)
        self.write({**self.original,'version':'4.18.0.2'})
        with self.assertRaisesRegex(RuntimeError,'仅允许修改 version'):
            docs.scoped_changes(self.config,{'target':self.base})
        self.assertIn('2.0.0',self.git('show',':'+docs.APP_PACKAGE))

    def test_invalid_version_json_and_file_deletion_are_still_errors(self):
        for value in [None,1,'']:
            self.write({**self.original,'version':value})
            with self.assertRaisesRegex(RuntimeError,'仅允许修改 version'):
                docs.scoped_changes(self.config,{'target':self.base})
        self.app.write_text('invalid json\n')
        with self.assertRaisesRegex(RuntimeError,'仅允许修改 version'):
            docs.scoped_changes(self.config,{'target':self.base})
        self.app.unlink()
        with self.assertRaisesRegex(RuntimeError,'仅允许修改 version'):
            docs.scoped_changes(self.config,{'target':self.base})


if __name__=='__main__':unittest.main()
