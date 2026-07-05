"""Volcengine Ark multimodal vision helper for the Smart Fitness Agent.

Small, controlled wrapper around the OpenAI-compatible ``chat/completions``
endpoint exposed by 火山方舟. It only accepts an image path/URL/base64 that the
Agent has already retrieved from the pose pipeline (rep_frames / rep_clips) or a
public image URL provided by the user. It does not accept arbitrary local paths
outside the configured whitelist.

Returned analysis is treated as *observation, not medical/coaching authority*:
callers should always combine the qualitative comment with the rule-based
FORM_RULES score and cite ``search_fitness_kb`` for scientific basis.
"""
import base64
import json
import logging
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("fitness_agent.vision")

_VOLC_ARK_URL = os.environ.get(
    "VOLC_ARK_VISION_URL",
    "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
)


def _api_key() -> str:
    return os.environ.get("VOLC_ARK_API_KEY", "") or os.environ.get("ARK_API_KEY", "")


def _vision_model() -> str:
    # Users can override to any 火山方舟 vision-capable model.
    # doubao-seed-1-6-vision-250815 is the strongest Seed 1.6 vision model as of 2025-08,
    # detects subtle alignment cues (e.g. butt_wink) that older vision-pro missed.
    # If cost/latency is a concern, fall back to doubao-1-5-vision-pro-32k-250115.
    return os.environ.get(
        "AI_AGENT_VISION_MODEL",
        os.environ.get("VOLC_VISION_MODEL", "doubao-seed-1-6-vision-250815"),
    )


def _default_frame_roots() -> List[str]:
    """Directories where rep frames / clips are stored.

    Only files under these roots are accepted as image_path arguments. The user
    can extend with AI_AGENT_VISION_FRAME_ROOTS (';'-separated absolute paths).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.abspath(os.path.join(here, ".."))
    roots = [
        os.path.join(backend_dir, "data", "rep_frames"),
        os.path.join(backend_dir, "data", "rep_clips"),
        os.path.join(backend_dir, "data", "monitor"),
        # collages are derived by _make_collage() from rep_clips frames; whitelist so
        # session-level two-stage analysis (frames_per_rep > 1) can hand the stitched
        # image back to Stage 1 vision providers without "not inside allowed roots".
        os.path.join(backend_dir, "data", "collages"),
    ]
    extra = os.environ.get("AI_AGENT_VISION_FRAME_ROOTS", "")
    for p in extra.split(os.pathsep):
        p = p.strip()
        if p:
            roots.append(p)
    return [os.path.abspath(r) for r in roots]


def _is_inside_root(path: str, root: str) -> bool:
    try:
        p = os.path.abspath(path)
        r = os.path.abspath(root)
        return os.path.commonpath([p, r]) == r
    except Exception:
        return False


def _load_image_data_uri(image_path: str, max_bytes: int = 4 * 1024 * 1024) -> Dict[str, Any]:
    """Read image file, base64-encode, wrap as data URI. Enforces root whitelist."""
    if not image_path:
        return {"ok": False, "error": "image_path required"}
    roots = _default_frame_roots()
    if not any(_is_inside_root(image_path, r) for r in roots):
        return {"ok": False, "error": f"image_path not inside allowed roots: {roots}"}
    if not os.path.exists(image_path):
        return {"ok": False, "error": f"image not found: {image_path}"}
    size = os.path.getsize(image_path)
    if size > max_bytes:
        return {"ok": False, "error": f"image too large ({size} bytes; max {max_bytes})"}
    mime, _ = mimetypes.guess_type(image_path)
    if not mime or not mime.startswith("image/"):
        mime = "image/jpeg"
    with open(image_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    return {"ok": True, "data_uri": f"data:{mime};base64,{b64}", "size": size, "mime": mime}


_URL_PATTERN = re.compile(r"^https?://", re.I)


def _sanitize_public_url(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not _URL_PATTERN.match(url):
        return None
    return url[:500]


def _build_prompt(exercise: Optional[str], angles: Optional[Dict[str, Any]], rule_feedback: Optional[str]) -> str:
    lines = [
        "你是一位健身姿态视觉分析师。",
        "只根据图像中可见的关节位置/姿态做客观评价; 不要编造数值; 不做医疗诊断。",
        "输出严格 JSON, 结构: {",
        "  \"exercise_visible\": \"从画面判断这一帧最像哪个动作 (squat/push_up/plank/lunge/jumping_jack/bicep_curl/shoulder_press/unknown)\",",
        "  \"posture_findings\": [{\"issue\": \"...\", \"severity\": \"low|medium|high\"}, ...],",
        "  \"positive_points\": [\"...\"],",
        "  \"recommendations\": [\"...\"],",
        "  \"confidence\": 0-1",
        "}",
        "只输出这个 JSON, 不要 Markdown 包装。",
    ]
    if exercise:
        lines.append(f"目标动作: {exercise}")
    if angles:
        try:
            angle_str = ", ".join(f"{k}={v}" for k, v in angles.items() if v is not None)
            if angle_str:
                lines.append(f"该帧规则计算的关节角(仅供参考,可能因遮挡失真): {angle_str}")
        except Exception:
            pass
    if rule_feedback:
        lines.append(f"规则打分器已给出的定量反馈: {rule_feedback[:200]}")
    return "\n".join(lines)


def _parse_vision_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group(0)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def analyze_pose_image(
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
    exercise: Optional[str] = None,
    angles: Optional[Dict[str, Any]] = None,
    rule_feedback: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Send one image + prompt to a vision model with cross-vendor fallback.

    Tries configured providers (see ``vision_providers.available_providers``)
    in priority order; falls through on HTTP error / timeout. Uses whichever
    provider first returns HTTP 200 with parseable JSON.

    ``image_path`` must live inside one of the whitelisted frame roots.
    ``image_url`` must be a public http(s) URL.
    """
    from .vision_providers import available_providers

    providers = available_providers()
    if not providers:
        return {"ok": False, "error": "no vision provider configured (set DASHSCOPE_API_KEY / VOLC_ARK_API_KEY / ZHIPU_API_KEY / MOONSHOT_API_KEY / HUNYUAN_API_KEY)", "recoverable": True}

    # ---- resolve image once, share across attempts ----
    if image_path:
        loaded = _load_image_data_uri(image_path)
        if not loaded.get("ok"):
            return {"ok": False, "error": loaded.get("error"), "error_type": "image_load_failed"}
        image_ref = {"url": loaded["data_uri"]}
    elif image_url:
        clean = _sanitize_public_url(image_url)
        if not clean:
            return {"ok": False, "error": "image_url must be http(s) URL", "error_type": "invalid_url"}
        image_ref = {"url": clean}
    else:
        return {"ok": False, "error": "either image_path or image_url is required"}

    content_parts: List[Dict[str, Any]] = [
        {"type": "text", "text": _build_prompt(exercise, angles, rule_feedback)},
        {"type": "image_url", "image_url": image_ref},
    ]
    tried: List[Dict[str, Any]] = []
    for prov in providers:
        # zhipu glm-4v-flash caps max_tokens=1024, others accept up to 4k+
        max_tokens = 1024 if "flash" in prov["model"] else 800
        payload = {
            "model": prov["model"],
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(
                prov["url"],
                headers={"Authorization": f"Bearer {prov['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout or float(os.environ.get("AI_AGENT_VISION_TIMEOUT", "90")),
            )
        except requests.RequestException as exc:
            log.warning("vision provider %s network error: %s", prov["provider"], exc)
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"network:{exc}"})
            continue
        if resp.status_code != 200:
            log.warning("vision provider %s HTTP %s: %s", prov["provider"], resp.status_code, resp.text[:200])
            tried.append({"provider": prov["provider"], "model": prov["model"], "http": resp.status_code, "body": resp.text[:200]})
            continue
        try:
            data = resp.json()
            raw_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"response_parse:{exc}"})
            continue
        parsed = _parse_vision_json(raw_content)
        return {
            "ok": True,
            "provider": prov["provider"],
            "model": prov["model"],
            "exercise_hint": exercise,
            "raw_reply": raw_content[:2000],
            "analysis": parsed,
            "tried_before": tried,
            "note": "视觉分析仅为定性观察; 定量分数以规则/rep_scorer 为准; 科学依据请引用 search_fitness_kb / search_fitness_web。",
        }
    return {
        "ok": False,
        "error": "all vision providers failed",
        "error_type": "vision_all_providers_failed",
        "tried": tried,
        "recoverable": True,
    }
