#!/usr/bin/env python3
"""launchd 的透明记录入口：保留原命令、工作目录、环境和 stdout/stderr。"""

import argparse
import codecs
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import uuid

import store
from config import redact
from outcomes import scan_result, docs_result


def execute(options):
    """旁路持久化任务生命周期；监控数据库失败时仍执行原任务，并透传退出码。"""
    run_id = uuid.uuid4().hex
    started_at = time.time()
    recording = True
    tail = ''
    process = None
    received_signal = None
    business_results = {}
    latest_scan = None
    latest_docs = None

    def safe_record(callback):
        nonlocal recording
        if not recording:
            return
        try:
            callback()
        except Exception as error:
            recording = False
            print('[task-monitor] 记录失败，原任务继续执行：' + redact(error), file=sys.stderr, flush=True)

    def forward(signum, _frame):
        nonlocal received_signal
        received_signal = signum
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass

    safe_record(store.initialize)
    birth = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'lstart='], capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'}).stdout.strip()
    safe_record(lambda: store.insert_run({'id': run_id, 'task_id': options.task_id, 'external_key': 'execution:' + run_id,
                                         'kind': 'execution', 'status': 'running', 'created_at': started_at,
                                         'started_at': started_at, 'heartbeat_at': started_at, 'pid': os.getpid(),
                                         'process_start': birth, 'summary': '正在执行', 'details': {'时间精度': '监控入口实时记录'}}))
    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGHUP, forward)

    # 子任务独立进程组，收到停止信号时传给整棵业务进程树，避免审查进程孤悬。
    try:
        process = subprocess.Popen(options.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except Exception as error:
        safe_record(lambda: store.update_run(run_id, status='failed', finished_at=time.time(), exit_code=127, summary='原命令启动失败：' + redact(error)))
        print('[task-monitor] 原命令启动失败：' + redact(error), file=sys.stderr, flush=True)
        return 127
    if received_signal:
        forward(received_signal, None)

    selector = selectors.DefaultSelector()
    buffers = {}
    decoders = {}
    for pipe, output in [(process.stdout, sys.stdout.buffer), (process.stderr, sys.stderr.buffer)]:
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, output)
        buffers[pipe] = ''
        decoders[pipe] = codecs.getincrementaldecoder('utf-8')('replace')

    def append_text(pipe, text, final=False):
        nonlocal tail, latest_scan, latest_docs
        buffers[pipe] += text
        lines = buffers[pipe].splitlines(keepends=True)
        buffers[pipe] = ''
        if lines and not final and not lines[-1].endswith(('\n', '\r')):
            buffers[pipe] = lines.pop()
        for line in lines:
            tail = (tail + redact(line))[-24000:]
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('event') == 'worker_finished':
                    business_results[str(event.get('pr'))] = event
                elif docs_result(event) is not None:
                    latest_docs = event
                elif isinstance(event, dict) and event.get('event') == 'scan':
                    latest_scan = event
            except (ValueError, TypeError):
                pass
        # 限制异常的无换行输出；只在记录副本里截断，不影响原标准输出。
        if len(buffers[pipe]) > 65536:
            tail = (tail + redact(buffers[pipe])[-24000:])[-24000:]
            buffers[pipe] = ''

    heartbeat = time.monotonic()
    while selector.get_map():
        for key, _events in selector.select(timeout=1):
            try:
                chunk = os.read(key.fileobj.fileno(), 65536)
            except BlockingIOError:
                continue
            if chunk:
                try:
                    key.data.write(chunk)
                    key.data.flush()
                except (BrokenPipeError, OSError):
                    pass
                append_text(key.fileobj, decoders[key.fileobj].decode(chunk))
            else:
                append_text(key.fileobj, decoders[key.fileobj].decode(b'', final=True), final=True)
                selector.unregister(key.fileobj)
                key.fileobj.close()
        if time.monotonic() - heartbeat >= 5:
            safe_record(lambda: store.update_run(run_id, heartbeat_at=time.time(), log_tail=tail))
            heartbeat = time.monotonic()
    selector.close()
    code = process.wait()
    status = 'success' if code == 0 else 'interrupted' if code < 0 or received_signal else 'failed'
    summary = '执行成功' if code == 0 else f'执行失败，退出码 {code}'
    if status == 'interrupted':
        summary = f'执行被信号 {received_signal or -code} 中断'
    elif code == 0:
        for line in reversed(tail.splitlines()):
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict) and event.get('event'):
                name = event['event']
                if name in ('worker_idle', 'poller_already_running', 'daily_report_in_progress', 'daily_report_already_sent_or_uncertain'):
                    status = 'skipped'
                    summary = {'worker_idle': '队列空闲，本次无需执行', 'poller_already_running': '扫描器已在运行，本次跳过',
                               'daily_report_in_progress': '日报正在生成，本次跳过', 'daily_report_already_sent_or_uncertain': '已有发送凭据，本次未重复发送'}[name]
                elif name == 'scan':
                    result = scan_result(event)
                    summary = result[1] if result is not None else f'扫描 #{event.get("scan_id")} 完成，待执行队列未知'
                elif name in ('flowercloud_finished', 'flowercloud_skipped'):
                    summary = event.get('summary') or 'FlowerCloud 流量检查结束'
                    if name == 'flowercloud_skipped':
                        status = 'skipped'

                elif name == 'scheduled_task':
                    summary = event.get('summary') or '定时任务结束'
                    status = {'success':'success', 'skipped':'skipped', 'partial':'partial'}.get(event.get('status'), 'failed')
                elif name == 'notification_sent':
                    summary = f'日报已发送 · {event.get("prs", "—")} 个 PR'
                elif name == 'worker_finished':
                    summary = f'PR #{event.get("pr")} 执行结束 · {event.get("status")}'
                break
        if '今日备份目录已存在' in tail or '已有备份任务运行' in tail:
            status, summary = 'skipped', '备份已存在或已有任务运行，本次跳过'
        elif '备份成功：' in tail:
            summary = 'MongoDB、PostgreSQL、RustFS 备份完成，校验通过'
        if options.task_id == 'pr-scan':
            result = scan_result(latest_scan)
            if result is not None:
                status, summary = result
        if options.task_id == 'release-docs':
            result = docs_result(latest_docs)
            if result is not None:
                status, summary = result
        # 执行器处理完任务后同样会输出 worker_idle，不能把实际完成的审查归为“空闲跳过”。
        if business_results:
            failures = [item for item in business_results.values() if item.get('code') not in (None, 0) or item.get('status') == 'error']
            status = 'failed' if failures else 'success'
            summary = f'已处理 {len(business_results)} 个 PR' + (f'，其中 {len(failures)} 个异常' if failures else '')

    # 迁移任务的失败阶段同样显示业务摘要；不能被最后一条普通日志遮盖。
    if options.task_id in ('mail-digest', 'signoz-report', 'plugin-model-sync', 'pr-weekly-report'):
        for line in reversed(tail.splitlines()):
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict) and event.get('event') == 'scheduled_task':
                summary = event.get('summary') or summary
                if code == 0 and event.get('status') == 'skipped':
                    status = 'skipped'
                break
    safe_record(lambda: store.update_run(run_id, status=status, finished_at=time.time(), heartbeat_at=time.time(),
                                        exit_code=code, summary=summary, log_tail=tail))
    return 128 - code if code < 0 else code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if options.command[:1] == ['--']:
        options.command = options.command[1:]
    if not options.command:
        parser.error('必须提供原始命令')
    sys.exit(execute(options))


if __name__ == '__main__':
    main()
