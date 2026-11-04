#!/usr/bin/env python3
"""注册每日北京时间 09:00 的纯脚本任务；不触发模型或其他业务。"""
from pathlib import Path
import json
import os
import plistlib
import subprocess

ROOT = Path(__file__).resolve().parent
LABEL = "com.yjl.flowercloud-traffic"
MONITOR = Path("/Users/yjl/.local/share/macmini-task-monitor")
PYTHON = "/opt/homebrew/bin/python3"


def main():
    """检查私有配置和时区后注册 LaunchAgent；运行中的同名任务不强制停止。"""
    if "Asia/Shanghai" not in str(Path("/etc/localtime").resolve()):
        raise SystemExit("launchd 按主机时区调度，需要确认主机为 Asia/Shanghai")
    config = ROOT / "config.json"
    if not config.is_file() or config.stat().st_mode & 0o077:
        raise SystemExit("请先创建权限为 600 的 config.json")
    domain = "gui/" + str(os.getuid())
    existing = subprocess.run(["launchctl", "print", domain + "/" + LABEL],
                              capture_output=True, text=True)
    if "\n\tpid = " in existing.stdout:
        raise SystemExit("任务正在运行，保留当前进程")
    definition = {
        "Label": LABEL,
        "ProgramArguments": [PYTHON, str(MONITOR / "runner.py"), "--task-id",
                            "flowercloud-traffic", "--", PYTHON, str(ROOT / "run_check.py")],
        "WorkingDirectory": str(ROOT), "RunAtLoad": False,
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "ProcessType": "Background", "Umask": 0o077,
        "StandardOutPath": str(ROOT / "task.log"),
        "StandardErrorPath": str(ROOT / "task.stderr.log"),
        "EnvironmentVariables": {"HOME": str(Path.home()), "TZ": "Asia/Shanghai",
            "LANG": "en_US.UTF-8", "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    previous = path.read_bytes() if path.exists() else None
    temp = path.with_suffix(".tmp")
    temp.write_bytes(plistlib.dumps(definition))
    temp.chmod(0o600)
    temp.replace(path)
    if existing.returncode == 0:
        subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        if previous is not None:
            path.write_bytes(previous)
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        raise SystemExit("任务注册失败：" + result.stderr[:300])
    print(json.dumps({"event": "flowercloud_installed", "schedule": "每天北京时间 09:00",
                      "label": LABEL}, ensure_ascii=False))


if __name__ == "__main__":
    main()
