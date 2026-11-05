#!/usr/bin/env python3
"""每小时读取真实执行记录；持续故障才派给 Codex，健康检查不调用模型。"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
MONITOR = Path('/Users/yjl/.local/share/macmini-task-monitor')
DB = MONITOR/'data/monitor.sqlite3'
SELF_ID = 'task-health-watchdog'
sys.path.insert(0, str(MONITOR))
from config import redact

DEFAULTS = {'minimum_failures': 3, 'persistent_seconds': 3600,
            'retry_seconds': 3600, 'max_repair_rounds': 3,
            'max_repairs_per_scan': 1, 'max_snapshot_age': 180}

def read(path, fallback=None):
    return json.loads(path.read_text()) if path.exists() else (fallback if fallback is not None else {})

def save(path, value):
    """持检查锁时原子保存事件、会话与派单意图，重启不丢去重信息。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temp.chmod(0o600)
    temp.replace(path)

def emit(event, **fields):
    print(json.dumps({'event': event, **fields}, ensure_ascii=False), flush=True)

def snapshot(db_path=DB):
    """只读事务同时获取任务快照和真实 execution 记录，不混入历史推断。"""
    db = sqlite3.connect('file:'+str(db_path)+'?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        tasks = [json.loads(row['snapshot']) for row in db.execute('SELECT snapshot FROM tasks')]
        runs = {}
        for task in tasks:
            runs[task['id']] = [dict(row) for row in db.execute(
                "SELECT id,status,created_at,started_at,finished_at,heartbeat_at,summary,log_tail "
                "FROM runs WHERE task_id=? AND kind='execution' AND status<>'skipped' "
                "ORDER BY created_at DESC,id DESC LIMIT 128", (task['id'],))]
        return tasks, runs
    finally:
        db.close()

def signal(task, runs, now, policy):
    """业务暂停优先；运行中的正常重试及已成功恢复的旧 exit code 不算失败。"""
    data = task.get('data') or {}
    storage = data.get('storage') or {}
    if task['id'] == 'pr-worker' and storage.get('paused'):
        return {'kind':'business_paused', 'reason':redact(storage.get('pause_reason') or '审查领取暂停'),
                'anchor':None}
    latest = runs[0] if runs else None
    if latest and latest['status'] == 'running':
        heartbeat = latest.get('heartbeat_at') or latest.get('started_at')
        if heartbeat and now-heartbeat > 600:
            return {'kind':'stale_heartbeat', 'reason':'执行心跳超过 10 分钟未更新',
                    'anchor':latest['id']}
        return None
    failures = []
    for run in runs:
        if run['status'] not in ('failed','interrupted'):
            break
        failures.append(run)
    if len(failures) >= policy['minimum_failures']:
        since = failures[-1].get('started_at') or failures[-1]['created_at']
        if now-since >= policy['persistent_seconds']:
            return {'kind':'execution_failures', 'reason':f'连续 {len(failures)} 次执行失败',
                    'anchor':failures[-1]['id'], 'since':since}
    if task.get('state') == 'overdue':
        # 新迁移的日历任务在首次应执行时间之前没有 execution，不能沿用安装前的计划误报。
        installed = 0
        definition = task.get('definition_path')
        if definition:
            try:
                installed = Path(definition).stat().st_mtime
            except OSError:
                return None
        previous = task.get('previous_at')
        if previous and previous < installed:
            return None
        if not latest and not previous:
            interval = (task.get('schedule') or {}).get('seconds')
            if not installed or not interval or now-installed < interval*2+policy['persistent_seconds']:
                return None
        return {'kind':'overdue', 'reason':'监控确认执行记录逾期', 'anchor':None}
    return None

def eligible_tasks(tasks, now, policy):
    """只检查已启用且有执行记录入口的自建任务，排除自身、停用及陈旧快照。"""
    return [t for t in tasks if t['id'] != SELF_ID and t.get('instrumented')
            and t.get('source') == 'launchd' and (t.get('label') or '').startswith('com.yjl.')
            and t.get('loaded') and now-t.get('updated_at',0) <= policy['max_snapshot_age']]

def observe(state, tasks, runs, now, policy):
    """一个任务持续异常期间只保留一个事件，成功恢复后才允许下一事件。"""
    incidents = state.setdefault('incidents', {})
    seen = set()
    for task in eligible_tasks(tasks, now, policy):
        tid = task['id']
        seen.add(tid)
        problem = signal(task, runs.get(tid, []), now, policy)
        current = incidents.get(tid)
        if problem is None:
            # 当前正在执行可能仍在恢复，不把运行中/记录缺失误当恢复成功。
            last = (runs.get(tid) or [{}])[0]
            healthy = last.get('status') == 'success'
            if current and current['status'] != 'resolved' and healthy:
                current.update(status='resolved', resolved_at=now)
            continue
        if not current or current['status'] == 'resolved':
            current = incidents[tid] = {
                'id':uuid.uuid4().hex, 'task_id':tid,'name':task['name'],
                'kind':problem['kind'], 'first_seen':now, 'observations':0,
                'status':'observing', 'attempts':0}
        current.update(last_seen=now, reason=problem['reason'])
        current['observations'] += 1
        if problem['kind'] == 'execution_failures':
            current['persistent'] = True
        else:
            current['persistent'] = (current['observations'] >= 2
                and now-current['first_seen'] >= policy['persistent_seconds'])
    return seen

def ready(incident, now, policy):
    return (incident.get('persistent') and incident['status'] in
            ('observing','failed','awaiting_verification')
            and incident['attempts'] < policy['max_repair_rounds']
            and now-incident.get('last_dispatched',0) >= policy['retry_seconds'])

def evidence(task, runs, incident):
    """只传展示字段、脱敏日志及路径；不复制任务环境、配置或凭据。"""
    fields = ['id','name','label','script','definition_path','log_paths','state','schedule','updated_at']
    return {'task':{k:task.get(k) for k in fields}, 'incident':incident,
            'recent_runs':[{k:(redact(v)[-6000:] if k in ('summary','log_tail') else v)
                            for k,v in row.items()} for row in runs[:5]],
            'data':redact(json.dumps(task.get('data') or {},ensure_ascii=False))[:12000]}

def scan(root=ROOT, *, inspect_only=False, collect=snapshot, dispatch=None, now=None):
    """检查与派单共享锁；一次最多修复一个任务，其余等待下一小时，绝不并行修复。"""
    realtime = now is None
    now = time.time() if realtime else now
    policy = {**DEFAULTS, **read(root/'policy.json')}
    state_path = root/'state.json'
    state = read(state_path, {'incidents':{},'threads':{}})
    tasks, runs = collect()
    if not tasks or not any(now-t.get('updated_at',0) <= policy['max_snapshot_age'] for t in tasks):
        raise RuntimeError('看板采集快照缺失或过期，拒绝基于旧记录派单')
    seen = observe(state,tasks,runs,now,policy)
    state['checked_at'] = now
    if not inspect_only:
        save(state_path,state)
    candidates = [i for tid,i in state['incidents'].items()
                  if tid in seen and ready(i,now,policy)
                  and signal(next(t for t in tasks if t['id']==tid),runs.get(tid,[]),now,policy)]
    candidates.sort(key=lambda i:i['first_seen'])
    dispatched = 0
    for incident in candidates[:policy['max_repairs_per_scan']]:
        if inspect_only:
            continue
        tid = incident['task_id']
        task = next(t for t in tasks if t['id']==tid)
        folder = root/'incidents'/incident['id']
        attempt = folder/f'round-{incident["attempts"]+1}'
        attempt.mkdir(parents=True,exist_ok=True,mode=0o700)
        save(attempt/'evidence.json',evidence(task,runs.get(tid,[]),incident))
        incident.update(status='dispatching', attempts=incident['attempts']+1,
                        last_dispatched=now, attempt_folder=str(attempt))
        save(state_path,state)  # 发起 RPC 前落盘；响应不明不能自动重发。
        try:
            if dispatch is None:
                from repair_agent import repair
                asyncio.run(repair(root,state,incident,attempt,lambda:save(state_path,state)))
            else:
                dispatch(state,incident,attempt)
            incident['status']='awaiting_verification'
        except Exception as error:
            if incident['status'] != 'uncertain':
                incident['status']='failed'
            incident['error']=redact(str(error))[-1500:]
        save(state_path,state)
        dispatched += 1
        # 重新读实际结果，不能以模型声称已修复作为成功依据。
        fresh_tasks,fresh_runs=collect()
        observe(state,fresh_tasks,fresh_runs,time.time() if realtime else now,policy)
        save(state_path,state)
    open_incidents = [i for i in state['incidents'].values() if i['status']!='resolved']
    emit('task_health_checked', checked=len(seen), persistent=len(candidates),
         dispatched=dispatched, open_incidents=len(open_incidents), model_called=bool(dispatched),
         inspect_only=inspect_only)
    return state

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only',action='store_true',help='只读检查，不派单或写业务状态')
    args=parser.parse_args()
    os.umask(0o077)
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (ROOT/'task.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            emit('task_health_already_running',model_called=False)
            return
        scan(inspect_only=args.check_only)

if __name__=='__main__':
    main()
