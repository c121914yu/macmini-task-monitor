"""模拟 HTTP 响应和等待，验证共享重试上限与未知结果保护，不发送真实消息。"""
import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
import webhook_delivery as delivery

URL = "https://open.feishu.cn/open-apis/bot/v2/hook/00000000-0000-0000-0000-000000000000"


class Response:
    def __init__(self, code=0, status=200, raw=None):
        self.status = status
        self.raw = json.dumps({"code": code}).encode() if raw is None else raw
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.raw


class DeliveryTests(unittest.TestCase):
    def call(self, sequence, initial=0):
        opener, sleeper, events = Mock(side_effect=sequence), Mock(), []
        try:
            result = delivery.send(URL, {"same": "payload"}, opener=opener, sleeper=sleeper,
                                   on_attempt=events.append, initial_attempts=initial)
        except delivery.DeliveryError as error:
            result = error
        return result, opener, sleeper, events

    def test_all_business_rate_limits_wait_exactly_one_minute_then_succeed(self):
        for code in (11232,11233,11247):
            with self.subTest(code=code):
                result, opener, sleep, events = self.call([Response(code), Response()])
                self.assertEqual(result["status"], "sent")
                self.assertEqual(opener.call_count, 2)
                sleep.assert_called_once_with(60)
                self.assertEqual([e["status"] for e in events],
                                 ["sending","retry_wait","sending","sent"])
                self.assertEqual(opener.call_args_list[0].args[0].data,
                                 opener.call_args_list[1].args[0].data)

    def test_limit_exhaustion_makes_four_calls_and_three_sleeps_only(self):
        result, opener, sleep, _ = self.call([Response(11232)] * 5)
        self.assertEqual(result.status, "rate_limited")
        self.assertEqual(opener.call_count, 4)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [60,60,60])

    def test_http_429_without_json_is_also_a_definite_rate_limit(self):
        error=HTTPError(URL,429,"limited",{},io.BytesIO(b"not-json"))
        result, opener, sleep, _ = self.call([error,Response()])
        self.assertEqual(result["status"],"sent");sleep.assert_called_once_with(60)

    def test_timeout_or_invalid_success_response_never_retries(self):
        for item in (TimeoutError(),Response(raw=b"invalid"),Response(code=0,status=500),Response(code=[])):
            with self.subTest(item=item):
                result, opener, sleep, _ = self.call([item,Response()])
                self.assertEqual(result.status,"unknown")
                self.assertEqual(opener.call_count,1);sleep.assert_not_called()

    def test_non_rate_rejection_does_not_retry(self):
        result, opener, sleep, _ = self.call([Response(19024),Response()])
        self.assertEqual(result.status,"rejected")
        self.assertEqual(opener.call_count,1);sleep.assert_not_called()

    def test_prior_attempts_count_toward_the_same_message_limit(self):
        result, opener, sleep, events = self.call([Response(11232)] * 4, initial=1)
        self.assertEqual(result.status,"rate_limited")
        self.assertEqual(opener.call_count,3)
        self.assertEqual([x["attempt"] for x in events if x["status"]=="sending"],[2,3,4])
        self.assertEqual(sleep.call_count,2)

    def test_exhausted_message_cannot_restart_a_new_retry_budget(self):
        result, opener, sleep, _ = self.call([], initial=4)
        self.assertEqual(result.status,"rate_limited")
        opener.assert_not_called();sleep.assert_not_called()

    def test_unknown_after_rate_limit_stops_without_another_post(self):
        result, opener, sleep, _ = self.call([Response(11232),TimeoutError(),Response()])
        self.assertEqual(result.status,"unknown")
        self.assertEqual(opener.call_count,2);sleep.assert_called_once_with(60)


if __name__ == "__main__":unittest.main()
