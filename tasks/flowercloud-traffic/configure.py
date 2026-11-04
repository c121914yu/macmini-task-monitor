"""仅从标准输入接收认证配置，不通过命令行参数或日志传递凭据。"""
from pathlib import Path
import json
import os
import sys

os.umask(0o077)
print("CONFIG_INPUT_READY", flush=True)
data = json.loads(sys.stdin.readline())
for field in ("username", "password", "webhook_url", "product_url"):
    if not isinstance(data.get(field), str) or not data[field]:
        raise SystemExit("Missing required configuration field")
path = Path(__file__).resolve().parent / "config.json"
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
temp.chmod(0o600)
temp.replace(path)
print("CONFIG_SAVED_PRIVATE", flush=True)
