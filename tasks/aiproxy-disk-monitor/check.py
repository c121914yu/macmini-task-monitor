#!/usr/bin/env python3
"""每日只读检查远端根分区；低于阈值才告警，同日发送凭据独立于采集。"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, '/Users/yjl/.local/share/webhook-delivery')
import webhook_delivery as delivery


def save(path, value):
    """原子保存私有状态，发送意图先于网络写入，防止异常退出后重复告警。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temp.chmod(0o600)
    temp.replace(path)


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def parse_df(text):
    """只接受根分区的 POSIX KiB 统计；格式异常不能被当成磁盘不足或零用量。"""
    lines = text.strip().splitlines()
    if len(lines) != 2:
        raise ValueError('Unexpected filesystem result')
    columns = lines[1].split()
    if len(columns) != 6 or columns[5] != '/':
        raise ValueError('Unexpected filesystem mount')
    total, used, available = map(int, columns[1:4])
    if total <= 0 or min(used, available) < 0 or max(used, available) > total:
        raise ValueError('Invalid filesystem capacity')
    return {'filesystem':columns[0], 'mount':'/', 'total_bytes':total*1024,
            'used_bytes':used*1024, 'available_bytes':available*1024}


def collect(config):
    """通过独立严格主机校验及现有密钥直连；物理网卡绑定绕过 Mini 的 TUN。"""
    args = ['/usr/bin/ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', '-o', 'UserKnownHostsFile='+str(ROOT/'known_hosts'),
            '-i', config['identity_file'], '-o', 'ProxyCommand=/usr/bin/nc -b '+config['interface']+' -G 10 %h %p',
            config['user']+'@'+config['host'], 'LC_ALL=C df -Pk /']
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('SSH disk query failed, exit='+str(result.returncode))
    return parse_df(result.stdout)


def notify(config, sample, day):
    """同日同根分区只告警一次；限流沿用原消息及累计预算，未知结果不自动重发。"""
    path = ROOT/'notifications'/(day+'.json')
    state = read(path)
    if state.get('status') in ('sent','sending','unknown','rejected','rate_limited'):
        return state['status']
    endpoint = hashlib.sha256(config['webhook'].encode()).hexdigest()
    if state and state.get('endpoint_sha256') != endpoint:
        raise RuntimeError('Pending notification endpoint changed')
    if not state:
        gb = lambda n: f'{n/1_000_000_000:.2f} GB'
        content = (f"**aiproxy-io 磁盘空间不足**\n\n分区：`/`\n"
                   f"剩余：**{gb(sample['available_bytes'])}**\n"
                   f"总容量：{gb(sample['total_bytes'])}\n"
                   f"告警阈值：{gb(config['threshold_bytes'])}\n检查时间：{sample['checked_at']}")
        state = {'status':'pending', 'endpoint_sha256':endpoint, 'attempts':[],
                 'payload':{'msg_type':'interactive','card':{'schema':'2.0',
                 'header':{'template':'orange','title':{'tag':'plain_text','content':'aiproxy-io 磁盘空间不足'}},
                 'body':{'elements':[{'tag':'markdown','content':content}]}}}}
        save(path,state)

    def progress(event):
        if state['attempts'] and state['attempts'][-1]['attempt']==event['attempt']:
            state['attempts'][-1].update(event)
        else:
            state['attempts'].append(event)
        state['status']=event['status']
        save(path,state)

    previous = state['attempts'][-1] if state['attempts'] else {}
    try:
        delivery.send(config['webhook'], state['payload'], on_attempt=progress,
                      initial_attempts=previous.get('attempt',0), not_before=previous.get('next_attempt_at'))
    except delivery.DeliveryError as error:
        state['status']=error.status
        save(path,state)
        raise RuntimeError('Disk alert delivery '+error.status) from None
    return 'sent'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only',action='store_true',help='只采集并记录，绝不发送通知')
    options=parser.parse_args()
    os.umask(0o077)
    with (ROOT/'task.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'event':'disk_check_already_running'}));return 0
        try:
            config=read(ROOT/'config.json')
            if type(config['threshold_bytes']) is not int or config['threshold_bytes']<=0:
                raise ValueError('Invalid threshold')
            now=datetime.now(ZoneInfo('Asia/Shanghai'))
            sample={**collect(config),'checked_at':now.isoformat(),'host':'aiproxy-io'}
            low=sample['available_bytes']<config['threshold_bytes']
            status='check_only' if options.check_only else ('healthy' if not low else notify(config,sample,now.date().isoformat()))
            if low and status in ('unknown','sending','rejected','rate_limited'):
                raise RuntimeError('Disk alert requires reconciliation: '+status)
            result={**sample,'threshold_bytes':config['threshold_bytes'],'low_space':low,'notification':status}
            save(ROOT/'latest.json',result)
            print(json.dumps({'event':'disk_check_completed',**result},ensure_ascii=False),flush=True)
            return 0
        except Exception as error:
            # 不输出 SSH 原始响应、配置、Webhook 或异常正文中的潜在认证信息。
            save(ROOT/'last-error.json',{'error_type':type(error).__name__,'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()})
            print(json.dumps({'event':'disk_check_failed','error_type':type(error).__name__}),flush=True)
            return 1


if __name__=='__main__':
    raise SystemExit(main())
