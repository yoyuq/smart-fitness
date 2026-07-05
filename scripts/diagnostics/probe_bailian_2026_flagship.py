"""Probe 2026 flagship models on Aliyun Bailian.

Reads keys from environment (BAILIAN_API_KEY / DASHSCOPE_API_KEY). Falls back
to reading a KEY=VAL formatted .env file at BAILIAN_ENV_FILE if that variable
is set. Never prints raw key values.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


env_file_hint = os.environ.get("BAILIAN_ENV_FILE")
if env_file_hint:
    _load_env_file(Path(env_file_hint))

# Default backend .env if it exists (relative to this script).
_ROOT = Path(__file__).resolve().parents[2]
_load_env_file(_ROOT / "backend" / ".env")

K_QWEN = os.environ.get("BAILIAN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")

if not K_QWEN:
    raise SystemExit(
        "Missing BAILIAN_API_KEY / DASHSCOPE_API_KEY. "
        "Export the key or set BAILIAN_ENV_FILE to a .env with the key."
    )

BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

CANDIDATES = [
    "qwen3.7-max",
    "kimi-k2.7-code",
    "deepseek-v4-pro",
    "qwen3.6-flash",
    "deepseek-v4-flash",
    "glm-5.2",
]


def ask(model: str) -> None:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE,
        data=body,
        headers={
            "Authorization": f"Bearer {K_QWEN}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        dt = time.time() - t0
        finish = data.get("choices", [{}])[0].get("finish_reason", "?")
        print(f"  OK  {model:25s}  finish={finish}  {dt*1000:.0f} ms")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"  {e.code}  {model:25s}  {body}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERR {model:25s}  {e}")


for m in CANDIDATES:
    ask(m)
