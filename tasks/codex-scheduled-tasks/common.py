"""Private, atomic receipts and stable Shanghai scheduling periods."""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
ROOT = Path(os.environ.get('SCHEDULED_TASK_ROOT', Path(__file__).resolve().parent))
TASK_IDS = ('mail-digest', 'signoz-report', 'plugin-model-sync', 'pr-weekly-report')


def save(path, value):
    """Atomic private state: receipts must exist before any external mutation."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def period(task, now=None):
    """A late Friday run keeps its Friday cycle; daily reports cover yesterday."""
    now = now or dt.datetime.now(ZONE)
    if task == 'pr-weekly-report':
        day = now.date() - dt.timedelta(days=(now.weekday() - 4) % 7)
    elif task in ('mail-digest', 'signoz-report'):
        day = now.date() - dt.timedelta(days=1)
    else:
        day = now.date()
    return day.isoformat()


def window(task, key, started_at):
    if task == 'pr-weekly-report':
        start = dt.datetime.combine(dt.date.fromisoformat(key) - dt.timedelta(days=7),
                                    dt.time(12), ZONE)
        end = dt.datetime.fromtimestamp(started_at, ZONE)
    else:
        start = dt.datetime.combine(dt.date.fromisoformat(key), dt.time(), ZONE)
        end = start + dt.timedelta(days=1)
    return start, end


def configuration(task):
    if task not in TASK_IDS:
        raise ValueError('Unknown task')
    return read(ROOT / 'tasks.json')[task]


def run_folder(task, key):
    dt.date.fromisoformat(key)
    return ROOT / 'runs' / task / key
