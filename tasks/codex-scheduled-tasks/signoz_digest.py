"""Prepare bounded, counted SigNoz evidence before any model starts; never export raw messages."""
import collections
import datetime as dt
import hashlib
import json
import re
from urllib.parse import urlsplit

from common import ZONE, save

MAX_GROUPS = 20
MAX_EXAMPLES = 2
MAX_DIMENSIONS = 8
MAX_DIGEST_BYTES = 128 * 1024
KINDS = {'zod': 6, 'llm': 11, 'plugin': 11}


class DigestError(RuntimeError):
    """Fixed public diagnostics only: input contents and arbitrary exceptions stay private."""


def decode(value):
    """JSON projections may contain objects, encoded objects, scalars or invalid text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def safe_label(value, fallback='未记录'):
    """Only declared identifier fields may leave the host; arbitrary prose/URLs never do."""
    text = str(value) if value is not None else ''
    if (not re.fullmatch(r'[\w./:-]{1,100}', text)
            or '://' in text or re.match(r'(?i)(sk-|bearer|eyJ|gh[pousr]_)', text)):
        return fallback
    return text


def valid_trace(value):
    return bool(re.fullmatch(r'[0-9a-fA-F]{32}', str(value))) and set(str(value)) != {'0'}


def safe_route(value):
    """Keep API route shapes only; remove host, query, fragments and dynamic path segments."""
    try:
        path = urlsplit(str(value)).path
    except ValueError:
        return '接口未知'
    if not path.startswith('/api/'):
        return '接口未知'
    parts = [part if (re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,23}|:[A-Za-z_]+', part)
                               and not re.fullmatch(r'[a-fA-F0-9]{16,}', part))
                    else ':id' for part in path.split('/')[1:]]
    return '/'+'/'.join(parts)


def request_keys(rows):
    """Resolve request/trace aliases only on unique co-occurrence; log IDs approximate missing IDs."""
    aliases = collections.defaultdict(set)
    def request(value):
        return str(value) if value and str(value).lower() not in ('null', 'undefined', 'none') else None
    for row in rows:
        if valid_trace(row[2]) and request(row[3]):
            aliases[str(row[2]).lower()].add(request(row[3]))
    keys = []
    for row in rows:
        trace = str(row[2]).lower() if valid_trace(row[2]) else None
        candidates = aliases.get(trace, set())
        if request(row[3]):
            keys.append(('request', request(row[3])))
        elif trace:
            keys.append(('request', next(iter(candidates))) if len(candidates) == 1 else ('trace', trace))
        else:
            keys.append(('log_approximation', str(row[1])))
    return keys


def llm_signature(row):
    """Classify known semantics using static text. Unknown messages are hashed, never forwarded."""
    error = decode(row[4])
    nodes = []
    def visit(value, depth=0):
        if depth > 4 or not isinstance(value, dict):
            return
        nodes.append(value)
        for name in ('error', 'cause'):
            visit(value.get(name), depth+1)
    visit(error)
    message = next((v['message'] for v in reversed(nodes) if isinstance(v.get('message'), str)), '')
    if not message and isinstance(error, str):
        message = error
    lower = message.lower()
    status = next((v['status'] for v in nodes if isinstance(v.get('status'), int)
                   and not isinstance(v['status'], bool) and 100 <= v['status'] <= 599), None)
    code = next((safe_label(v.get('code')) for v in reversed(nodes) if v.get('code') is not None), '未记录')
    error_type = next((safe_label(v.get('type')) for v in nodes if v.get('type')), '未记录')
    stages = {'LLM response error', 'LLM response empty', 'LLM tool calls missing'}
    stage = row[8] if row[8] in stages else '其他错误阶段'
    family, core = 'unclassified', '未识别错误；仅依据状态码及元数据分析，不推断具体原因'
    network_codes = {'EPERM', 'ECONNREFUSED', 'ECONNRESET', 'ENOTFOUND', 'EAI_AGAIN', 'ETIMEDOUT'}
    if stage == 'LLM response empty':
        family, core = 'empty_response', '模型响应为空'
    elif stage == 'LLM tool calls missing':
        family, core = 'tool_result', '工具调用结果缺失'
    elif code in network_codes:
        family, core = 'network', '模型请求发生网络或系统连接错误'
    elif re.search(r'model.*(?:does not exist|not exist|no access|not found|无权限|不存在)', lower):
        family, core = 'model_access', '模型不存在或无访问权限，无法进一步区分'
    elif 'image' in lower and ('height' in lower or 'width' in lower):
        family, core = 'image_dimensions', '图片宽高不符合模型限制'
    elif 'image' in lower and ('format' in lower or 'unsupported' in lower):
        family, core = 'image_format', '图片格式或类型不支持'
    elif code in ('data_inspection_failed', 'content_filter'):
        family, core = 'content_safety', ('输出' if 'output' in lower else '输入')+'内容安全检查拦截'
    elif 'download' in lower or re.search(r'url.*timeout', lower):
        family, core = 'download', '远程内容下载'+('超时' if re.search(r'timeout|timed out', lower) else '失败')
    elif re.search(r'timeout|timed out', lower):
        family, core = 'timeout', '模型调用超时'
    elif code == 'invalid_json':
        family, core = 'request_json', '请求体无法解析为 JSON'
    elif re.search(r'contain.*json|messages.*json', lower) and code == 'invalid_parameter_error':
        family, core = 'json_mode', 'JSON 输出模式要求消息包含 JSON 提示'
    elif 'function' in lower and 'arguments' in lower and 'json' in lower:
        family, core = 'parameter_json', '工具调用参数 JSON 格式错误'
    elif '不支持' in message and '关闭' in message and '思考' in message:
        family, core = 'thinking', '模型不支持关闭思考'
    elif 'input' in lower and 'length' in lower:
        direction = '过长' if re.search(r'too long|exceed|maximum', lower) else '过短' if 'too short' in lower else '不符合范围'
        family, core = 'input_length', '输入长度'+direction
    elif status == 429:
        family, core = 'rate_limit', '上游请求受限或配额不足，需结合错误码判断'
    elif status in (401, 403):
        family, core = 'authentication', '上游认证或权限拒绝，具体原因未明'
    elif status in (500, 502, 503, 504):
        family, core = 'upstream_server', '上游服务错误，具体原因未明'
    signature = {'family': family, 'core': core, 'stage': stage,
                 'http_status': status, 'error_code': code, 'error_type': error_type}
    if family == 'unclassified':
        # 只忽略已知动态字段，未知核心语义仍区分指纹；原始错误、请求、headers、stack 不输出。
        unknown_text = lower or json.dumps(error, sort_keys=True, ensure_ascii=False).lower()
        normalized = re.sub(r'https?://[^\s\"<>]+', '<url>', unknown_text)
        normalized = re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{24,32}\b', '<id>', normalized)
        normalized = re.sub(r'(?i)\b(?:request|trace|span)[_-]?id\s*[:=]\s*[^\s,;]+', '<request-id>', normalized)
        signature['unknown_signature'] = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return signature


def dimension(counter):
    top = counter.most_common(MAX_DIMENSIONS)
    return {'top': [{'value': key, 'count': n} for key, n in top],
            'other_count': sum(counter.values())-sum(n for _, n in top), 'distinct': len(counter)}


def prepare_digest(folder, start, end):
    """Validate all raw rows, aggregate before AI, and retain bounded top groups plus exact long-tail totals."""
    path = folder/'signoz.json'
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise DigestError('SigNoz 原始数据格式无效') from None
    route_map = collections.defaultdict(set)
    for item in data.get('http_correlations', []):
        if not isinstance(item, list) or len(item) != 3 or not valid_trace(item[0]):
            raise DigestError('SigNoz 接口关联数据格式无效')
        route = safe_route(item[2] or item[1])
        if route != '接口未知':
            route_map[str(item[0]).lower()].add(route)
    def route_for(row, day):
        matches = route_map.get(str(row[2]).lower(), set()) if day == 'current' else set()
        return next(iter(matches)) if len(matches) == 1 else '接口未知'
    def clock(ns):
        return dt.datetime.fromtimestamp(ns/1e9, ZONE).isoformat(timespec='seconds')
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    def signature_for(kind, row, route):
        if kind == 'llm':
            signature = llm_signature(row)
            return signature, signature
        if kind == 'plugin':
            signature = {'family': 'plugin_not_found', 'core': 'Plugin not found',
                         'plugin': safe_label(row[4] or row[5] or row[6], '插件未知'),
                         'source': safe_label(row[7], '来源未知'),
                         'entry': 'Tool Detail Error' if row[10] == 'Tool Detail Error' else '入口未知'}
            return signature, signature
        issues = decode(row[4])
        issues = issues.get('issues', []) if isinstance(issues, dict) else issues
        normalized = set()
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                continue
            issue_path = issue.get('path', [])
            path = '.'.join(':index' if isinstance(v, int) else safe_label(v, ':key')
                            for v in (issue_path if isinstance(issue_path, list) else []) if isinstance(v, (str, int)))
            code = safe_label(issue.get('code'), 'unknown_validation')
            types = {'string', 'number', 'boolean', 'object', 'array', 'null', 'undefined', 'date', 'integer', 'unknown', 'any', 'never'}
            expected = issue.get('expected') if isinstance(issue.get('expected'), str) and issue.get('expected') in types else '未记录'
            received = issue.get('received') if isinstance(issue.get('received'), str) and issue.get('received') in types else '未记录'
            constraints = tuple((name, value) for name in ('minimum', 'maximum', 'inclusive', 'exact')
                                if isinstance((value := issue.get(name)), (int, float, bool)))
            normalized.add((code, path[:160], expected, received, constraints))
        fields = sorted(normalized)
        semantic = {'family': 'zod_validation', 'issues': fields}
        if not fields:
            semantic['unknown_signature'] = fingerprint(row[4])
        signature = {'family': 'zod_validation', 'core': '请求字段校验失败', 'route': route,
                     'issues': fields[:10], 'additional_issue_types': max(0, len(fields)-10)}
        return signature, semantic
    all_groups = {}
    totals = {}
    for day, left, right in [('current', start, end), ('previous', start-dt.timedelta(days=1), start)]:
        if set(data.get(day, {})) != set(KINDS):
            raise DigestError('SigNoz 三类主日志不完整')
        all_groups[day], totals[day] = {}, {}
        lo, hi = int(left.timestamp()*1000)*1_000_000, int(right.timestamp()*1000)*1_000_000
        for kind, columns in KINDS.items():
            item = data[day][kind]
            rows = item.get('rows')
            if (item.get('complete') is not True or not isinstance(rows, list)
                    or item.get('total') != len(rows)):
                raise DigestError('SigNoz 采集数量或完整性校验失败')
            if any(not isinstance(row, list) or len(row) != columns for row in rows):
                raise DigestError('SigNoz 日志列数校验失败')
            try:
                if len({row[1] for row in rows}) != len(rows) or any(not lo <= int(row[0]) < hi for row in rows):
                    raise DigestError('SigNoz 日志重复或超出统计范围')
            except (ValueError, TypeError):
                raise DigestError('SigNoz 日志标识或时间格式无效') from None
            groups = {}
            keys = request_keys(rows)
            for row, request_key in zip(rows, keys):
                route = route_for(row, day)
                signature, semantic = signature_for(kind, row, route)
                semantic_id = fingerprint(semantic)
                group_id = fingerprint([semantic_id, route]) if kind == 'zod' else semantic_id
                if group_id not in groups:
                    groups[group_id] = {'id': group_id, 'signature_id': semantic_id,
                        'signature': signature, 'count': 0, '_requests': set(), '_first': int(row[0]),
                        '_last': int(row[0]), '_models': collections.Counter(), '_providers': collections.Counter(),
                        '_routes': collections.Counter(), '_hours': collections.Counter(),
                        '_versions': collections.Counter(), 'missing_request_identifier_logs': 0, 'examples': []}
                group = groups[group_id]
                group['count'] += 1
                group['_requests'].add(request_key)
                group['_first'], group['_last'] = min(group['_first'], int(row[0])), max(group['_last'], int(row[0]))
                group['_routes'][route] += 1
                group['_hours'][clock(int(row[0]))[11:13]] += 1
                group['missing_request_identifier_logs'] += request_key[0] == 'log_approximation'
                example = {'message': signature['core'], 'route': route}
                if kind == 'llm':
                    model = safe_label(row[5] or row[6] or row[7], '模型未知')
                    provider = safe_label(row[9] or row[10])
                    group['_models'][model] += 1
                    group['_providers'][provider] += 1
                    example.update(model=model, provider=provider)
                elif kind == 'plugin':
                    group['_versions'][safe_label(decode(row[8]))] += 1
                    group['fallback_true_logs'] = group.get('fallback_true_logs', 0)+(decode(row[9]) is True)
                if len(group['examples']) < MAX_EXAMPLES and example not in group['examples']:
                    group['examples'].append(example)
            ordered = sorted(groups.values(), key=lambda g: (-g['count'], g['id']))
            if sum(g['count'] for g in ordered) != len(rows):
                raise DigestError('SigNoz 聚合计数不闭合')
            all_groups[day][kind] = ordered
            totals[day][kind] = len(rows)
    def export_group(group):
        exported = {k: v for k, v in group.items() if not k.startswith('_')}
        exported.update(requests_approx=len(group['_requests']), first=clock(group['_first']), last=clock(group['_last']),
                        models=dimension(group['_models']), providers=dimension(group['_providers']),
                        routes=dimension(group['_routes']), versions=dimension(group['_versions']),
                        hourly=dict(sorted(group['_hours'].items())))
        return exported
    previous = {kind: collections.Counter() for kind in KINDS}
    for kind, groups in all_groups['previous'].items():
        for group in groups:
            previous[kind][group['signature_id']] += group['count']
    for kind, groups in all_groups['current'].items():
        for group in groups:
            group['previous_count'] = previous[kind][group['signature_id']]
            group['comparison_basis'] = 'same_signature_all_routes' if kind == 'zod' else 'same_signature'
    limits = {(day, kind): min(MAX_GROUPS, len(groups)) for day, cats in all_groups.items() for kind, groups in cats.items()}
    digest = {'schema_version': 1, 'range': [start.isoformat(), end.isoformat()],
              'raw_snapshot_sha256': hashlib.sha256(raw).hexdigest(), 'totals': totals,
              'route_query_failures': data.get('route_query_failures', 0),
              'notes': ['次数为原始日志数量；影响请求按唯一 requestId/traceId 估算，无标识时按日志 ID 近似。',
                        '不同错误组的影响请求数可能重叠，不得相加当成总请求数。',
                        '样例仅含受控错误概述和允许的元数据，不包含原始消息、用户输入或请求内容。',
                        '前一日未采集接口关联，Zod 对比按错误签名汇总所有接口，不代表某接口新增。']}
    def render():
        for day, cats in all_groups.items():
            digest[day] = {}
            for kind, groups in cats.items():
                limit = limits[(day, kind)]
                tail = groups[limit:]
                requests = set().union(*(g['_requests'] for g in groups)) if groups else set()
                tail_requests = set().union(*(g['_requests'] for g in tail)) if tail else set()
                digest[day][kind] = {'total': totals[day][kind], 'group_count': len(groups),
                    'requests_approx': len(requests), 'groups': [export_group(g) for g in groups[:limit]],
                    'other': {'group_count': len(tail), 'count': sum(g['count'] for g in tail),
                              'requests_approx': len(tail_requests)},
                    'missing_request_identifier_logs': sum(g['missing_request_identifier_logs'] for g in groups)}
        return len((json.dumps(digest, ensure_ascii=False, indent=2)+'\n').encode())
    size = render()
    while size > MAX_DIGEST_BYTES:
        candidates = [key for key, limit in limits.items() if limit]
        if not candidates:
            raise DigestError('SigNoz 聚合摘要超过大小限制')
        key = min(candidates, key=lambda k: all_groups[k[0]][k[1]][limits[k]-1]['count'])
        limits[key] -= 1
        size = render()
    save(folder/'signoz-groups.json', {day: {kind: [export_group(g) for g in groups]
        for kind, groups in cats.items()} for day, cats in all_groups.items()})
    save(folder/'signoz-digest.json', digest)
    stats = {'schema_version': 1, 'raw_bytes': len(raw), 'digest_bytes': size,
             'raw_logs': sum(sum(c.values()) for c in totals.values()),
             'groups': sum(len(g) for cats in all_groups.values() for g in cats.values()),
             'model_groups': sum(limits.values()), 'count_closure': True,
             'raw_snapshot_sha256': digest['raw_snapshot_sha256']}
    save(folder/'signoz-digest-validation.json', stats)
    return stats
