#!/usr/bin/env python3
"""Install four launchd calendars. Check first, then pause native timers and activate."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time
import tomllib

from common import ROOT, TASK_IDS, configuration, read, save
from run import check_config

PYTHON='/opt/homebrew/bin/python3'
MONITOR=Path.home()/'.local/share/macmini-task-monitor'
AGENTS=Path.home()/'Library/LaunchAgents'
CALENDARS={
    'mail-digest':{'Hour':7,'Minute':0},
    'signoz-report':{'Hour':7,'Minute':0},
    'plugin-model-sync':{'Hour':1,'Minute':30},
    'pr-weekly-report':{'Weekday':5,'Hour':10,'Minute':0},
}


def launch(args,check=True):
    r=subprocess.run(['launchctl',*args],capture_output=True,text=True,timeout=30)
    if check and r.returncode:
        raise RuntimeError('launchctl '+args[0]+' failed with code '+str(r.returncode))
    return r


def definition(task):
    config=check_config(task)
    logs=ROOT/'logs'/task
    return {'Label':config['label'],
      'ProgramArguments':['/usr/bin/caffeinate','-is',PYTHON,str(MONITOR/'runner.py'),
                          '--task-id',task,'--',PYTHON,str(ROOT/'run.py'),'--task',task],
      'WorkingDirectory':str(ROOT),'StartCalendarInterval':CALENDARS[task],
      'RunAtLoad':False,'StandardOutPath':str(logs/'task.log'),
      'StandardErrorPath':str(logs/'task.stderr.log'),
      'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/Users/yjl/.local/bin:/Users/yjl/Library/pnpm:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                              'TZ':'Asia/Shanghai','PYTHONUNBUFFERED':'1','SCHEDULED_TASK_ROOT':str(ROOT)},
      'ProcessType':'Background'}


def serialize_toml(data):
    """Known native automation fields are scalars, arrays and inline target tables."""
    def value(x):
        if isinstance(x,str):
            return json.dumps(x,ensure_ascii=False)
        if isinstance(x,bool):
            return 'true' if x else 'false'
        if isinstance(x,(int,float)):
            return str(x)
        if isinstance(x,list):
            return '['+', '.join(value(v) for v in x)+']'
        if isinstance(x,dict):
            return '{'+', '.join(k+' = '+value(v) for k,v in x.items())+'}'
        raise ValueError('Unexpected native automation field type')
    return '\n'.join(k+' = '+value(v) for k,v in data.items())+'\n'


def install():
    """No RunAtLoad: loading the calendar never runs a report as a test."""
    for task in TASK_IDS:
        config=check_config(task)
        data=definition(task)
        path=AGENTS/(config['label']+'.plist')
        if path.exists():
            previous=plistlib.loads(path.read_bytes())
            if str(ROOT/'run.py') not in previous.get('ProgramArguments',[]):
                raise RuntimeError('Label already belongs to another task')
            current=launch(['list',config['label']],False)
            if '"PID"' in current.stdout:
                raise RuntimeError('Task running; refusing scheduler replacement')
            launch(['bootout',f'gui/{os.getuid()}/'+config['label']],False)
        (ROOT/'logs'/task).mkdir(mode=0o700,parents=True,exist_ok=True)
        data_bytes=plistlib.dumps(data,sort_keys=False)
        temporary=path.with_suffix('.plist.tmp')
        temporary.write_bytes(data_bytes);temporary.chmod(0o600);temporary.replace(path)
        launch(['bootstrap',f'gui/{os.getuid()}',str(path)])
        print(json.dumps({'task':task,'loaded':True,'run_at_load':False}),flush=True)


def activate():
    """Pause old timers before enabling new workers; rollback on any partial cutover."""
    configs=read(ROOT/'tasks.json')
    originals={}
    stamp=str(time.time_ns())
    audit=ROOT/'migration'/stamp
    audit.mkdir(mode=0o700,parents=True)
    save(audit/'tasks-before.json',configs)
    for task in TASK_IDS:
        launch(['list',configs[task]['label']])
        path=Path.home()/'.codex/automations'/configs[task]['native_id']/'automation.toml'
        originals[path]=path.read_bytes()
        (audit/(configs[task]['native_id']+'.toml')).write_bytes(originals[path])
        (audit/(configs[task]['native_id']+'.toml')).chmod(0o600)
    try:
        for task in TASK_IDS:
            path=Path.home()/'.codex/automations'/configs[task]['native_id']/'automation.toml'
            data=tomllib.loads(originals[path].decode())
            data['status']='PAUSED'
            data['prompt']=('该任务已迁移到 Mac mini 统一调度；请勿启用本旧定时器。'
                            '当前入口：'+str(ROOT/'run.py')+' --task '+task+'\n\n'+
                            (ROOT/'prompts'/(task+'.txt')).read_text())
            data['updated_at']=int(time.time()*1000)
            text=serialize_toml(data)
            parsed=tomllib.loads(text)
            for key in data:
                if parsed[key] != data[key]:
                    raise RuntimeError('TOML roundtrip mismatch')
            temporary=path.with_name('automation.migration.tmp')
            temporary.write_text(text);temporary.chmod(0o600);temporary.replace(path)
        for task in TASK_IDS:
            configs[task]['enabled']=True
        save(ROOT/'tasks.json',configs)
        save(audit/'cutover.json',{'status':'active','at':time.time(),'tasks':list(TASK_IDS)})
        print(json.dumps({'status':'active','native_status':'PAUSED','tasks':list(TASK_IDS)}))
    except BaseException:
        for path,body in originals.items():
            temporary=path.with_name('automation.rollback.tmp')
            temporary.write_bytes(body);temporary.chmod(0o600);temporary.replace(path)
        previous=read(audit/'tasks-before.json')
        save(ROOT/'tasks.json',previous)
        raise


def verify():
    output=[]
    for task in TASK_IDS:
        c=check_config(task)
        path=AGENTS/(c['label']+'.plist')
        actual=plistlib.loads(path.read_bytes())
        native=tomllib.loads((Path.home()/'.codex/automations'/c['native_id']/'automation.toml').read_text())
        loaded=launch(['list',c['label']],False).returncode==0
        good=(actual==definition(task) and loaded and c.get('enabled') and native['status']=='PAUSED')
        output.append({'task':task,'loaded':loaded,'enabled':c.get('enabled'),
                       'native_status':native['status'],'valid':bool(good)})
    print(json.dumps(output,ensure_ascii=False))
    if not all(x['valid'] for x in output):
        raise RuntimeError('Cutover verification failed')


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['install','activate','verify'])
    args=p.parse_args()
    {'install':install,'activate':activate,'verify':verify}[args.action]()


if __name__=='__main__':
    main()
