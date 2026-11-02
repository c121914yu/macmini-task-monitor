#!/bin/bash
# 本机卸载 Cloudflare Tunnel；仅服务及系统日志移动需要管理员权限。
# 不访问 Cloudflare 云端，不输出 tunnel token，不删除监控执行历史。
set -euo pipefail
umask 077

TASK_APP=/Users/yjl/.local/share/macmini-task-monitor
TASK_USER=yjl
TASK_PYTHON=/opt/homebrew/bin/python3
TASK_LABEL=com.cloudflare.cloudflared

if [[ "${1:-}" == '--user-cleanup' ]]; then
  if [[ "$EUID" -eq 0 ]]; then
    echo '用户清理阶段不能以 root 运行。' >&2
    exit 1
  fi
  TASK_BACKUP="$2"

  # 服务停止后才卸载可执行文件，避免 KeepAlive 反复启动一个不存在的入口。
  if HOMEBREW_NO_AUTO_UPDATE=1 /opt/homebrew/bin/brew list --versions cloudflared >/dev/null 2>&1; then
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 /opt/homebrew/bin/brew uninstall cloudflared
  fi
  if [[ -d /Users/yjl/.cloudflared ]]; then
    /bin/mv /Users/yjl/.cloudflared "$TASK_BACKUP/user-cloudflared"
  fi

  cd "$TASK_APP"
  "$TASK_PYTHON" - <<'PY'
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import install
import store

# 真正卸载服务后再移除展示项，避免把仍在运行的隧道误报为已经移除。
path = Path('config.py')
source = path.read_text()
updated = ''.join(line for line in source.splitlines(keepends=True)
                  if not line.lstrip().startswith("{'id': 'cloudflared'"))
compile(updated, str(path), 'exec')
if updated != source:
    path.write_text(updated)

# 只重载监控进程，不重载四个业务定时任务；沿用当前监听地址和端口。
configuration = store.get_meta('server_configuration', {'bind': ['127.0.0.1'], 'port': 8765})
options = argparse.Namespace(bind=configuration['bind'], port=configuration['port'], allowed_host=[])
install.install_service(options)
url = 'http://127.0.0.1:' + str(options.port)
for attempt in range(30):
    try:
        with urllib.request.urlopen(url + '/api/overview', timeout=3) as response:
            overview = json.load(response)
        if any(service['id'] == 'cloudflared' for service in overview['collector']['services']):
            raise RuntimeError('网页仍包含 CF Tunnel')
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(1)
result = subprocess.run(['pgrep', '-x', 'cloudflared'], capture_output=True, text=True)
if result.returncode == 0:
    raise RuntimeError('仍有 cloudflared 进程，需要进一步检查')
print('Cloudflare Tunnel 已从本机卸载，监控网页已更新。')
PY
  echo "原服务定义和日志已保留在：$TASK_BACKUP"
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  echo '请使用 sudo /bin/bash /Users/yjl/.local/share/macmini-task-monitor/uninstall-cloudflare.sh' >&2
  exit 1
fi

# 卸载系统级启动项，确认进程退出后再移动文件。
if /bin/launchctl print "system/$TASK_LABEL" >/dev/null 2>&1; then
  /bin/launchctl bootout "system/$TASK_LABEL"
fi
for TASK_ATTEMPT in {1..15}; do
  if ! /usr/bin/pgrep -x cloudflared >/dev/null; then
    break
  fi
  /bin/sleep 1
done
if /usr/bin/pgrep -x cloudflared >/dev/null; then
  echo 'cloudflared 尚未退出，停止清理，未卸载可执行文件。' >&2
  exit 1
fi

TASK_BACKUP=$(/usr/bin/mktemp -d /Users/yjl/.Trash/CloudflareTunnel-uninstall.XXXXXX)
/usr/sbin/chown 501:20 "$TASK_BACKUP"
for TASK_PATH in \
  /Library/LaunchDaemons/com.cloudflare.cloudflared.plist \
  /Library/Logs/com.cloudflare.cloudflared.out.log \
  /Library/Logs/com.cloudflare.cloudflared.err.log; do
  if [[ -f "$TASK_PATH" ]]; then
    TASK_DEST="$TASK_BACKUP/$(/usr/bin/basename "$TASK_PATH")"
    /bin/mv "$TASK_PATH" "$TASK_DEST"
    /usr/sbin/chown 501:20 "$TASK_DEST"
    /bin/chmod 600 "$TASK_DEST"
  fi
done

# 后续 Homebrew 和监控更新降回原用户权限执行。
/usr/bin/sudo -u "$TASK_USER" -H /bin/bash "$TASK_APP/uninstall-cloudflare.sh" --user-cleanup "$TASK_BACKUP"
