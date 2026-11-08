#!/opt/homebrew/bin/python3
"""Keep the Mac mini 01:00 schedule; backup payloads are created only on Ubuntu Dev."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
HOST='archer-unbuntu26-dev'
REMOTE='/home/archer/.local/share/fastgpt-backup'
SSH=['/usr/bin/ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST]


def refresh_summary():
    """Only non-secret manifest metadata is cached for the read-only dashboard."""
    result=subprocess.run([*SSH,'/usr/bin/python3 '+REMOTE+'/verify_remote.py'],
                          capture_output=True,text=True,check=True,timeout=60)
    data=json.loads(result.stdout)
    if data.get('storage_root')!='/home/archer/backups/fastgpt':
        raise RuntimeError('Unexpected remote backup root')
    tmp=ROOT/'remote-summary.tmp'
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.chmod(0o600)
    tmp.replace(ROOT/'remote-summary.json')
    return data


def main():
    os.umask(0o077)
    with (ROOT/'backup.lock').open('a')as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有备份任务运行，本次跳过',flush=True)
            return
        result=subprocess.run([*SSH,'/usr/bin/python3 '+REMOTE+'/run_remote.py'],check=False)
        try:data=refresh_summary()
        except Exception:
            if result.returncode:raise RuntimeError('Ubuntu backup failed; previous remote backups retained')from None
            raise
        if result.returncode:raise RuntimeError('Ubuntu backup failed; previous remote backups retained')
        latest=data['backups'][-1]if data['backups']else None
        if not latest or latest.get('date')!=dt.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
            raise RuntimeError('No completed backup for today on Ubuntu')
        print('备份成功：'+latest['path'],flush=True)


if __name__=='__main__':
    main()
