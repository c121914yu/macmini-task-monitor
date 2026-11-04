#!/usr/bin/env python3
"""每日流量检查入口：本机互斥和执行超时，不调用模型。"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    """相同时间只运行一次；仅终止本次脚本进程组，浏览器由子脚本负责关闭。"""
    os.umask(0o077)
    with (ROOT / "check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"event": "flowercloud_skipped", "summary": "流量检查正在运行"}), flush=True)
            return 0
        process = subprocess.Popen(
            ["/opt/homebrew/bin/node", str(ROOT / "run.mjs"), *sys.argv[1:]],
            cwd=ROOT, start_new_session=True)
        try:
            return process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            print(json.dumps({"event": "flowercloud_failed", "error": "检查超过 150 秒"}), flush=True)
            return 124


if __name__ == "__main__":
    sys.exit(main())
