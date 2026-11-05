"""临时凭据与模拟请求验证真实 Python 调用方的重试去重，禁止真实消息。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, "/Users/yjl/.codex/experiments/pr-review-client-probe")
import feishu_notify as notify
import webhook_delivery as common
from test_webhook_delivery import Response


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / "config.json"
        config.write_text(json.dumps({"feishu_webhook":
            "https://open.feishu.cn/open-apis/bot/v2/hook/00000000-0000-0000-0000-000000000000"}))
        self.enterContext(patch.object(notify, "CONFIG_PATH", config))
        self.sleep = self.enterContext(patch.object(common.time, "sleep"))
        self.delivery = {"path": self.root / "receipt.json"}

    def test_limited_then_sent_is_deduplicated(self):
        with patch.object(notify, "urlopen", side_effect=[Response(11232), Response()]) as post:
            self.assertEqual(notify.send_webhook({}, self.delivery)["event"], "notification_sent")
            self.assertEqual(notify.send_webhook({}, self.delivery)["event"], "notification_already_sent")
            self.assertEqual(post.call_count, 2)
            self.sleep.assert_called_once_with(60)

    def test_compensation_does_not_reset_exhausted_budget(self):
        with patch.object(notify, "urlopen", side_effect=[Response(11232)] * 5) as post:
            with self.assertRaises(RuntimeError):
                notify.send_webhook({}, self.delivery)
            self.assertEqual(notify.send_webhook({}, self.delivery)["event"], "notification_retry_exhausted")
            self.assertEqual(post.call_count, 4)
            self.assertEqual(self.sleep.call_count, 3)

    def test_timeout_after_limit_keeps_unknown_and_never_resends(self):
        with patch.object(notify, "urlopen", side_effect=[Response(11232), TimeoutError(), Response()]) as post:
            with self.assertRaises(RuntimeError):
                notify.send_webhook({}, self.delivery)
            self.assertEqual(notify.send_webhook({}, self.delivery)["event"], "notification_needs_inspection")
            self.assertEqual(post.call_count, 2)

    def test_recovered_wait_sleeps_only_remaining_time(self):
        self.delivery["path"].write_text(json.dumps({"status": "retry_wait", "code": 11232,
            "attempts": [{"attempt": 1, "next_attempt_at": 130}]}))
        with patch.object(common.time, "time", return_value=100), patch.object(notify, "urlopen", return_value=Response()):
            self.assertEqual(notify.send_webhook({}, self.delivery)["event"], "notification_sent")
            self.sleep.assert_called_once_with(30)


if __name__ == "__main__": unittest.main()
