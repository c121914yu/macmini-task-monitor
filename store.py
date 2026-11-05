"""监控自己的 SQLite 账本；不写入任何被监控业务的数据库。"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from config import DATA, DB_PATH, redact


@contextmanager
def connect():
    """每个请求/进程独立连接，WAL 允许页面读取与任务写入并行。"""
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=10000')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    """幂等建表；数据库目录仅当前用户可读，历史记录按稳定来源键去重。"""
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DATA, 0o700)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                external_key TEXT UNIQUE, kind TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL,
                started_at REAL, finished_at REAL, heartbeat_at REAL,
                pid INTEGER, process_start TEXT, exit_code INTEGER,
                summary TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}',
                log_tail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS runs_task_time ON runs(task_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS runs_time ON runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('created_at', json.dumps(time.time())))
    os.chmod(DB_PATH, 0o600)


def set_meta(key, value):
    """保存采集心跳和安装凭据；使用同一 SQLite 事务避免半写文件。"""
    with connect() as db:
        db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))


def get_meta(key, default=None):
    with connect() as db:
        row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def backup_database(name):
    """历史结果修正前保存一次 WAL 一致快照；写完后原子发布，避免保留半份备份。"""
    folder = DATA / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    destination = folder / name
    if destination.exists():
        return str(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    if temporary.exists():
        temporary.unlink()
    target = sqlite3.connect(temporary)
    try:
        with connect() as source:
            source.backup(target)
    finally:
        target.close()
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    return str(destination)


def save_task(task):
    with connect() as db:
        db.execute('INSERT INTO tasks VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot,updated_at=excluded.updated_at',
                   (task['id'], json.dumps(task, ensure_ascii=False), time.time()))


def reconcile_discovered_tasks(definitions):
    """按相同 launchd label 合并自动发现的旧别名；执行凭据只改归属，不删除。

    只处理 id/name 均等于 label 的自动发现条目。正式登记冲突、来源不符或
    快照身份不一致时保留现场，避免把不同任务或用户自行命名的任务误合并。
    """
    labels = {}
    for task in definitions:
        if task.get('source') == 'launchd' and task.get('label'):
            labels.setdefault(task['label'], []).append(task)
    canonical = {label: items[0]['id'] for label, items in labels.items()
                 if len(items) == 1 and items[0]['id'] != label}
    if not canonical:
        return 0
    with connect() as db:
        rows = db.execute('SELECT id,snapshot FROM tasks').fetchall()
    candidates = []
    for row in rows:
        try:
            snapshot = json.loads(row['snapshot'])
        except (ValueError, TypeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        alias = row['id']
        if (alias in canonical and snapshot.get('id') == alias
                and snapshot.get('name') == alias and snapshot.get('label') == alias
                and snapshot.get('source') == 'launchd'
                and snapshot.get('description') == '自动发现的 launchd 定时任务。'):
            candidates.append((alias, canonical[alias], row['snapshot']))
    if not candidates:
        return 0
    backup_database('monitor-before-task-aliases-v1.sqlite3')
    merged = 0
    with connect() as db:
        for alias, target, original in candidates:
            # 备份期间可能有新采集；仅合并仍与已验证快照一致的旧条目。
            row = db.execute('SELECT snapshot FROM tasks WHERE id=?', (alias,)).fetchone()
            if not row or row['snapshot'] != original:
                continue
            moved = db.execute('UPDATE runs SET task_id=? WHERE task_id=?', (target, alias)).rowcount
            evidence = {'canonical_id': target, 'previous_snapshot': json.loads(original),
                        'moved_runs': moved, 'merged_at': time.time()}
            db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO NOTHING',
                       ('task-alias:' + alias, json.dumps(evidence, ensure_ascii=False)))
            db.execute('DELETE FROM tasks WHERE id=?', (alias,))
            merged += 1
    return merged


def prune_runs(now=None):
    """滚动保留七天执行记录；运行中的记录留到结束后再清理。

    截止点同时约束历史导入，避免下一轮从旧日志重新导入已过期的记录。
    SQLite 删除后的空页由新记录复用，不在采集循环内执行阻塞性的 VACUUM。
    """
    now = time.time() if now is None else now
    cutoff = now - 7 * 86400
    with connect() as db:
        removed = db.execute("DELETE FROM runs WHERE created_at<? AND status<>'running'",
                             (cutoff,)).rowcount
        db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('run_retention', json.dumps({'days': 7, 'cutoff': cutoff, 'checked_at': now})))
    return removed


def insert_run(run):
    """真实执行与历史凭据共用账本；导入冲突时保留最初记录，不重复计数。"""
    fields = ['id', 'task_id', 'external_key', 'kind', 'status', 'created_at', 'started_at', 'finished_at', 'heartbeat_at', 'pid', 'process_start', 'exit_code', 'summary', 'details', 'log_tail']
    values = {**{'summary': '', 'details': {}, 'log_tail': ''}, **run}
    values['details'] = json.dumps(values['details'], ensure_ascii=False)
    values['summary'] = redact(values['summary'])[:2000]
    values['log_tail'] = redact(values['log_tail'])[-24000:]
    with connect() as db:
        retention = db.execute('SELECT value FROM metadata WHERE key=?', ('run_retention',)).fetchone()
        if (retention and values.get('status') != 'running'
                and values['created_at'] < json.loads(retention['value'])['cutoff']):
            return
        db.execute(f'''INSERT INTO runs ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})
            ON CONFLICT(external_key) DO UPDATE SET status=excluded.status,started_at=excluded.started_at,
            finished_at=excluded.finished_at,exit_code=excluded.exit_code,summary=excluded.summary,
            details=excluded.details,log_tail=excluded.log_tail
            WHERE runs.kind='history' AND excluded.kind='history' ''', [values.get(key) for key in fields])


def update_run(run_id, **fields):
    allowed = {'status', 'finished_at', 'heartbeat_at', 'exit_code', 'summary', 'log_tail'}
    if not fields or not set(fields).issubset(allowed):
        raise ValueError('Invalid run fields')
    for key in ('summary', 'log_tail'):
        if key in fields:
            fields[key] = redact(fields[key])[-24000:]
    with connect() as db:
        db.execute(f'UPDATE runs SET {",".join(key+"=?" for key in fields)} WHERE id=?', [*fields.values(), run_id])


def decode_run(row):
    if row is None:
        return None
    run = dict(row)
    run['details'] = json.loads(run['details'])
    run['duration'] = max(0, run['finished_at'] - run['started_at']) if run['started_at'] is not None and run['finished_at'] is not None else None
    return run


def list_runs(filters):
    """有界分页查询；所有筛选参数作为绑定值，不拼接用户 SQL。"""
    clauses, values = [], []
    for key in ('task_id', 'status', 'kind'):
        if filters.get(key):
            if key == 'status' and filters[key] == 'non_skipped':
                clauses.append('status<>?')
                values.append('skipped')
            else:
                clauses.append(key + '=?')
                values.append(filters[key])
    if filters.get('since') is not None:
        clauses.append('created_at>=?')
        values.append(filters['since'])
    if filters.get('search'):
        clauses.append("(summary LIKE ? ESCAPE '\\' OR task_id LIKE ? ESCAPE '\\')")
        query = '%' + filters['search'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        values.extend([query, query])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with connect() as db:
        total = db.execute('SELECT count(*) FROM runs' + where, values).fetchone()[0]
        rows = db.execute('SELECT * FROM runs' + where + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                          [*values, filters.get('limit', 50), filters.get('offset', 0)]).fetchall()
    result = [decode_run(row) for row in rows]
    for run in result:
        run.pop('log_tail', None)
    return {'items': result, 'total': total, 'limit': filters.get('limit', 50), 'offset': filters.get('offset', 0)}


def read_run(run_id):
    with connect() as db:
        return decode_run(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
