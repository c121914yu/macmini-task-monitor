"""本机任务注册表；只暴露展示所需字段，不向网页传递环境变量或凭据。"""

import datetime as dt
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TASK_MONITOR_DATA', ROOT / 'data')).resolve()
DB_PATH = DATA / 'monitor.sqlite3'
ZONE = ZoneInfo('Asia/Shanghai')
PR_ROOT = Path('/Users/yjl/.codex/experiments/pr-review-client-probe')
BACKUP_ROOT = Path('/Users/yjl/.local/share/fastgpt-backup')
METASO_ROOT = Path('/Users/yjl/.codex/automations/metaso-balance')
AGENTS = Path('/Users/yjl/Library/LaunchAgents')

TASKS = [
    {'id': 'flowercloud-traffic', 'name': 'FlowerCloud 流量日报', 'icon': 'chart', 'category': 'work',
     'source': 'launchd', 'label': 'com.yjl.flowercloud-traffic',
     'description': '每天北京时间 09:00，独立无头 Chrome 读取已用/总流量并推送飞书。支持锁屏，无模型调用；同日通知去重。',
     'script': '/Users/yjl/.local/share/flowercloud-traffic/run_check.py',
     'logs': ['task.log', 'task.stderr.log'], 'log_root': '/Users/yjl/.local/share/flowercloud-traffic'},
    {'id': 'pr-scan', 'name': 'PR 扫描', 'icon': 'radar', 'category': 'work',
     'label': 'com.yjl.fastgpt-codex-pr-review-poller', 'source': 'launchd',
     'description': '发现 FastGPT PR、刷新队列与 PR 状态，并补偿未发送的审查日报。扫描本身不调用模型。',
     'script': str(PR_ROOT / 'scan_prs.py'), 'logs': ['pr-poll.log', 'pr-poll.stderr.log'],
     'log_root': str(PR_ROOT)},
    {'id': 'pr-worker', 'name': 'PR 审查执行器', 'icon': 'workflow', 'category': 'work',
     'label': 'com.yjl.fastgpt-codex-review-worker', 'source': 'launchd',
     'description': '按 FIFO 执行审查，最多并发 2 个任务。新提交静置 10 分钟；运行中每 15 秒检查代码变更。执行阶段会调用 AI。',
     'script': str(PR_ROOT / 'scan_prs.py') + ' --worker',
     'logs': ['review-worker.log', 'review-worker.stderr.log'], 'log_root': str(PR_ROOT)},
    {'id': 'release-docs', 'name': '升级文档自动更新', 'icon': 'report', 'category': 'work',
     'label': 'com.yjl.fastgpt-release-docs', 'source': 'launchd',
     'description': '每小时检查 main 新合入 PR；有变化才调用 Codex 更新进行中版本中英文文档。固定 worktree，复用未合入 PR；版本发布后等待指定下一版。',
     'script': '/Users/yjl/.local/share/fastgpt-release-docs/release_docs.py',
     'logs': ['task.log', 'task.stderr.log'], 'log_root': '/Users/yjl/.local/share/fastgpt-release-docs'},
    {'id': 'pr-report', 'name': 'PR 审查日报', 'icon': 'report', 'category': 'work',
     'label': 'com.yjl.fastgpt-codex-review-daily-report', 'source': 'launchd',
     'description': '每天汇总昨日审查活动、累计 Token 和 PR 合并状态，发送飞书日报。同日发送凭据防止重复通知。',
     'script': str(PR_ROOT / 'daily_review_report.py') + ' --send',
     'logs': ['daily-report.log', 'daily-report.stderr.log'], 'log_root': str(PR_ROOT)},
    {'id': 'fastgpt-backup', 'name': 'FastGPT 数据备份', 'icon': 'database', 'category': 'work',
     'label': 'com.yjl.fastgpt-daily-backup', 'source': 'launchd',
     'description': '备份 MongoDB、PostgreSQL、MinIO，完成压缩流和 SHA256 校验后发布备份；保留最近 7 个自然日。',
     'script': str(BACKUP_ROOT / 'backup.py'), 'logs': ['launchd.log'], 'log_root': str(BACKUP_ROOT)},
    {'id': 'com.yjl.weekly-progress-reminder', 'name': '每周进度提醒', 'icon': 'clock', 'category': 'work',
     'label': 'com.yjl.weekly-progress-reminder', 'source': 'launchd',
     'description': '每周一北京时间 10:00，通过飞书 webhook 提醒全员更新需求状态与进度；11:30 前完成，设计和测试核对 P0 需求覆盖。',
     'script': '/Users/yjl/.local/share/weekly-progress-reminder/remind.py',
     'logs': ['task.log', 'task.stderr.log'], 'log_root': '/Users/yjl/.local/share/weekly-progress-reminder'},
    {'id': 'metaso-balance', 'name': '秘塔搜索余额', 'icon': 'wallet', 'category': 'work',
     'source': 'codex-script', 'label': None,
     'description': '查询秘塔搜索 API 剩余积分，低于 1000 点时发送飞书告警。业务逻辑已是纯脚本，目前由 Codex 定时触发。',
     'script': str(METASO_ROOT / 'check_balance.sh'), 'logs': ['memory.md'], 'log_root': str(METASO_ROOT)}
]

SERVICES = [
    {'id': 'opencodex', 'name': 'OpenCodex 网关', 'label': 'com.yjl.opencodex-gateway-auth', 'domain': 'gui', 'description': '本地模型网关与认证桥接'},
    {'id': 'cloudflared', 'name': 'Cloudflare Tunnel', 'label': 'com.cloudflare.cloudflared', 'domain': 'system', 'description': 'Cloudflare 常驻隧道'}
]


def redact(value):
    """在入库和 HTTP 输出两处脱敏；不修改原任务的标准输出及原始日志。"""
    text = str(value)
    text = re.sub(r'https://(?:open\.)?(?:feishu|larksuite)\.cn/open-apis/bot/[^\s\"\'<>]+', '[飞书 Webhook 已隐藏]', text, flags=re.I)
    text = re.sub(r'(?i)(Bearer\s+)[\w.\-]+', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{15,}|github_pat_[\w]+)\b', '[REDACTED]', text)
    text = re.sub(r'(?i)([\"\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)[\"\']?\s*[:=]\s*)[\"\']?[^\s,\"\'}]+[\"\']?', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)([?&](?:key|token|secret|api_key)=)[^&\s]+', r'\1[REDACTED]', text)
    return text


def timestamp(value):
    """接受现有清单里的 ISO 时间或 Unix 秒；缺失时间保持缺失。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return dt.datetime.fromisoformat(str(value)).timestamp()
