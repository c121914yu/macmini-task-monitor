#!/usr/bin/env python3
"""AI/命令行安装入口：备份 launchd 定义，空闲时接入记录层，并启动网页。"""

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import store
from config import AGENTS, DATA, ROOT, TASKS

PYTHON = '/opt/homebrew/bin/python3'
MONITOR_LABEL = 'com.yjl.macmini-task-monitor'


def launchctl(args, check=True):
    result = subprocess.run(['launchctl', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError('launchctl ' + args[0] + ' failed: ' + result.stderr[:500])
    return result


def atomic_write(path, data):
    """同目录原子替换，避免 launchd 或采集器读到半份 plist。"""
    temp = path.with_name(path.name + '.monitor-tmp')
    temp.write_bytes(data)
    os.chmod(temp, 0o600)
    temp.replace(path)


def bootstrap(path):
    """bootout 的进程清理可能异步完成；短暂的错误 5 重试，不误要求 root 权限。"""
    deadline = time.monotonic() + 15
    while True:
        result = launchctl(['bootstrap', f'gui/{os.getuid()}', str(path)], check=False)
        if result.returncode == 0:
            return
        if result.returncode != 5 or time.monotonic() >= deadline:
            raise RuntimeError('launchctl bootstrap failed: ' + result.stderr[:500])
        time.sleep(1)


def wrap_tasks():
    """仅包装四个已授权业务任务；运行中的任务等待结束，不终止已有审查。"""
    folder = DATA / 'launchd-backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    for task in TASKS:
        if task['source'] != 'launchd':
            continue
        path = AGENTS / (task['label'] + '.plist')
        original = path.read_bytes()
        definition = plistlib.loads(original)
        if str(ROOT / 'runner.py') in definition.get('ProgramArguments', []):
            print(task['id'] + ': already instrumented', flush=True)
            continue
        while True:
            state = launchctl(['list', task['label']], check=False)
            if '"PID"' not in state.stdout:
                break
            print(task['id'] + ': waiting for current execution to finish', flush=True)
            time.sleep(5)
        digest = hashlib.sha256(original).hexdigest()[:16]
        backup = folder / (task['label'] + '.' + digest + '.plist')
        if not backup.exists():
            backup.write_bytes(original)
            os.chmod(backup, 0o600)
        args = definition.get('ProgramArguments')
        if not args:
            raise RuntimeError('缺少原始 ProgramArguments：' + task['label'])
        definition['ProgramArguments'] = [PYTHON, str(ROOT / 'runner.py'), '--task-id', task['id'], '--', *args]
        was_loaded = state.returncode == 0
        atomic_write(path, plistlib.dumps(definition, sort_keys=False))
        try:
            if was_loaded:
                launchctl(['bootout', domain + '/' + task['label']])
                bootstrap(path)
        except Exception:
            # 调度器重载失败时立即恢复原入口，保持业务可运行。
            atomic_write(path, original)
            if was_loaded:
                bootstrap(path)
            raise
        store.set_meta('installation:' + task['id'], {'installed_at': time.time(), 'original': str(backup)})
        print(task['id'] + ': instrumented; original saved', flush=True)


def install_service(options):
    """监控进程常驻；网页服务的重启不影响独立运行的业务记录入口。"""
    args = [PYTHON, str(ROOT / 'server.py'), '--port', str(options.port)]
    for address in options.bind or ['127.0.0.1']:
        args.extend(['--bind', address])
    for host in options.allowed_host:
        args.extend(['--allowed-host', host])
    path = AGENTS / (MONITOR_LABEL + '.plist')
    definition = {'Label': MONITOR_LABEL, 'ProgramArguments': args, 'WorkingDirectory': str(ROOT),
                  'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
                  'StandardOutPath': str(DATA / 'server.log'), 'StandardErrorPath': str(DATA / 'server.stderr.log'),
                  'EnvironmentVariables': {'TZ': 'Asia/Shanghai', 'PYTHONUNBUFFERED': '1', 'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
    atomic_write(path, plistlib.dumps(definition, sort_keys=False))
    domain = f'gui/{os.getuid()}'
    launchctl(['bootout', domain + '/' + MONITOR_LABEL], check=False)
    bootstrap(path)
    store.set_meta('server_configuration', {'bind': options.bind or ['127.0.0.1'], 'port': options.port})
    print('monitor: installed', flush=True)


def restore_tasks():
    """只恢复仍使用本记录入口的 plist，避免覆盖后来由用户或 AI 修改的任务。"""
    domain = f'gui/{os.getuid()}'
    for task in TASKS:
        receipt = store.get_meta('installation:' + task['id'])
        if not receipt:
            continue
        path = AGENTS / (task['label'] + '.plist')
        definition = plistlib.loads(path.read_bytes())
        if str(ROOT / 'runner.py') not in definition.get('ProgramArguments', []):
            continue
        current = launchctl(['list', task['label']], check=False)
        if '"PID"' in current.stdout:
            raise RuntimeError('任务正在执行，请在结束后恢复：' + task['id'])
        atomic_write(path, Path(receipt['original']).read_bytes())
        if current.returncode == 0:
            launchctl(['bootout', domain + '/' + task['label']])
            bootstrap(path)
        print(task['id'] + ': original restored', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', action='append')
    parser.add_argument('--allowed-host', action='append', default=[])
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--restore-tasks', action='store_true')
    options = parser.parse_args()
    store.initialize()
    if options.restore_tasks:
        restore_tasks()
    else:
        wrap_tasks()
        install_service(options)
