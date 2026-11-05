#!/usr/bin/env python3
"""仅安装巡检任务及单个看板条目，不调用统一看板的全量任务安装器。"""
import ast
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT=Path('/Users/yjl/.local/share/task-health-watchdog')
MONITOR=Path('/Users/yjl/.local/share/macmini-task-monitor')
LABEL='com.yjl.task-health-watchdog'
ENTRY={'id':'task-health-watchdog','name':'定时任务巡检与修复','icon':'radar','category':'work',
       'label':LABEL,'source':'launchd',
       'description':'每小时第 20 分钟纯脚本检查，持久失败才调用 Codex 修复；同一任务复用会话，单并发，最多 3 轮。',
       'script':str(ROOT/'watchdog.py'),'logs':['task.log','task.stderr.log'],'log_root':str(ROOT)}

def install():
    os.umask(0o077)
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ['watchdog.py','repair_agent.py','policy.json','AGENTS.md']:
        if not (ROOT/name).is_file(): raise RuntimeError('Missing runtime file: '+name)
    config=MONITOR/'config.py'
    before=config.read_text()
    parsed=ast.parse(before)
    # 看板配置可能正有其他维护改动，只插入本任务，不覆盖其余条目。
    registered="'id': 'task-health-watchdog'" in before or '"id": "task-health-watchdog"' in before
    if not registered:
        marker='TASKS = [\n'
        if before.count(marker)!=1: raise RuntimeError('Task registry location ambiguous')
        updated=before.replace(marker,marker+'    '+repr(ENTRY)+',\n',1)
        ast.parse(updated)
        temp=config.with_name('config.watchdog.tmp')
        temp.write_text(updated)
        temp.chmod(config.stat().st_mode & 0o777)
        if config.read_text()!=before:
            temp.unlink();raise RuntimeError('Monitor registry changed concurrently; retry install')
        temp.replace(config)
    definition=Path('/Users/yjl/Library/LaunchAgents')/(LABEL+'.plist')
    job={'Label':LABEL,'ProgramArguments':['/opt/homebrew/bin/python3',str(MONITOR/'runner.py'),
         '--task-id','task-health-watchdog','--','/opt/homebrew/bin/python3','-u',str(ROOT/'watchdog.py')],
         'WorkingDirectory':str(ROOT),'EnvironmentVariables':{
             'HOME':'/Users/yjl','LANG':'en_US.UTF-8',
             'PATH':'/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin:/opt/homebrew/bin:/Users/yjl/Library/pnpm:/usr/bin:/bin:/usr/sbin:/sbin'},
         'RunAtLoad':True,'StartCalendarInterval':{'Minute':20},
         'StandardOutPath':str(ROOT/'task.log'),'StandardErrorPath':str(ROOT/'task.stderr.log'),
         'ProcessType':'Background','Umask':63}
    data=plistlib.dumps(job)
    domain='gui/'+str(os.getuid())
    loaded=subprocess.run(['launchctl','print',domain+'/'+LABEL],capture_output=True).returncode==0
    if loaded and definition.exists() and definition.read_bytes()!=data:
        raise RuntimeError('Existing watchdog definition differs; inspect running job before changing')
    definition.write_bytes(data);definition.chmod(0o600)
    if not loaded:
        subprocess.run(['launchctl','bootstrap',domain,str(definition)],check=True,capture_output=True)
    # 只重启只读展示服务，使新增条目生效，不重启扫描/执行器或其他任务。
    subprocess.run(['launchctl','kickstart','-k',domain+'/com.yjl.macmini-task-monitor'],check=True,capture_output=True)
    print('Installed task-health-watchdog: hourly at minute 20; existing business jobs unchanged')

if __name__=='__main__':install()
