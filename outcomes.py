"""从原任务的结构化输出解释业务结果，不执行或修改业务任务。"""

import json


def scan_result(event):
    """只有明确的 queued 列表才能判定是否有任务；缺失或异常数据不推断为空。"""
    if not isinstance(event, dict) or event.get('event') != 'scan' or not isinstance(event.get('queued'), list):
        return None
    count = len(event['queued'])
    if count == 0:
        return 'skipped', f'扫描 #{event.get("scan_id")} 未发现待执行任务，本次跳过'
    return 'success', f'扫描 #{event.get("scan_id")} 完成 · 等待队列 {count}'


def scan_result_from_log(text, scan_id=None):
    """读取最后一条扫描事件，可按扫描编号精确定位旧凭据；不依赖中文摘要。"""
    latest = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get('event') == 'scan' and (scan_id is None or str(event.get('scan_id')) == str(scan_id)):
            latest = event
    return scan_result(latest)


def docs_result(event):
    """仅解释明确的文档终态；达到重试上限仍归异常，不能显示为成功。"""
    if not isinstance(event, dict):
        return None
    name = event.get('event')
    if name == 'docs_completed':
        return 'success', f"升级文档已更新 · {event.get('version')} · PR #{event.get('pr')}" if event.get('pr') else '升级文档已核对，无内容差异'
    if name in ('docs_idle', 'docs_already_running', 'docs_waiting_version'):
        return 'skipped', {'docs_idle': '没有新的合入任务，本次跳过',
                           'docs_already_running': '文档任务正在运行，本次跳过',
                           'docs_waiting_version': '版本已发布，等待指定下一版'}[name]
    if name in ('docs_retry_limit', 'docs_error'):
        return 'failed', '文档任务达到重试上限，等待处理' if name == 'docs_retry_limit' else '升级文档处理异常'
    return None
