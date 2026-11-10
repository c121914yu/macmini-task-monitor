#!/usr/bin/env python3
"""Deterministic read-only collectors; raw data stays in private run directories."""
import argparse
import datetime as dt
import email
import email.policy
import fcntl
import imaplib
import http.client
import socket
import urllib.error
import json
import re
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path
from common import ROOT, ZONE, read, save, window


def retry(callback):
    """Only retry the same mailbox operation; no model or webhook is involved."""
    for index, delay in enumerate((0, 10, 30)):
        if delay:
            time.sleep(delay)
        try:
            return callback()
        except Exception:
            if index == 2:
                raise


def imap_collect(account, start, end):
    """EXAMINE plus BODY.PEEK preserves unread; retain UIDVALIDITY for later STORE."""
    with imaplib.IMAP4_SSL(account['host'], 993, ssl_context=ssl.create_default_context(), timeout=45) as client:
        client.login(account['username'], account['password'])
        status, _ = client.select('INBOX', readonly=True)
        if status != 'OK':
            raise RuntimeError('EXAMINE failed')
        validity = client.response('UIDVALIDITY')[1][0].decode()
        month = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
        def fmt(day):
            return f'{day.day:02d}-{month[day.month-1]}-{day.year}'
        status, ids = client.uid('search', None, 'SINCE', fmt(start.date() - dt.timedelta(days=1)),
                                 'BEFORE', fmt(end.date() + dt.timedelta(days=1)))
        if status != 'OK':
            raise RuntimeError('UID SEARCH failed')
        messages = []
        for uid in ids[0].split():
            status, response = client.uid('fetch', uid, '(INTERNALDATE BODY.PEEK[])')
            parts = [x for x in response if isinstance(x, tuple)]
            if status != 'OK' or len(parts) != 1:
                raise RuntimeError('UID FETCH failed')
            header, raw = parts[0]
            stamp = imaplib.Internaldate2tuple(header)
            if stamp is None:
                raise RuntimeError('INTERNALDATE missing')
            instant = dt.datetime.fromtimestamp(time.mktime(stamp), ZONE)
            if not start <= instant < end:
                continue
            message = email.message_from_bytes(raw, policy=email.policy.default)
            body = message.get_body(preferencelist=('plain', 'html'))
            text = body.get_content() if body else ''
            messages.append({'uid': uid.decode(), 'received_at': instant.isoformat(),
                             'subject': str(message.get('Subject', '')),
                             'from': str(message.get('From', '')), 'body': str(text)})
        return {'username': account['username'], 'folder': 'INBOX',
                'uidvalidity': validity, 'status': 'complete', 'messages': messages}


def collect_imap(folder, start, end):
    accounts = read(ROOT / 'private.json')['mail-digest']['imap']
    result = []
    for account in accounts:
        path = folder / ('imap-' + account['username'] + '.json')
        existing = read(path)
        if existing and existing.get('status') == 'complete':
            result.append(existing)
            continue
        try:
            item = retry(lambda: imap_collect(account, start, end))
        except Exception as error:
            item = {'username': account['username'], 'folder': 'INBOX', 'status': 'failed',
                    'error_type': type(error).__name__, 'messages': []}
        save(path, item)
        result.append(item)
    save(folder / 'imap.json', result)
    return {'mailboxes': len(result), 'failed': sum(x['status'] != 'complete' for x in result)}


def mark_imap(folder):
    """Only this report's exact UID set, after every summary segment is confirmed sent."""
    receipt = read(folder / 'notification.json', {})
    if receipt.get('status') != 'sent':
        raise RuntimeError('Complete summary not delivered; refusing unread changes')
    manifest = read(folder / 'mail-included.json')
    captured = {x['username']: x for x in read(folder / 'imap.json')}
    accounts = {x['username']: x for x in read(ROOT / 'private.json')['mail-digest']['imap']}
    progress = read(folder / 'imap-marked.json', {})
    for username, ids in manifest.get('imap', {}).items():
        item, account = captured[username], accounts[username]
        allowed = {x['uid'] for x in item['messages']}
        if item['status'] != 'complete' or not isinstance(ids, list) or any(x not in allowed for x in ids):
            raise ValueError('Reported UID not in the successfully collected snapshot')
        if progress.get(username) == sorted(set(ids)):
            continue
        def mark():
            if not ids:
                return
            with imaplib.IMAP4_SSL(account['host'], 993, ssl_context=ssl.create_default_context(), timeout=45) as client:
                client.login(username, account['password'])
                if client.select('INBOX', readonly=False)[0] != 'OK':
                    raise RuntimeError('SELECT failed')
                validity = client.response('UIDVALIDITY')[1][0].decode()
                if validity != item['uidvalidity']:
                    raise RuntimeError('UIDVALIDITY changed; refusing stale UID')
                for uid in sorted(set(ids)):
                    if client.uid('store', uid, '+FLAGS.SILENT', r'(\Seen)')[0] != 'OK':
                        raise RuntimeError('UID STORE failed')
        retry(mark)
        progress[username] = sorted(set(ids))
        save(folder / 'imap-marked.json', progress)
    return {'status': 'marked'}


SETTINGS = ' SETTINGS max_threads=1,max_block_size=16,max_bytes_before_external_group_by=32000000'
FILTERS = {
    'zod': """attributes_string['category'] = '["http","error"]' AND JSONExtractString(body,'__log_message') = 'Zod validation error'""",
    'llm': """attributes_string['category'] = '["ai","llm"]' AND severity_text = 'error'""",
    'plugin': """resources_string['service.name'] = 'fastgpt-io-plugin-v1' AND JSONExtractString(body,'error','cause','reason','en') = 'Plugin not found'"""
}
PROJECTIONS = {
    'zod': "timestamp,id,trace_id,JSONExtractString(body,'requestId') AS request_id,JSONExtractRaw(body,'data') AS issues,JSONExtractString(body,'__log_message') AS message",
    'llm': """timestamp,id,trace_id,JSONExtractString(body,'requestId') AS request_id,JSONExtractRaw(body,'error') AS error,coalesce(nullIf(JSONExtractString(body,'request','model'),''),nullIf(JSONExtractString(body,'requestBody','model'),''),JSONExtractString(JSONExtractString(body,'requestBody'),'model')) AS request_model,JSONExtractString(body,'data','model') AS data_model,JSONExtractString(body,'model') AS model,JSONExtractString(body,'__log_message') AS stage,JSONExtractString(body,'provider') AS provider,JSONExtractString(body,'request','provider') AS request_provider""",
    'plugin': """timestamp,id,trace_id,JSONExtractString(body,'requestId') AS request_id,JSONExtractString(body,'input','pluginId') AS plugin_id,JSONExtractString(body,'input','pluginName') AS plugin_name,JSONExtractString(body,'input','toolName') AS tool_name,JSONExtractString(body,'input','source') AS source,JSONExtractRaw(body,'input','version') AS version,JSONExtractRaw(body,'input','fallbackLatestVersion') AS fallback,JSONExtractString(body,'__log_message') AS entry"""
}


class SigNozQueryWindowError(RuntimeError):
    """查询结果截断或明确的服务端资源限制，缩短时间窗口后可以安全重查。"""


class SigNozRequestError(RuntimeError):
    """只携带允许公开的连接诊断；不保存 API key、SQL、响应体或原始异常文本。"""

    def __init__(self, details):
        self.details = details
        labels = {
            'certificate_verification': 'HTTPS 证书校验失败',
            'tls_eof': 'TLS 连接提前关闭',
            'tls_handshake': 'TLS 握手失败',
            'timeout': '请求超时',
            'dns': 'DNS 解析失败',
            'connection': '网络连接失败',
            'authentication': '认证或权限失败',
            'rate_limit': '接口限流',
            'server_error': '服务端暂时不可用',
            'http_error': 'HTTP 请求被拒绝',
        }
        status = f'（HTTP {details["http_status"]}）' if details.get('http_status') else ''
        retries = details['attempts'] - 1
        budget = f'，已重试 {retries} 次' if retries else '，请求 1 次后停止'
        super().__init__('SigNoz 数据采集失败：' + labels[details['category']] + status + budget)


def signoz_query(sql, lo, hi):
    """固定只读 SELECT；瞬时连接错误原窗重试，证书/权限错误直接报告，资源限制交给拆窗。"""
    config = read(ROOT / 'private.json')['signoz-report']
    payload = {'schemaVersion': 'v1', 'start': lo, 'end': hi-1, 'requestType': 'scalar',
               'compositeQuery': {'queries': [{'type': 'clickhouse_sql',
                                               'spec': {'name': 'A', 'query': sql + SETTINGS}}]}}
    request = urllib.request.Request(config['base_url'] + '/api/v5/query_range',
        data=json.dumps(payload).encode(),
        headers={'SIGNOZ-API-KEY': config['api_key'], 'Content-Type': 'application/json'}, method='POST')

    def classify(error, attempts):
        """按异常类型分类；只允许已知安全的字段进入状态和看板日志。"""
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        details = {'category': 'connection', 'retryable': True, 'attempts': attempts,
                   'error_type': type(error).__name__, 'reason_type': type(reason).__name__,
                   'start_ms': lo, 'end_ms': hi}
        if isinstance(error, urllib.error.HTTPError):
            details['http_status'] = error.code
            if error.code in (401, 403):
                details.update(category='authentication', retryable=False)
            elif error.code == 429:
                details['category'] = 'rate_limit'
            elif error.code in (500, 502, 503, 504):
                details['category'] = 'server_error'
            else:
                details.update(category='http_error', retryable=False)
        elif isinstance(reason, ssl.SSLCertVerificationError) or any(
                marker in str(reason).lower() for marker in ('certificate_verify_failed', 'certificate verify failed')):
            details.update(category='certificate_verification', retryable=False)
            details['ssl_verify_code'] = getattr(reason, 'verify_code', None)
            details['ssl_verify_message'] = getattr(reason, 'verify_message', None)
        elif isinstance(reason, ssl.SSLEOFError) or (
                isinstance(reason, str) and 'eof' in reason.lower()):
            details['category'] = 'tls_eof'
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            details['category'] = 'timeout'
        elif isinstance(reason, socket.gaierror):
            details.update(category='dns', retryable=reason.errno == socket.EAI_AGAIN)
        elif isinstance(reason, ssl.SSLError):
            details['category'] = 'tls_handshake'
        return details

    delays = (10, 30, 60)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=55) as response:
                data = json.load(response)
            break
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            if isinstance(error, urllib.error.HTTPError):
                try:
                    if error.code in (400, 500):
                        # 只识别明确的 ClickHouse 资源限制；响应体不落盘、不写入日志。
                        try:
                            body = error.read(16384).decode('utf-8', errors='replace').upper()
                        except (OSError, http.client.HTTPException):
                            body = ''
                        # SigNoz 也会仅返回 ClickHouse 的 code: 241 + 普通文字，省略符号名。
                        # 必须同时出现错误码和内存限制描述，不能把所有 HTTP 500 都当成资源问题。
                        memory_limit = (re.search(r'\bCODE\s*[:=]\s*241\b', body)
                                        and re.search(r'\bMEMORY\s+LIMIT\b', body))
                        if memory_limit or any(code in body for code in (
                                'MEMORY_LIMIT_EXCEEDED', 'TIMEOUT_EXCEEDED',
                                'TOO_MANY_ROWS_OR_BYTES', 'TOO_MANY_ROWS')):
                            raise SigNozQueryWindowError('SigNoz 查询触及资源限制，需要缩短窗口') from error
                finally:
                    error.close()
            details = classify(error, attempt + 1)
            if not details['retryable'] or attempt == len(delays):
                raise SigNozRequestError(details) from error
            print(json.dumps({'event': 'signoz_query_retry', **details,
                              'delay_seconds': delays[attempt]}, ensure_ascii=False), flush=True)
            time.sleep(delays[attempt])
    result = data['data']['data']['results'][0]
    if result.get('nextCursor'):
        raise SigNozQueryWindowError('Truncated SQL result')
    if 'data' not in result:
        # Some server versions return raw rows even for SQL projections.
        if sql.startswith('SELECT count()'):
            keys = ['total']
        elif sql.startswith('SELECT trace_id,'):
            keys = ['trace_id', 'url_path', 'route']
        elif sql.startswith('SELECT '+PROJECTIONS['zod']):
            keys = ['timestamp','id','trace_id','request_id','issues','message']
        elif sql.startswith('SELECT '+PROJECTIONS['plugin']):
            keys = ['timestamp','id','trace_id','request_id','plugin_id','plugin_name','tool_name','source','version','fallback','entry']
        elif sql.startswith('SELECT '+PROJECTIONS['llm']):
            keys = ['timestamp','id','trace_id','request_id','error','request_model','data_model','model','stage','provider','request_provider']
        else:
            raise RuntimeError('Unsupported SQL response projection')
        result['data'] = [[row['data'].get(key,'') for key in keys] for row in result['rows']]
    return result


def signoz_clause(lo, hi):
    return (f'FROM signoz_logs.logs_v2 PREWHERE ts_bucket_start >= {(lo//1800000)*1800} '
            f'AND ts_bucket_start < {(hi+1799999)//1800000*1800} '
            f'AND timestamp >= {lo}000000 AND timestamp < {hi}000000 ')


def collect_signoz(folder, start, end):
    """Independent count/extract checks, bounded time buckets, and unique log IDs."""
    output = {}
    for day, left, right in [('current', start, end),
                              ('previous', start-dt.timedelta(days=1), start)]:
        output[day] = {}
        for kind in FILTERS:
            path = folder / (day + '-' + kind + '.json')
            existing = read(path)
            if existing and existing.get('complete'):
                output[day][kind] = existing
                continue
            def fetch(lo, hi):
                clause = signoz_clause(lo, hi) + 'WHERE ' + FILTERS[kind]
                try:
                    count = signoz_query('SELECT count() AS total ' + clause, lo, hi)['data'][0][0]
                    rows = signoz_query('SELECT '+PROJECTIONS[kind]+' '+clause, lo, hi).get('data', []) if count else []
                    if len(rows) == count:
                        return rows
                except SigNozQueryWindowError:
                    if hi-lo <= 60000:
                        raise
                if hi-lo <= 60000:
                    raise SigNozQueryWindowError('Count/extract mismatch at minimum bucket')
                mid = (lo+hi)//2
                return fetch(lo, mid) + fetch(mid, hi)
            lo, hi = int(left.timestamp()*1000), int(right.timestamp()*1000)
            rows = []
            for bucket in range(lo, hi, 3600000):
                rows += fetch(bucket, min(bucket+3600000, hi))
            if len({r[1] for r in rows}) != len(rows) or any(not lo*1000000<=int(r[0])<hi*1000000 for r in rows):
                raise RuntimeError('Duplicate or out-of-window logs')
            item = {'complete': True, 'total': len(rows), 'columns': PROJECTIONS[kind],
                    'rows': rows}
            save(path, item)
            output[day][kind] = item
    tids = sorted({r[2] for item in output['current'].values() for r in item['rows']
                   if re.fullmatch(r'[0-9a-fA-F]{32}', str(r[2])) and set(r[2]) != {'0'}})
    correlations = []
    for offset in range(0, len(tids), 40):
        ids = ','.join("'"+v+"'" for v in tids[offset:offset+40])
        lo, hi = int(start.timestamp()*1000), int(end.timestamp()*1000)
        for bucket in range(lo, hi, 1800000):
            stop = min(bucket+1800000, hi)
            sql = ("SELECT trace_id,path(JSONExtractString(body,'url')) AS url_path,"
                   "attributes_string['http.route'] AS route " + signoz_clause(bucket, stop) +
                   "WHERE attributes_string['category'] = '[\"http\",\"request\"]' AND trace_id IN ("+ids+")")
            try:
                correlations += signoz_query(sql, bucket, stop).get('data', [])
            except Exception:
                # Missing route attribution is explicit, never guessed from a similar request.
                output.setdefault('route_query_failures', 0)
                output['route_query_failures'] += 1
    output['http_correlations'] = correlations
    save(folder / 'signoz.json', output)
    return {'totals': {k:v['total'] for k,v in output['current'].items()}}


def collect_prs(folder, start, end):
    """Fetch all authors and branches; recursively split GitHub's 1000-result window."""
    import os
    os.environ['PATH'] = '/opt/homebrew/bin:' + os.environ.get('PATH', '')
    def search(repo, left, right):
        query = (f'repo:{repo} is:pr is:merged '
                 f'merged:{left.astimezone(dt.timezone.utc).isoformat(timespec="seconds")}..'
                 f'{right.astimezone(dt.timezone.utc).isoformat(timespec="seconds")}')
        def page(number):
            result = subprocess.run(['gh','api','--method','GET','search/issues',
                '-f','q='+query,'-f','per_page=100','-f','page='+str(number)],
                text=True,capture_output=True,check=True,timeout=120)
            data = json.loads(result.stdout)
            if data.get('incomplete_results'):
                raise RuntimeError('Incomplete GitHub search')
            return data
        first = page(1)
        total = first['total_count']
        # Inspect total before pagination: GitHub rejects pages beyond 1000.
        if total >= 1000:
            if (right-left).total_seconds() <= 1:
                raise RuntimeError('GitHub result cap')
            middle = left+(right-left)/2
            return search(repo,left,middle)+search(repo,middle,right)
        items = list(first['items'])
        for number in range(2,(total+99)//100+1):
            data = page(number)
            if data['total_count'] != total:
                raise RuntimeError('GitHub search changed during pagination')
            items.extend(data['items'])
        if len(items) != total:
            raise RuntimeError('Incomplete pagination')
        return items
    result = {}
    for repo in ['labring/FastGPT','labring/fastgpt-pro','labring/fastgpt-plugin']:
        path = folder / (repo.split('/')[-1] + '-prs.json')
        if path.exists():
            result[repo] = read(path)
            continue
        by_number = {x['number']:x for x in search(repo,start,end)}
        items = []
        for number in sorted(by_number):
            data = subprocess.run(['gh','pr','view',str(number),'--repo',repo,'--json',
                'number,title,body,url,author,mergedAt,baseRefName'],
                text=True,capture_output=True,check=True,timeout=45)
            item = json.loads(data.stdout)
            merged = dt.datetime.fromisoformat(item['mergedAt'].replace('Z','+00:00'))
            if start < merged <= end:
                items.append(item)
        save(path, items)
        result[repo] = items
    save(folder / 'prs.json', result)
    return {'counts': {k:len(v) for k,v in result.items()}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['mark-imap'])
    p.add_argument('--folder', required=True)
    args = p.parse_args()
    folder = Path(args.folder).resolve()
    if not folder.is_relative_to((ROOT/'runs'/'mail-digest').resolve()):
        raise ValueError('Invalid mail run path')
    with (folder/'mark.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(mark_imap(folder)))


if __name__ == '__main__':
    main()
