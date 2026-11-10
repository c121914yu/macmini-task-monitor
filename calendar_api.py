"""Read-only weekly schedule projection and bounded execution evidence, in Asia/Shanghai."""
import datetime as dt
import json
import time

import store
from config import ZONE


def repeating(task):
    """Interval and wildcard hourly rules belong in the recurring lane, never invented exact starts."""
    schedule = task.get('schedule', {})
    return schedule.get('type') == 'interval' or any(
        'Hour' not in rule or 'Minute' not in rule for rule in schedule.get('rules', []))


def planned_times(task, day):
    """Expand supported launchd calendar fields; wildcard components stay wildcards, duplicate rules coalesce."""
    times = set()
    ranges = {'Month': (1,12), 'Day': (1,31), 'Weekday': (0,7), 'Hour': (0,23), 'Minute': (0,59)}
    for rule in task.get('schedule', {}).get('rules', []):
        if not rule or any(key not in ranges or isinstance(value, bool) or not isinstance(value, int)
                           or not ranges[key][0] <= value <= ranges[key][1] for key,value in rule.items()):
            continue
        if ('Month' in rule and rule['Month'] != day.month
                or 'Day' in rule and rule['Day'] != day.day
                or 'Weekday' in rule and rule['Weekday'] % 7 != (day.weekday()+1) % 7):
            continue
        for hour in [rule['Hour']] if 'Hour' in rule else range(24):
            for minute in [rule['Minute']] if 'Minute' in rule else range(60):
                times.add(dt.datetime.combine(day, dt.time(hour,minute), ZONE).timestamp())
    return sorted(times)


def week_snapshot(start):
    """Project current configuration for seven days; actual evidence never implies a scheduled run succeeded."""
    try:
        day = dt.date.fromisoformat(start)
    except (ValueError, TypeError):
        raise ValueError('日历起始日期无效') from None
    if abs((day-dt.datetime.now(ZONE).date()).days) > 366:
        raise ValueError('日历仅支持前后一年内的日期')
    left = dt.datetime.combine(day, dt.time(), ZONE).timestamp()
    right = left+7*86400
    days = {(day+dt.timedelta(days=i)).isoformat(): {'planned': [], 'actual': [], 'recurring': {}} for i in range(7)}
    with store.connect() as db:
        tasks = [json.loads(row['snapshot']) for row in db.execute('SELECT snapshot FROM tasks ORDER BY rowid')]
        tasks = [task for task in tasks if task.get('category') == 'work']
        by_id = {task['id']: task for task in tasks}
        repeated = {task['id'] for task in tasks if repeating(task)}
        for task in tasks:
            if task['id'] in repeated:
                continue
            for key, value in days.items():
                for at in planned_times(task, dt.date.fromisoformat(key)):
                    value['planned'].append({'task_id': task['id'], 'at': at, 'loaded': task.get('loaded', False)})
        # One bounded read: frequent executions are counted into lanes, no log bodies or unbounded pagination.
        rows = db.execute("SELECT id,task_id,status,kind,created_at,started_at,finished_at FROM runs WHERE COALESCE(started_at,created_at)>=? AND COALESCE(started_at,created_at)<? AND status<>'skipped' ORDER BY COALESCE(started_at,created_at),id LIMIT 10001", (left,right)).fetchall()
    truncated = len(rows)>10000
    for row in rows[:10000]:
        if row['task_id'] not in by_id:
            continue
        at = row['started_at'] if row['started_at'] is not None else row['created_at']
        key = dt.datetime.fromtimestamp(at,ZONE).date().isoformat()
        if row['task_id'] in repeated:
            lane = days[key]['recurring'].setdefault(row['task_id'], {'count':0,'statuses':{},'last_run_id':None,'last_at':None})
            lane['count'] += 1
            lane['statuses'][row['status']] = lane['statuses'].get(row['status'],0)+1
            lane.update(last_run_id=row['id'],last_at=at)
        else:
            days[key]['actual'].append({'id':row['id'],'task_id':row['task_id'],'at':at,
                'status':row['status'],'kind':row['kind'],'time_known':row['started_at'] is not None,
                'duration':max(0,row['finished_at']-row['started_at']) if row['started_at'] is not None and row['finished_at'] is not None else None})
    for value in days.values():
        value['planned'].sort(key=lambda item:(item['at'],item['task_id']))
    return {'start':day.isoformat(),'days':days,'recurring_ids':sorted(repeated),'now':time.time(),
            'timezone':'Asia/Shanghai','truncated':truncated,'retention_days':7}
