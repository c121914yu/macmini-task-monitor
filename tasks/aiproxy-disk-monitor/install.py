#!/usr/bin/env python3
"""安装纯脚本每日检查及只读看板登记；凭据由运行目录私有配置提供。"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import plistlib
import py_compile
import subprocess

ROOT=Path('/Users/yjl/.local/share/aiproxy-disk-monitor')
MONITOR=Path('/Users/yjl/.local/share/macmini-task-monitor')
LABEL='com.yjl.aiproxy-disk-monitor'
TASK={'id':'aiproxy-disk-monitor','name':'aiproxy-io 磁盘检查','icon':'database','category':'work',
      'source':'launchd','label':LABEL,
      'description':'每天北京时间 10:00 通过 SSH 只读检查根分区；剩余不足 10GB 时推送飞书。纯脚本，同日告警去重。',
      'script':str(ROOT/'check.py'),'logs':['task.log','task.stderr.log'],'log_root':str(ROOT)}


def install():
    """只追加本任务注册，不覆盖其他看板配置；系统调度采用已确认的北京时间。"""
    if datetime.now().astimezone().utcoffset()!=timedelta(hours=8):
        raise RuntimeError('Host timezone must be Asia/Shanghai')
    for name in ['config.json','known_hosts','check.py']:
        if not (ROOT/name).exists():raise RuntimeError('Missing runtime file: '+name)
    config=MONITOR/'config.py';text=config.read_text()
    if "'id': 'aiproxy-disk-monitor'" not in text:
        if text.count('TASKS = [')!=1:raise RuntimeError('Unexpected task registry')
        updated=text.replace('TASKS = [','TASKS = [\n    '+repr(TASK)+',',1)
        temporary=config.with_suffix('.install-tmp.py');temporary.write_text(updated)
        py_compile.compile(str(temporary),doraise=True)
        temporary.chmod(config.stat().st_mode&0o777);temporary.replace(config)
    path=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    if path.exists():raise RuntimeError('Existing schedule: inspect before reinstalling')
    definition={'Label':LABEL,'ProgramArguments':['/opt/homebrew/bin/python3',str(MONITOR/'runner.py'),
                '--task-id',TASK['id'],'--','/opt/homebrew/bin/python3','-u',str(ROOT/'check.py')],
                'WorkingDirectory':str(ROOT),'StartCalendarInterval':{'Hour':10,'Minute':0},
                'RunAtLoad':False,'StandardOutPath':str(ROOT/'task.log'),
                'StandardErrorPath':str(ROOT/'task.stderr.log'),
                'EnvironmentVariables':{'TZ':'Asia/Shanghai','PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
    path.write_bytes(plistlib.dumps(definition,sort_keys=False));path.chmod(0o600)
    subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
    subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/com.yjl.macmini-task-monitor'],check=True)
    print('Installed aiproxy disk check: every day 10:00 Asia/Shanghai; RunAtLoad=false')


if __name__=='__main__':install()
