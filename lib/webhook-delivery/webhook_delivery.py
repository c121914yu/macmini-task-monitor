"""所有 Mac mini 自动任务共用的飞书 webhook 传输：限流后 60 秒重试，最多 3 次。"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RETRY_SECONDS = 60
MAX_RETRIES = 3
LIMIT_CODES = frozenset((11232, 11233, 11247))


class DeliveryError(RuntimeError):
    """只携带脱敏后的状态和尝试记录，不把 URL、认证或消息正文带入错误日志。"""
    def __init__(self, status, attempts, *, code=None, http_status=None, error_type=None):
        self.status, self.attempts = status, attempts
        self.code, self.http_status, self.error_type = code, http_status, error_type
        super().__init__("Webhook delivery " + status + " (HTTP " + str(http_status) +
                         ", code " + str(code) + ")")


def save(path, data):
    """原子记录每次请求的发送意图和明确响应；不持久化 endpoint 或消息正文。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def send(url, payload, *, opener=None, sleeper=None, on_attempt=None, initial_attempts=0, not_before=None):
    """同一消息最多 4 次请求；仅明确限流可重发，未知传输结果立即交人工核查。"""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "open.feishu.cn" or
            not re.fullmatch(r"/open-apis/bot/v2/hook/[a-f0-9-]+", parsed.path) or
            parsed.query or parsed.fragment):
        raise ValueError("Invalid Feishu webhook endpoint")
    if type(initial_attempts) is not int or not 0 <= initial_attempts <= MAX_RETRIES + 1:
        raise ValueError("Invalid webhook attempt count")
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    notify = on_attempt or (lambda event: None)
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    attempts = []

    def record(event):
        if attempts and attempts[-1]["attempt"] == event["attempt"]:
            attempts[-1].update(event)
        else:
            attempts.append(dict(event))
        notify(dict(attempts[-1]))

    if initial_attempts and isinstance(not_before, (int, float)):
        remaining = max(0, min(RETRY_SECONDS, not_before - time.time()))
        if remaining:
            sleeper(remaining)
    if initial_attempts == MAX_RETRIES + 1:
        raise DeliveryError("rate_limited", [], code=11232)
    for number in range(initial_attempts + 1, MAX_RETRIES + 2):
        record({"attempt": number, "status": "sending", "started_at": time.time()})
        status, body = None, None
        try:
            try:
                with opener(request, timeout=20) as response:
                    value = getattr(response, "status", 200)
                    status = value if type(value) is int else 200
                    raw = response.read()
            except HTTPError as error:
                status = error.code
                try:
                    raw = error.read()
                finally:
                    error.close()
            try:
                body = json.loads(raw)
            except (ValueError, TypeError):
                # HTTP 429 即使没有合法 JSON，也明确表示服务端拒绝。
                if status != 429:
                    raise
        except Exception as error:
            record({"attempt": number, "status": "unknown", "completed_at": time.time(),
                    "http_status": status, "error_type": type(error).__name__})
            raise DeliveryError("unknown", attempts, http_status=status,
                                error_type=type(error).__name__) from None
        raw_code = body.get("code", body.get("StatusCode")) if isinstance(body, dict) else None
        code = raw_code if type(raw_code) is int else None
        success = 200 <= status < 300 and code == 0
        limited = not success and (status == 429 or code in LIMIT_CODES)
        if success:
            state = "sent"
        elif limited:
            state = "retry_wait" if number <= MAX_RETRIES else "rate_limited"
        elif (type(code) is int and code != 0) or 400 <= status < 500:
            state = "rejected"
        else:
            state = "unknown"
        event = {"attempt": number, "status": state, "http_status": status,
                 "code": code, "completed_at": time.time()}
        if state == "retry_wait":
            event.update({"retry_after_seconds": RETRY_SECONDS,
                          "next_attempt_at": time.time() + RETRY_SECONDS})
        record(event)
        if success:
            return {"status": "sent", "code": 0, "attempts": attempts}
        if state != "retry_wait":
            raise DeliveryError(state, attempts, code=code, http_status=status)
        sleeper(RETRY_SECONDS)


def main():
    """Node/脚本桥接：认证与 payload 只走标准输入；每次请求先落盘私有传输凭据。"""
    os.umask(0o077)
    incoming = json.loads(sys.stdin.read())
    url, payload = incoming["url"], incoming["payload"]
    receipt = incoming.get("journal_path")
    state = {"status": "sending", "started_at": time.time(), "attempts": [],
             "endpoint_sha256": hashlib.sha256(url.encode()).hexdigest()}
    initial = incoming.get("initial_attempts", 0)

    def progress(event):
        if state["attempts"] and state["attempts"][-1]["attempt"] == event["attempt"]:
            state["attempts"][-1].update(event)
        else:
            state["attempts"].append(event)
        state["status"] = event["status"]
        if receipt:
            save(receipt, state)

    try:
        result = send(url, payload, on_attempt=progress, initial_attempts=initial, not_before=incoming.get("not_before"))
        state.update({"status": "sent", "finished_at": time.time()})
        if receipt:
            save(receipt, state)
        print(json.dumps(result), flush=True)
    except DeliveryError as error:
        state.update({"status": error.status, "finished_at": time.time()})
        if receipt:
            save(receipt, state)
        print(json.dumps({"status": error.status, "code": error.code,
              "http_status": error.http_status, "attempts": error.attempts,
              "error_type": error.error_type}), flush=True)
        return 1
    except Exception:
        # 日志/JSON 无法确认时也不能把它作为服务端明确拒绝继续重试。
        print(json.dumps({"status": "unknown", "attempts": state["attempts"],
                          "error_type": "DeliveryExecutionError"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
