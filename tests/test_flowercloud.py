"""临时 SQLite 加模拟子进程验证流量任务在看板上的真实结果，不访问网站或飞书。"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FlowercloudMonitorTests(unittest.TestCase):
    def check(self, event, code=0):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "TASK_MONITOR_DATA": folder}
            command = "import sys;print(" + repr(json.dumps(event, ensure_ascii=False)) + ");sys.exit(" + str(code) + ")"
            result = subprocess.run([sys.executable, str(ROOT / "runner.py"),
                "--task-id", "flowercloud-traffic", "--", sys.executable, "-c", command],
                env=env, capture_output=True, text=True, timeout=15)
            with sqlite3.connect(Path(folder) / "monitor.sqlite3") as db:
                row = db.execute("SELECT status, summary, exit_code FROM runs").fetchone()
            return result, row

    def test_success_keeps_actual_usage_summary(self):
        result, row = self.check({"event": "flowercloud_finished", "summary": "已用 15.83 GB / 150 GB，飞书已发送"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(row[0], "success")
        self.assertIn("15.83 GB", row[1])

    def test_daily_duplicate_is_skipped(self):
        _, row = self.check({"event": "flowercloud_skipped", "summary": "当天已有发送凭据"})
        self.assertEqual(row[0], "skipped")
        self.assertIn("发送凭据", row[1])

    def test_nonzero_exit_never_becomes_success(self):
        result, row = self.check({"event": "flowercloud_failed", "error": "login failed"}, 7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(row[0], "failed")
        self.assertEqual(row[2], 7)
