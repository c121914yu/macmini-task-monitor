#!/usr/bin/env python3
"""仅提供读取接口和静态页面的本机 HTTP 服务，无任务执行或配置接口。"""

import argparse
import datetime as dt
import json
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import collector
import store
from config import DB_PATH, ROOT, ZONE, redact


def snapshot():
    """返回一次一致的账本视图；统计中的“记录”包括明确标识来源的历史凭据。"""
    today = dt.datetime.now(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    with store.connect() as db:
        tasks = [json.loads(row['snapshot']) for row in db.execute('SELECT snapshot FROM tasks ORDER BY rowid')]
        counts = {row['status']: row['total'] for row in db.execute('SELECT status,count(*) AS total FROM runs WHERE created_at>=? GROUP BY status', (today.timestamp(),))}
        daily = []
        for offset in range(6, -1, -1):
            day = today - dt.timedelta(days=offset)
            values = {row['status']: row['total'] for row in db.execute('SELECT status,count(*) AS total FROM runs WHERE created_at>=? AND created_at<? GROUP BY status', (day.timestamp(), (day + dt.timedelta(days=1)).timestamp()))}
            daily.append({'date': day.date().isoformat(), 'counts': values})
        total = db.execute('SELECT count(*) FROM runs').fetchone()[0]
        execution_count = db.execute("SELECT count(*) FROM runs WHERE kind='execution'").fetchone()[0]
    metadata = store.get_meta('collector', {})
    work = [task for task in tasks if task['category'] == 'work']
    return {'tasks': tasks, 'collector': metadata, 'now': time.time(), 'timezone': 'Asia/Shanghai',
            'stats': {'tasks': len(work), 'running': sum(task['state'] == 'running' for task in work),
                      'attention': sum(task['state'] in ('attention', 'overdue', 'disabled', 'unknown') for task in work),
                      'today': sum(counts.values()), 'counts': counts, 'total_records': total, 'execution_records': execution_count},
            'daily': daily, 'recent': store.list_runs({'limit': 8, 'status': 'non_skipped'})['items'], 'recording_since': store.get_meta('created_at')}


def log_tail(path):
    """仅由注册表提供路径；读取固定上限的尾部，不接受客户端传入文件路径。"""
    source = Path(path)
    if not source.is_file():
        return {'path': str(source), 'text': '尚无日志文件', 'available': False}
    with source.open('rb') as stream:
        size = source.stat().st_size
        stream.seek(max(0, size - 48000))
        text = stream.read(48000).decode('utf-8', errors='replace')
    return {'path': str(source), 'text': redact(text), 'available': True, 'truncated': size > 48000}


class Handler(BaseHTTPRequestHandler):
    """所有请求只读，限制 Host/Origin；不向外部站点开放 CORS。"""

    def log_message(self, _format, *_args):
        pass

    def respond(self, status, body, mime='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        for key, value in {'Content-Type': mime, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                           'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
                           'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"}.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        host = urlparse('//' + self.headers.get('Host', '')).hostname
        if host not in self.server.allowed_hosts:
            self.respond(403, {'error': '不允许的访问主机'})
            return
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            self.respond(403, {'error': '不允许跨站访问'})
            return
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if path == '/api/overview':
                self.respond(200, snapshot())
            elif path == '/api/health':
                with store.connect() as db:
                    db.execute('SELECT 1').fetchone()
                state = store.get_meta('collector', {})
                healthy = time.time() - state.get('checked_at', 0) < 60
                self.respond(200 if healthy else 503, {'ok': healthy, 'collector': state.get('checked_at'), 'database': 'ok'})
            elif path == '/api/runs':
                query = parse_qs(url.query)
                filters = {key: query[key][0] for key in ('task_id', 'status', 'kind', 'search') if query.get(key)}
                for key, default, maximum in [('limit', 50, 100), ('offset', 0, 10000000), ('days', 0, 3650)]:
                    value = int(query.get(key, [default])[0])
                    if value < (1 if key == 'limit' else 0) or value > maximum:
                        raise ValueError('分页或日期范围无效')
                    filters[key] = value
                if filters.get('status') and filters['status'] not in ('non_skipped', 'success', 'failed', 'running', 'skipped', 'interrupted', 'unknown'):
                    raise ValueError('执行状态无效')
                if filters.get('kind') and filters['kind'] not in ('history', 'execution'):
                    raise ValueError('记录来源无效')
                if filters['days']:
                    filters['since'] = (dt.datetime.now(ZONE).replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=filters['days'] - 1)).timestamp()
                self.respond(200, store.list_runs(filters))
            elif path.startswith('/api/runs/'):
                record = store.read_run(path.removeprefix('/api/runs/'))
                self.respond(200, record) if record else self.respond(404, {'error': '记录不存在'})
            elif path.startswith('/api/tasks/') and path.endswith('/logs'):
                task_id = path[len('/api/tasks/'):-len('/logs')]
                with store.connect() as db:
                    row = db.execute('SELECT snapshot FROM tasks WHERE id=?', (task_id,)).fetchone()
                if not row:
                    self.respond(404, {'error': '任务不存在'})
                    return
                task = json.loads(row['snapshot'])
                paths = task.get('log_paths', [])
                if task_id == 'fastgpt-backup':
                    paths = [*paths, str(Path('/Users/yjl/.local/share/fastgpt-backup/logs') / (dt.datetime.now(ZONE).date().isoformat() + '.log'))]
                self.respond(200, {'items': [log_tail(path) for path in dict.fromkeys(paths)]})
            elif path in ('/', '/index.html', '/app.js', '/styles.css', '/favicon.svg'):
                asset = ROOT / 'web' / ('index.html' if path == '/' else path.lstrip('/'))
                mime = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}[asset.suffix]
                self.respond(200, asset.read_bytes(), mime)
            else:
                self.respond(404, {'error': '页面不存在'})
        except ValueError as error:
            self.respond(400, {'error': str(error)[:200]})
        except Exception as error:
            print('http_error:', redact(error), flush=True)
            self.respond(500, {'error': '读取暂时失败，请稍后刷新'})

    def do_POST(self):
        self.respond(405, {'error': '监控页面只提供读取接口'})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def serve(options):
    """分别监听回环和显式指定的地址；Tailscale 暂不可用时保留本机访问并重试绑定。"""
    store.initialize()
    stop = threading.Event()
    servers = []
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    collector.collect()
    threading.Thread(target=collector.loop, args=(stop,), daemon=True).start()

    def bind(address):
        while not stop.is_set():
            try:
                server = ThreadingHTTPServer((address, options.port), Handler)
                server.daemon_threads = True
                server.allowed_hosts = {'localhost', '127.0.0.1', socket.gethostname().lower(), address, *options.allowed_host}
                servers.append(server)
                print(f'Task monitor: http://{address}:{options.port} · SQLite: {DB_PATH}', flush=True)
                server.serve_forever(poll_interval=0.5)
                server.server_close()
                return
            except OSError as error:
                print('bind_retry:', address, redact(error), flush=True)
                stop.wait(15)

    for address in dict.fromkeys(options.bind or ['127.0.0.1']):
        threading.Thread(target=bind, args=(address,), daemon=True).start()
    stop.wait()
    for server in servers:
        server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', action='append')
    parser.add_argument('--allowed-host', action='append', default=[])
    parser.add_argument('--port', type=int, default=8765)
    serve(parser.parse_args())
