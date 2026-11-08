"""读取 launchd 与现有凭据，逐项隔离故障；从不触发被监控任务。"""

import copy
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import socket
import subprocess
import time
import tomllib
from pathlib import Path

import store
from config import AGENTS, BACKUP_ROOT, METASO_ROOT, PR_ROOT, ROOT, SERVICES, TASKS, ZONE, redact, timestamp
from outcomes import scan_result_from_log


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=8, env={**os.environ, 'LC_ALL': 'C'})


def gui_states():
    """launchctl list 的 PID 为 '-' 表示等待，不把已注册的定时任务误判为持续运行。"""
    result = command(['launchctl', 'list'])
    if result.returncode:
        raise RuntimeError('无法读取当前用户的 launchd 状态')
    states = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3:
            pid, exit_code, label = parts
            states[label] = {'loaded': True, 'pid': int(pid) if pid.isdigit() else None,
                             'last_exit_code': int(exit_code) if re.fullmatch(r'-?\d+', exit_code) else None}
    return states


def definitions():
    """注册常用任务，并发现后续由 AI 添加的自建 launchd 定时任务。"""
    tasks = copy.deepcopy(TASKS)
    known = {task['label'] for task in tasks}
    for path in sorted(AGENTS.glob('*.plist')):
        try:
            data = plistlib.loads(path.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if not isinstance(data, dict):
            continue
        label = data.get('Label', '')
        if label in known or not (data.get('StartInterval') or data.get('StartCalendarInterval')):
            continue
        if not (label.startswith('com.yjl.') or label == 'com.google.GoogleUpdater.wake'):
            continue
        scripts = [part for part in data.get('ProgramArguments', []) if part.startswith('/') and re.search(r'\.(py|sh|js|mjs|ts)$', part)]
        tasks.append({'id': label, 'label': label, 'name': 'Google 自动更新' if label.startswith('com.google.') else label,
                      'icon': 'clock', 'source': 'launchd', 'category': 'system' if label.startswith('com.google.') else 'work',
                      'description': '自动发现的 launchd 定时任务。', 'script': scripts[-1] if scripts else data.get('Program', ''),
                      'log_paths': [data[key] for key in ('StandardOutPath', 'StandardErrorPath') if data.get(key)]})
    return tasks


def import_record(data):
    """稳定外部键用于重复采集去重；没有依据的开始时间和耗时保持为空。"""
    key = data['external_key']
    store.insert_run({'id': 'h-' + hashlib.sha256(key.encode()).hexdigest()[:24], 'kind': 'history', **data})


def needs_receipt(task_id, event_time):
    """无实时执行覆盖时补入业务凭据；扫描器补发日报也可留痕，避免同次执行双计。"""
    with store.connect() as db:
        row = db.execute("SELECT 1 FROM runs WHERE task_id=? AND kind='execution' AND started_at<=? AND (finished_at>=? OR (status='running' AND heartbeat_at>=?)) LIMIT 1",
                         (task_id, event_time, event_time, event_time - 30)).fetchone()
    return row is None


def backup_data():
    records = []
    backup_root = Path('/Volumes/code/backups/fastgpt')
    if not backup_root.is_dir():
        raise RuntimeError('备份目录不可用：' + str(backup_root))
    for path in sorted(backup_root.glob('*/manifest.json')):
        data = read_json(path)
        if data.get('owner') != 'fastgpt-daily-backup-v1' or data.get('status') != 'complete':
            continue
        start, end = timestamp(data.get('started_at')), timestamp(data.get('completed_at'))
        size = sum(item.get('bytes', 0) for item in data.get('files', []))
        detail = {'备份日期': data.get('date'), '文件数量': len(data.get('files', [])), '总字节数': size,
                  '备份目录': str(path.parent), '保留天数': 7, '时间精度': '原始清单的开始与结束时间'}
        records.append({'date': data.get('date'), 'bytes': size, 'file_count': len(data.get('files', [])), 'completed_at': end, 'path': str(path.parent)})
        if end and needs_receipt('fastgpt-backup', end):
            import_record({'task_id': 'fastgpt-backup', 'external_key': 'backup:' + str(path), 'status': 'success',
                           'created_at': start or end, 'started_at': start, 'finished_at': end, 'exit_code': None,
                           'summary': 'MongoDB、PostgreSQL、MinIO 备份完成，校验通过', 'details': detail})
    return {'backups': records[-7:], 'latest_backup': records[-1] if records else None}


def scan_log_tail():
    """有限读取扫描器原日志，为只有编号的旧凭据补充可核实的队列结果。"""
    path = PR_ROOT / 'pr-poll.log'
    if not path.is_file():
        return ''
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 48000))
        return stream.read(48000).decode('utf-8', errors='replace')


def pr_data():
    data = read_json(PR_ROOT / 'queue-state.json')
    counts = {}
    active = []
    for number, entry in data.get('entries', {}).items():
        status = entry.get('status', 'unknown')
        counts[status] = counts.get(status, 0) + 1
        if status in ('queued', 'running', 'error'):
            active.append({'pr': number, 'status': status, 'phase': entry.get('phase'), 'ready_at': entry.get('ready_at'), 'attempts': entry.get('attempts')})
        end = entry.get('finished_at')
        if end and status in ('processed', 'error') and needs_receipt('pr-worker', end):
            import_record({'task_id': 'pr-worker', 'external_key': f'pr:{number}:{entry.get("attempt_id", end)}',
                           'status': 'success' if status == 'processed' else 'failed',
                           'created_at': entry.get('started_at') or end, 'started_at': entry.get('started_at'),
                           'finished_at': end, 'exit_code': entry.get('exit_code'),
                           'summary': f'PR #{number} · ' + ('已处理' if status == 'processed' else '处理异常'),
                           'details': {'PR': number, '通道': entry.get('route'), '尝试次数': entry.get('attempts'),
                                       '审查地址': entry.get('review_url'), '时间精度': '业务队列记录；不是调度器的完整执行历史'}})
    last_scan = data.get('last_scan_at')
    if last_scan and needs_receipt('pr-scan', last_scan):
        result = scan_result_from_log(scan_log_tail(), data.get('scan_id'))
        import_record({'task_id': 'pr-scan', 'external_key': f'scan:{data.get("scan_id")}', 'status': result[0] if result else 'success',
                       'created_at': last_scan, 'finished_at': last_scan, 'summary': result[1] if result else f'扫描 #{data.get("scan_id")} · 已读取最新 PR 队列',
                       'details': {'时间精度': '只有扫描记录时间，开始时间与耗时未知'}})
    return {'last_scan_at': last_scan, 'scan_id': data.get('scan_id'), 'queue_counts': counts,
            'queue_items': active, 'scheduling': data.get('scheduling', {})}


def report_data():
    latest = None
    for path in sorted((PR_ROOT / 'daily-reports').glob('*/notification.json')):
        receipt = read_json(path)
        data = read_json(path.parent / 'data.json')
        rows = data.get('rows', [])
        sent = receipt.get('sent_at')
        record_time = sent or data.get('generated_at')
        if not record_time:
            continue
        reported = [row['total_tokens'] for row in rows if isinstance(row.get('total_tokens'), (int, float))]
        info = {'date': path.parent.name, 'sent_at': sent, 'status': receipt.get('status'), 'pr_count': len(rows),
                'total_tokens': sum(reported) if reported else None, 'partial_usage': any(row.get('partial_usage') for row in rows),
                'rows': rows, 'path': str(path.parent)}
        latest = info
        if needs_receipt('pr-report', record_time):
            status = {'sent': 'success', 'failed': 'failed'}.get(receipt.get('status'), 'unknown')
            import_record({'task_id': 'pr-report', 'external_key': 'report:' + path.parent.name,
                           'created_at': record_time, 'finished_at': sent, 'status': status,
                           'summary': f'{path.parent.name} 日报 · {len(rows)} 个 PR · ' + ('已发送' if status == 'success' else '发送状态待确认'),
                           'details': {'统计日期': path.parent.name, 'PR 数量': len(rows), '累计 Token': info['total_tokens'],
                                       '部分用量缺失': info['partial_usage'], '时间精度': '发送凭据时间；开始时间与耗时未知'}})
    return {'latest_report': latest}


def metaso_data():
    """持续接入现有 Codex 记忆记录；不调用 API、不执行脚本、不发送通知。"""
    memory = METASO_ROOT / 'memory.md'
    text = memory.read_text() if memory.exists() else ''
    records = []
    pattern = r'- 运行时间:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\n]*\n- 结果:\s*([^\n]+)'
    for date, summary in re.findall(pattern, text):
        observed = dt.datetime.strptime(date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZONE).timestamp()
        balance_match = re.search(r'余额\s*([\d,]+)\s*点', summary)
        balance = int(balance_match[1].replace(',', '')) if balance_match else None
        status = 'success' if re.search(r'退出码\s*0', summary) else 'failed' if re.search(r'失败|退出码\s*[1-9]', summary) else 'unknown'
        info = {'observed_at': observed, 'balance': balance, 'summary': summary, 'status': status}
        records.append(info)
        import_record({'task_id': 'metaso-balance', 'external_key': 'metaso:' + date, 'status': status,
                       'created_at': observed, 'finished_at': observed, 'summary': summary,
                       'details': {'剩余积分': balance, '预警阈值': 1000, '时间精度': 'Codex 留存记录时间；开始时间与耗时未知'}})
    return {'balance': records[-1]['balance'] if records else None, 'threshold': 1000, 'balance_history': records[-30:]}


def automation_calendar(rule):
    """把现有每日/每周 Codex 规则转换为展示用日历；不认识的形式保持未知。"""
    values = dict(part.split('=', 1) for part in rule.removeprefix('RRULE:').split(';') if '=' in part)
    if set(values) - {'FREQ', 'INTERVAL', 'BYDAY', 'BYHOUR', 'BYMINUTE'} or values.get('INTERVAL', '1') != '1':
        return None
    if values.get('FREQ') not in ('DAILY', 'WEEKLY') or not values.get('BYHOUR'):
        return None
    mapping = {'SU': 0, 'MO': 1, 'TU': 2, 'WE': 3, 'TH': 4, 'FR': 5, 'SA': 6}
    days = values.get('BYDAY', '').split(',') if values.get('BYDAY') else []
    if days and not set(days).issubset(mapping):
        return None
    if values['FREQ'] == 'WEEKLY' and not days:
        return None
    weekdays = [None] if not days or len(set(days)) == 7 else [mapping[day] for day in days]
    result = []
    for hour in map(int, values['BYHOUR'].split(',')):
        for minute in map(int, values.get('BYMINUTE', '0').split(',')):
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                return None
            for day in weekdays:
                result.append({'Hour': hour, 'Minute': minute, **({'Weekday': day} if day is not None else {})})
    return result


def schedule_info(task, definition, now):
    """日历任务给出下一计划时间；间隔任务标注估计，避免声称掌握 launchd 内部计时器。"""
    interval = definition.get('StartInterval')
    calendar = definition.get('StartCalendarInterval')
    if interval:
        task['schedule'] = {'type': 'interval', 'seconds': interval, 'text': f'每 {interval // 60} 分钟' if interval % 60 == 0 else f'每 {interval} 秒'}
        if task['id'] == 'pr-worker':
            task['schedule']['text'] += '兜底，入队时唤起'
        reference = (task.get('last_run') or {}).get('started_at') or (task.get('data') or {}).get('last_scan_at')
        task['next_at'] = reference + (int((now - reference) // interval) + 1) * interval if reference and not task.get('pid') else None
        task['next_estimated'] = True
    elif calendar:
        calendars = calendar if isinstance(calendar, list) else [calendar]
        candidates = []
        previous = []
        labels = []
        for rule in calendars:
            local = dt.datetime.fromtimestamp(now, ZONE)
            # 只计算能明确解释的规则，复杂月历不伪造下一执行时间。
            if set(rule) == {'Minute'} and 0 <= rule['Minute'] <= 59:
                next_date = local.replace(minute=rule['Minute'], second=0, microsecond=0)
                if next_date.timestamp() <= now:
                    next_date += dt.timedelta(hours=1)
                candidates.append(next_date.timestamp())
                previous.append((next_date - dt.timedelta(hours=1)).timestamp())
                labels.append('每小时整点' if rule['Minute'] == 0 else f"每小时 {rule['Minute']:02d} 分")
            elif set(rule).issubset({'Hour', 'Minute', 'Weekday'}) and 'Hour' in rule:
                next_date = local.replace(hour=rule['Hour'], minute=rule.get('Minute', 0), second=0, microsecond=0)
                weekday = rule.get('Weekday')
                period = 7 if weekday is not None else 1
                if weekday is not None:
                    next_date += dt.timedelta(days=(weekday % 7 - (local.weekday() + 1) % 7) % 7)
                if next_date.timestamp() <= now:
                    next_date += dt.timedelta(days=period)
                candidates.append(next_date.timestamp())
                previous.append((next_date - dt.timedelta(days=period)).timestamp())
                prefix = f'每周{"日一二三四五六"[weekday % 7]}' if weekday is not None else '每天'
                labels.append(f'{prefix} {rule["Hour"]:02d}:{rule.get("Minute", 0):02d}')
        task['schedule'] = {'type': 'calendar', 'rules': calendars,
                            'text': '、'.join(labels) if candidates and len(candidates) == len(calendars) else '日历规则：' + json.dumps(calendar, ensure_ascii=False)}
        task['next_at'] = min(candidates) if candidates else None
        task['previous_at'] = max(previous) if previous else None
        task['next_estimated'] = False
    else:
        task['schedule'] = {'type': 'unknown', 'text': '未识别执行规则'}
        task['next_at'] = None
    return task


def recover_orphans():
    """仅当记录进程已不存在时结算为中断；空闲执行器的旧业务心跳不用于故障判断。"""
    with store.connect() as db:
        rows = db.execute("SELECT id,pid,process_start,started_at FROM runs WHERE status='running'").fetchall()
    for row in rows:
        if time.time() - row['started_at'] < 30:
            continue
        result = command(['ps', '-p', str(row['pid']), '-o', 'lstart='])
        if result.returncode or (row['process_start'] and result.stdout.strip() != row['process_start']):
            store.update_run(row['id'], status='interrupted', summary='记录进程已退出，未收到正常结束凭据；实际结束时间未知')


def normalize_empty_scans():
    """修正有明确空队列日志的成功扫描，保留失败/中断/未知记录；兼容部署前已启动的入口。"""
    key = 'empty_scan_classification_v1'
    previous = store.get_meta(key)
    # 首次修正全部历史；后续只检查一天内的旧入口结果，不反复扫描全量账本。
    since = time.time() - 86400 if previous and previous.get('history_normalized') else 0
    with store.connect() as db:
        rows = db.execute("SELECT id,kind,external_key,log_tail FROM runs WHERE task_id='pr-scan' AND status='success' AND ((kind='execution' AND exit_code=0) OR (kind='history' AND external_key LIKE 'scan:%')) AND created_at>=?", (since,)).fetchall()
    updates = []
    original_log = None
    for row in rows:
        if row['kind'] == 'history':
            if original_log is None:
                original_log = scan_log_tail()
            result = scan_result_from_log(original_log, row['external_key'].removeprefix('scan:'))
        else:
            result = scan_result_from_log(row['log_tail'])
        if result is not None and result[0] == 'skipped':
            updates.append((row['id'], result[1]))
    if updates:
        store.backup_database('monitor-before-empty-scan-v1.sqlite3')
        with store.connect() as db:
            # 条件重检，防止旧进程或另一采集轮次已经改变同一记录的结果。
            db.executemany("UPDATE runs SET status='skipped',summary=? WHERE id=? AND status='success' AND (exit_code=0 OR (kind='history' AND external_key LIKE 'scan:%'))", [(summary, run_id) for run_id, summary in updates])
    if updates or previous is None or not previous.get('history_normalized'):
        store.set_meta(key, {'applied_at': (previous or {}).get('applied_at', time.time()),
                             'reclassified': (previous or {}).get('reclassified', 0) + len(updates), 'history_normalized': True})
    return len(updates)


def collect():
    """每轮保存完整快照和采集错误；单一业务文件损坏不会停止其他任务的监控。"""
    now = time.time()
    store.prune_runs(now)
    normalize_empty_scans()
    errors = []
    try:
        states = gui_states()
        launchd_available = True
    except Exception as error:
        states, launchd_available = {}, False
        errors.append(str(error))
    sources = {}
    for key, reader in [('backup', backup_data), ('pr', pr_data),
                        ('report', report_data), ('metaso', metaso_data)]:
        try:
            sources[key] = reader()
        except Exception as error:
            sources[key] = {'source_error': redact(str(error))}
            errors.append(f'{key}: {redact(error)}')

    task_definitions = definitions()
    try:
        store.reconcile_discovered_tasks(task_definitions)
    except Exception as error:
        errors.append('任务别名合并失败：' + redact(error))
    for task in task_definitions:
        definition = {}
        if task.get('label'):
            try:
                definition = plistlib.loads((AGENTS / (task['label'] + '.plist')).read_bytes())
            except Exception as error:
                errors.append(task['id'] + ': ' + redact(error))
            task.update(states.get(task['label'], {'loaded': False, 'pid': None, 'last_exit_code': None}))
        else:
            try:
                automation = tomllib.loads((METASO_ROOT / 'automation.toml').read_text())
                task.update({'loaded': automation.get('status') == 'ACTIVE', 'pid': None, 'last_exit_code': None})
                definition = {'StartCalendarInterval': automation_calendar(automation.get('rrule', ''))}
            except Exception as error:
                task.update({'loaded': False, 'pid': None, 'last_exit_code': None})
                sources['metaso']['source_error'] = '调度配置无法读取：' + redact(error)
                errors.append(sources['metaso']['source_error'])
        task['definition_path'] = str(AGENTS / (task['label'] + '.plist')) if task.get('label') else str(METASO_ROOT / 'automation.toml')
        task['instrumented'] = str(ROOT / 'runner.py') in definition.get('ProgramArguments', [])
        task['run_at_load'] = bool(definition.get('RunAtLoad'))
        task['log_paths'] = task.get('log_paths', [str(Path(task.get('log_root', '.')) / name) for name in task.get('logs', [])])
        task.pop('log_root', None)
        task.pop('logs', None)
        source_key = {'fastgpt-backup': 'backup', 'pr-scan': 'pr', 'pr-worker': 'pr', 'pr-report': 'report', 'metaso-balance': 'metaso'}.get(task['id'])
        task['data'] = sources.get(source_key, {})
        with store.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task['id'],)).fetchone()
        task['last_run'] = store.decode_run(row)
        if task['last_run']:
            task['last_run'].pop('log_tail', None)
        last = task['last_run'] or {}
        task['state'] = 'running' if task['pid'] or last.get('status') == 'running' else 'ready'
        if not launchd_available and task.get('label'):
            task['state'] = 'unknown'
        elif not task['loaded']:
            task['state'] = 'disabled'
        elif task['state'] != 'running':
            if last.get('status') == 'partial':
                task['state'] = 'partial'
            elif last.get('status') in ('failed', 'interrupted', 'unknown'):
                task['state'] = 'attention'
            elif task.get('last_exit_code') not in (None, 0) and not last.get('details',{}).get('outcome_reconciled'):
                task['state'] = 'attention'
        if task['data'].get('source_error'):
            task['state'] = 'unknown'
        schedule_info(task, definition, now)
        if not task['loaded']:
            task['next_at'] = None
        # 错过每日任务超过 30 分钟才提示；原始记录缺失与任务执行失败分别展示。
        if task['schedule']['type'] == 'calendar' and task.get('next_at') and task['state'] == 'ready':
            previous = task.get('previous_at')
            if previous is not None and now > previous + 1800 and last.get('created_at', 0) < previous:
                task['state'] = 'overdue'
        if task['instrumented'] and task['schedule']['type'] == 'interval' and task['state'] == 'ready':
            installation = store.get_meta('installation:' + task['id'], {})
            # 未产生新记录不等于执行失败，因此使用“记录逾期”；给调度抖动保留两个周期。
            reference = max(installation.get('installed_at', 0), last.get('finished_at') or last.get('created_at', 0))
            if reference and now - reference > task['schedule']['seconds'] * 2 + 60:
                task['state'] = 'overdue'
        task['updated_at'] = now
        store.save_task(task)

    services = []
    for service in copy.deepcopy(SERVICES):
        try:
            if service['domain'] == 'gui':
                service.update(states.get(service['label'], {'loaded': False, 'pid': None, 'last_exit_code': None}))
            else:
                result = command(['launchctl', 'print', 'system/' + service['label']])
                pid = re.search(r'^\tpid = (\d+)$', result.stdout, re.M)
                code = re.search(r'^\tlast exit code = (-?\d+)$', result.stdout, re.M)
                service.update({'loaded': result.returncode == 0, 'pid': int(pid[1]) if pid else None, 'last_exit_code': int(code[1]) if code else None})
            service['state'] = 'running' if service['pid'] else 'attention' if service.get('last_exit_code') not in (None, 0) else 'stopped'
        except Exception:
            service['state'] = 'unknown'
        services.append(service)
    store.set_meta('collector', {'checked_at': now, 'errors': errors, 'hostname': socket.gethostname(), 'services': services})
    recover_orphans()


def loop(stop):
    """采集线程的异常只记入自身日志；后台持续重试，网页仍可读取上次成功快照。"""
    while not stop.is_set():
        try:
            collect()
        except Exception as error:
            print('collector_error:', redact(error), flush=True)
        stop.wait(15)
