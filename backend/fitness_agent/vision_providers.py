"""Multi-vendor vision provider registry with automatic fallback.

Design: all supported providers are OpenAI-compatible ``chat/completions``
endpoints, so we just swap base URL + api key + model id. The registry ranks
providers by (accuracy_score, latency_score) discovered via live bake-off; the
Agent uses the first available one and falls back on HTTP error / timeout.

Configured via env:
- ``DASHSCOPE_API_KEY`` / ``QWEN_API_KEY``      → Alibaba Bailian (Qwen-VL)
- ``VOLC_ARK_API_KEY`` / ``ARK_API_KEY``        → Volcengine Ark (Doubao vision)
- ``ZHIPU_API_KEY`` / ``BIGMODEL_API_KEY``      → Zhipu (GLM-4V)
- ``MOONSHOT_API_KEY`` / ``KIMI_API_KEY``       → Moonshot (Kimi vision)
- ``HUNYUAN_API_KEY`` / ``TENCENT_HUNYUAN_API_KEY``  → Tencent Hunyuan vision

Override the priority order with ``AI_AGENT_VISION_PROVIDERS`` (comma-separated
provider ids, e.g. ``qwen,volcengine,zhipu``). Pin a single model with
``AI_AGENT_VISION_MODEL`` (uses the first configured provider whose default
model matches; else uses the first configured provider with that model id).
"""
import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("fitness_agent.vision_providers")


# ranked by bake-off on rep 841 深蹲底部 (2026-07-03):
# qwen-vl-max — 9.7s, 3 alignment_cues (butt_wink + knee_over_toe + heels_lifted), 6 angles
# doubao-seed-1-6-vision — 52s, 2 cues (butt_wink + knee_over_toe), 0 angles
# doubao-1-5-vision-pro — 4.5s, 0 cues, 0 angles (baseline)
# glm-4.6v — 19.6s, 1 cue (knee_caving_in), 0 angles
# kimi moonshot-v1-8k — 4.5s, 1 cue (knee_over_toe), 0 angles
# hunyuan-standard-vision — 7.2s, 1 cue (knee_over_toe), 3 angles


ProviderCandidate = Tuple[str, str, str, List[str]]
# (provider_id, base_url, model_id, api_key_env_names)


_PROVIDER_CATALOG: List[ProviderCandidate] = [
    (
        "qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-vl-max",
        ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    ),
    # qwen-vl-plus / qwen3-vl-plus share the same key; treat as separate
    # "providers" so fallback re-tries same vendor with cheaper/faster models
    # before crossing to volcengine. Bake-off 2026-07-03 showed qwen-vl-plus
    # drops depth judgment but keeps angles + speed; qwen3-vl-plus is the
    # middle option.
    (
        "qwen-fast",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen3-vl-plus-2025-12-19",
        ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    ),
    (
        "qwen-cheap",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-vl-plus",
        ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    ),
    (
        "volcengine",
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "doubao-seed-1-6-vision-250815",
        ["VOLC_ARK_API_KEY", "ARK_API_KEY"],
    ),
    (
        "zhipu",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "glm-4.6v",
        ["ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "BIGMODEL_API_KEY", "GLM_API_KEY"],
    ),
    (
        "moonshot",
        "https://api.moonshot.cn/v1/chat/completions",
        "moonshot-v1-8k-vision-preview",
        ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
    ),
    (
        "hunyuan",
        "https://api.hunyuan.cloud.tencent.com/v1/chat/completions",
        "hunyuan-standard-vision",
        ["HUNYUAN_API_KEY", "TENCENT_HUNYUAN_API_KEY"],
    ),
    # Volcengine v1.5 as ultra-fast emergency fallback (baseline).
    (
        "volcengine-legacy",
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "doubao-1-5-vision-pro-32k-250115",
        ["VOLC_ARK_API_KEY", "ARK_API_KEY"],
    ),
]


def _first_key(env_names: List[str]) -> str:
    for k in env_names:
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return ""


def available_providers() -> List[Dict[str, str]]:
    """Return the ranked list of currently-configured providers (has API key).

    Priority override via ``AI_AGENT_VISION_PROVIDERS`` (comma-separated ids).
    """
    order_env = os.environ.get("AI_AGENT_VISION_PROVIDERS", "").strip()
    catalog = list(_PROVIDER_CATALOG)
    if order_env:
        wanted = [p.strip() for p in order_env.split(",") if p.strip()]
        indexed = {p[0]: p for p in catalog}
        catalog = [indexed[w] for w in wanted if w in indexed]
    # optional single-model pin
    pin_model = os.environ.get("AI_AGENT_VISION_MODEL", "").strip()
    result: List[Dict[str, str]] = []
    for pid, url, model, key_names in catalog:
        key = _first_key(key_names)
        if not key:
            continue
        effective_model = pin_model if pin_model else model
        result.append({
            "provider": pid,
            "url": url,
            "model": effective_model,
            "api_key": key,
        })
    return result


def summarize_config() -> Dict[str, object]:
    """Diagnostic snapshot of provider availability without leaking keys."""
    out = []
    for pid, url, model, key_names in _PROVIDER_CATALOG:
        key = _first_key(key_names)
        out.append({
            "provider": pid,
            "url": url,
            "default_model": model,
            "configured": bool(key),
            "key_env": next((k for k in key_names if os.environ.get(k, "").strip()), None),
        })
    return {
        "priority_override": os.environ.get("AI_AGENT_VISION_PROVIDERS") or None,
        "model_pin": os.environ.get("AI_AGENT_VISION_MODEL") or None,
        "providers": out,
    }
