"""Patch vision_pipeline.py to install 2026 bailian flagship chain.

Idempotent: if a marker exists, does nothing.

Path is resolved relative to this script:
    scripts/diagnostics/patch_bailian_2026_chain.py -> backend/fitness_agent/vision_pipeline.py
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
P = _ROOT / "backend" / "fitness_agent" / "vision_pipeline.py"
src = P.read_text(encoding="utf-8")

MARK = "bailian-deepseek-v4-pro"
if MARK in src:
    print("already patched, skip")
    raise SystemExit(0)

# Insert the 2026 flagship providers before the volc-coding block.
# Locate the current volc-coding lead-in and prepend new provider entries.
lead_pattern = re.compile(
    r'(_TEXT_PROVIDER_CATALOG:\s*List\[Dict\[str,\s*Any\]\]\s*=\s*\[\s*\n)'
    r'(\s*#[^\n]*\n)*\s*\{\s*"provider":\s*"volc-coding",',
    re.DOTALL,
)
match = lead_pattern.search(src)
if not match:
    raise SystemExit("could not locate _TEXT_PROVIDER_CATALOG lead-in")

flagship_block = '''    # Aliyun Bailian 2026 flagship reasoning models (primary path).
    # Enable by setting BAILIAN_API_KEY (or DASHSCOPE_API_KEY).
    {
        "provider": "bailian-qwen3-7-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-max",
        "env": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
        "max_tokens": 6000,
    },
    {
        "provider": "bailian-kimi-k2-7-code",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "kimi-k2.7-code",
        "env": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
        "max_tokens": 6000,
    },
    {
        "provider": "bailian-deepseek-v4-pro",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "deepseek-v4-pro",
        "env": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
        "max_tokens": 6000,
    },
    {
        "provider": "bailian-qwen3-6-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.6-flash",
        "env": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
    },
    {
        "provider": "bailian-deepseek-v4-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "deepseek-v4-flash",
        "env": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
    },
'''

insert_at = match.start(0) + len(match.group(1))
new_src = src[:insert_at] + flagship_block + src[insert_at:]
P.write_text(new_src, encoding="utf-8")
print(f"patched: {P} (+{len(flagship_block)} bytes)")
