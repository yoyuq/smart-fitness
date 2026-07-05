"""Sync fresh API keys from a source .env file into backend/.env.

Only touches lines starting with the KEY names we manage. Never prints raw keys.
Source path is taken from the SMART_FITNESS_KEY_SOURCE environment variable.

Usage:
    set SMART_FITNESS_KEY_SOURCE=D:\\path\\to\\rotated.env
    python scripts/diagnostics/sync_desktop_env_keys.py
"""
import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

src_env = os.environ.get("SMART_FITNESS_KEY_SOURCE")
if not src_env:
    raise SystemExit(
        "Set SMART_FITNESS_KEY_SOURCE to the path of the .env file that "
        "holds your rotated keys. This keeps the source path out of source control."
    )
SRC = Path(src_env)
DST = _ROOT / "backend" / ".env"

if not SRC.exists():
    raise SystemExit(f"source .env not found: {SRC}")

text = SRC.read_text(encoding="utf-8", errors="ignore")

# Accept simple KEY=VAL lines. No auto label parsing (kept locale-neutral).
def kv_lookup(name: str):
    m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    val = m.group(1).strip().strip('"').strip("'")
    return val or None


MANAGED = [
    "BAILIAN_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "VOLC_ARK_API_KEY",
    "ARK_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "HUNYUAN_API_KEY",
    "ZHIPU_API_KEY",
    "BIGMODEL_API_KEY",
]


keys = {name: kv_lookup(name) for name in MANAGED}
found = {k: v for k, v in keys.items() if v}
if not found:
    raise SystemExit("no managed keys found in source file. Nothing to do.")

# Read existing backend/.env (create if missing) and update or append.
dst_lines: list[str] = []
if DST.exists():
    dst_lines = DST.read_text(encoding="utf-8").splitlines()

def upsert(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


for key, value in found.items():
    dst_lines = upsert(dst_lines, key, value)

DST.parent.mkdir(parents=True, exist_ok=True)
DST.write_text("\n".join(dst_lines) + "\n", encoding="utf-8")

print(f"synced {len(found)} key(s) into {DST.relative_to(_ROOT)}: {sorted(found.keys())}")
