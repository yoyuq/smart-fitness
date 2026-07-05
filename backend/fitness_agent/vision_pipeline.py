"""Two-stage vision pipeline for the Smart Fitness Agent.

Stage 1: vision model extracts structured pose features from an image
         (joint visibility, observed angles, alignment cues, phase of movement).
Stage 2: text-only LLM synthesizes the extracted features + user context
         + rule-based scoring into an individualized coaching analysis.

Separating the two stages gives us:
- Cheaper repeat analyses (stage 1 output can be cached; only stage 2 re-runs
  when user context / evidence changes).
- Better auditability (each stage's intermediate output is inspectable).
- Cross-model resilience (stage 2 can run on any text model).
"""
import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

import requests

from .vision import (
    _VOLC_ARK_URL,
    _api_key,
    _load_image_data_uri,
    _parse_vision_json,
    _sanitize_public_url,
    _vision_model,
)

log = logging.getLogger("fitness_agent.vision_pipeline")


# ------------------------- Frame sampling -----------------------------------

_ALLOWED_FRAME_COUNTS = (1, 3, 5, 7, 9)
_DEFAULT_FRAME_COUNT = 5


def normalize_frame_count(value: Any, default: int = _DEFAULT_FRAME_COUNT) -> int:
    """Clamp requested frame count to the allowed 1/3/5/7/9 grid.

    - Non-int / negative / zero => default (5)
    - 1 => single-frame mode (backward compat, only peak.jpg)
    - 2/4/6/8 => rounded down to nearest odd allowed value
    - >9 => 9
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    if n >= 9:
        return 9
    # snap down to allowed odd values (1,3,5,7,9)
    for allowed in reversed(_ALLOWED_FRAME_COUNTS):
        if n >= allowed:
            return allowed
    return default


def sample_rep_frames(
    clip_dir: Optional[str],
    angle_series: Optional[Dict[str, Any]] = None,
    k: int = _DEFAULT_FRAME_COUNT,
    fallback_frame: Optional[str] = None,
) -> Dict[str, Any]:
    """Pick k frames from a rep clip with the peak/bottom anchored to the middle.

    Strategy:
      1. List all frames sorted by filename (chronological).
      2. Locate the *bottom* frame:
           - Prefer the min-primary index of ``angle_series['primary']`` (32-len
             resampled series produced by RepScorer) and map back to clip index.
           - Fallback to n//2.
      3. Distribute the remaining k-1 slots evenly on both sides of the bottom
         while always including frame 0 (top) and frame n-1 (top again). This
         keeps top-and-bottom coverage regardless of rep tempo.
      4. Deduplicate + backfill so we always return <=k unique frame paths.

    Args:
      clip_dir: absolute path to the rep clip directory (from rep_scores.clip_dir).
      angle_series: JSON from rep_scores.angle_series (optional but recommended).
      k: desired sample count (will be normalized to 1/3/5/7/9).
      fallback_frame: single-frame path used when clip_dir is missing / unreadable
                      (typically rep_scores.peak_frame). Kept for backward compat
                      with reps that predate clip storage.

    Returns:
      {
        "ok": True/False,
        "frames": ["abs/path/f000.jpg", ...],
        "indices": [0, 2, 4, 6, 8],
        "bottom_index": 4,
        "source": "clip" | "single_frame" | "missing",
        "requested_k": 5,
        "actual_k": 5,
      }
    """
    k = normalize_frame_count(k)

    # Missing clip_dir or non-existent path: fall back to single frame
    if not clip_dir or not os.path.isdir(clip_dir):
        if fallback_frame and os.path.isfile(fallback_frame):
            return {
                "ok": True, "frames": [fallback_frame], "indices": [0],
                "bottom_index": 0, "source": "single_frame",
                "requested_k": k, "actual_k": 1,
                "note": "clip_dir unavailable, degraded to single-frame mode",
            }
        return {
            "ok": False, "frames": [], "indices": [], "source": "missing",
            "requested_k": k, "actual_k": 0,
            "error": "no clip_dir and no fallback frame",
        }

    try:
        frames = sorted(f for f in os.listdir(clip_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    except OSError as exc:
        if fallback_frame and os.path.isfile(fallback_frame):
            return {
                "ok": True, "frames": [fallback_frame], "indices": [0],
                "bottom_index": 0, "source": "single_frame",
                "requested_k": k, "actual_k": 1,
                "note": f"clip_dir read failed ({exc}); degraded to single-frame mode",
            }
        return {
            "ok": False, "frames": [], "indices": [], "source": "missing",
            "requested_k": k, "actual_k": 0,
            "error": f"clip_dir read failed: {exc}",
        }
    n = len(frames)
    if n == 0:
        if fallback_frame and os.path.isfile(fallback_frame):
            return {
                "ok": True, "frames": [fallback_frame], "indices": [0],
                "bottom_index": 0, "source": "single_frame",
                "requested_k": k, "actual_k": 1,
                "note": "clip_dir empty, degraded to single-frame mode",
            }
        return {
            "ok": False, "frames": [], "indices": [], "source": "missing",
            "requested_k": k, "actual_k": 0,
            "error": "clip_dir has no images",
        }

    if k <= 1 or n == 1:
        # Single-frame mode: prefer explicit peak fallback if provided, else
        # take the estimated bottom, else the middle frame.
        if fallback_frame and os.path.isfile(fallback_frame):
            return {
                "ok": True, "frames": [fallback_frame], "indices": [-1],
                "bottom_index": -1, "source": "single_frame",
                "requested_k": k, "actual_k": 1,
            }
        chosen = frames[_bottom_index_from_series(n, angle_series) or (n // 2)]
        return {
            "ok": True, "frames": [os.path.join(clip_dir, chosen)], "indices": [0],
            "bottom_index": 0, "source": "clip",
            "requested_k": k, "actual_k": 1,
        }

    # ----- multi-frame case -----
    bottom_i = _bottom_index_from_series(n, angle_series)
    if bottom_i is None:
        bottom_i = n // 2
    bottom_i = max(1, min(n - 2, bottom_i))

    # Distribute k slots: always keep 0, n-1, and bottom_i; fill remaining
    # k-3 slots (descent side + ascent side) proportionally around bottom_i.
    indices = _distribute_indices_around_bottom(n=n, k=k, bottom=bottom_i)
    frame_paths = [os.path.join(clip_dir, frames[i]) for i in indices]
    return {
        "ok": True,
        "frames": frame_paths,
        "indices": indices,
        "bottom_index": bottom_i,
        "source": "clip",
        "requested_k": k,
        "actual_k": len(indices),
    }


def _bottom_index_from_series(clip_n: int, angle_series: Optional[Dict[str, Any]]) -> Optional[int]:
    """Map min-of-primary in a 32-len angle_series back to a clip frame index."""
    if not angle_series:
        return None
    primary = angle_series.get("primary") if isinstance(angle_series, dict) else None
    if not primary or len(primary) < 2:
        return None
    try:
        vals = [v for v in primary if v is not None]
        if len(vals) < 2:
            return None
        m = len(primary)
        bi_m = min(range(m), key=lambda i: (primary[i] if primary[i] is not None else float("inf")))
        return int(bi_m * (clip_n - 1) / (m - 1))
    except Exception:
        return None


def _distribute_indices_around_bottom(n: int, k: int, bottom: int) -> List[int]:
    """Return sorted unique list of k frame indices in [0, n-1] with bottom pinned.

    Guarantees:
      - contains 0 and n-1
      - contains bottom (if 1<=bottom<=n-2)
      - remaining slots split evenly between the [0..bottom] and [bottom..n-1] halves
    """
    if k >= n:
        return list(range(n))
    slots = {0, n - 1, bottom}
    remaining = k - len(slots)
    if remaining <= 0:
        return sorted(slots)[:k]
    # split remaining between descent (0..bottom) and ascent (bottom..n-1)
    left = remaining // 2
    right = remaining - left
    # descent picks: evenly spaced between 0 and bottom (exclusive of both)
    def evenly(a: int, b: int, count: int) -> List[int]:
        if count <= 0 or b - a <= 1:
            return []
        out = []
        for i in range(1, count + 1):
            pos = a + i * (b - a) / (count + 1)
            out.append(int(round(pos)))
        return out
    for idx in evenly(0, bottom, left):
        slots.add(idx)
    for idx in evenly(bottom, n - 1, right):
        slots.add(idx)
    # If dedup shrunk the set (adjacent picks collided), backfill by scanning
    while len(slots) < k:
        added = False
        for cand in range(n):
            if cand not in slots:
                slots.add(cand)
                added = True
                break
        if not added:
            break
    return sorted(slots)[:k]


# ------------------------- Stage 1: feature extraction ------------------------


def _extraction_prompt(exercise: Optional[str], multi_frame: bool = False) -> str:
    """Prompt that tells the vision model to only *observe*, not judge."""
    if multi_frame:
        header = [
            "你是姿态观察员。下面按时间顺序给你一整个 rep 的连续画面帧, 综合观察全过程 (top->descent->bottom->ascent->top),",
            "不要单看一帧。中间那一帧是动作最深处(bottom), 请在 observed_angles_deg 中记录 bottom 处的角度值。",
        ]
    else:
        header = ["你是姿态观察员。只描述画面里客观可见的信息, 不做好坏评价, 不做诊断, 不建议动作。"]
    lines = header + [
        "严格输出下面结构的 JSON, 不要 Markdown 代码块, 不要多余文字:",
        "{",
        "  \"exercise_visible\": \"squat|push_up|plank|lunge|jumping_jack|bicep_curl|shoulder_press|unknown\",",
        "  \"movement_phase\": \"top|descent|bottom|ascent|hold|unknown  (若无法判断填 unknown)\",",
        "  \"visible_body_parts\": [\"head\",\"torso\",\"left_arm\",\"right_arm\",\"left_leg\",\"right_leg\", ...],",
        "  \"observed_angles_deg\": {\"knee_left\":0-180 或 null,\"knee_right\":0-180 或 null,\"hip\":0-180 或 null,\"elbow_left\":0-180 或 null,\"elbow_right\":0-180 或 null,\"trunk_forward_lean\":0-90 或 null},",
        "  \"alignment_cues\": [\"knee_over_toe\",\"knee_caving_in\",\"heels_lifted\",\"pelvis_tucked_back (butt_wink)\",\"lumbar_hyperextension\",\"shoulder_shrug\",\"elbow_flare\",\"hip_pike\",\"hip_sag\",\"asymmetric_left_right\", ... 只写画面中真的可见的]",
        "  \"other_observations\": [\"任何不在上面枚举里、但你确实看到的现象 (如:手臂颇抖、呼吸憋气、面部表情吃力、脚踝内翻、头颈前伸、视线偏离、器械位置偏移、环境因素等) - 自由描述\"],",
        "  \"symmetry\": {\"left_right_balance\": \"balanced|left_heavy|right_heavy|unclear\", \"notes\": \"...\"},",
        "  \"camera_angle\": \"front|side|three_quarter|back|top|unclear\",",
        "  \"image_quality\": {\"blur\": \"none|slight|significant\", \"occlusion\": [\"knee_left\", ...], \"lighting\": \"good|dim|overexposed\"},",
        "  \"confidence\": 0-1",
        "}",
        "不要添加以上未列出的 key。observed_angles_deg 中不确定的值填 null, 不要瞎填。",
    ]
    if exercise:
        lines.append(f"用户/系统告诉你这个 rep 目标动作是: {exercise}。请优先按该动作的关节角度语义来读取。")
    return "\n".join(lines)


def extract_pose_features(
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
    exercise: Optional[str] = None,
    timeout: Optional[float] = None,
    image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Stage 1: send image(s) to vision model with cross-vendor fallback.

    Supports three input modes:
      1. Single local image via ``image_path``.
      2. Multiple local images via ``image_paths`` (list, up to 9). All images
         share the same prompt; a multi-frame prompt variant is used.
      3. Single public URL via ``image_url``.

    Tries configured providers (see ``vision_providers.available_providers``)
    in priority order; on HTTP error / timeout / empty-parse it moves to the
    next provider. Returns the first successful extraction.
    """
    from .vision_providers import available_providers

    providers = available_providers()
    if not providers:
        return {"ok": False, "error": "no vision provider configured", "recoverable": True}

    # resolve image(s) once, share across attempts
    image_refs: List[Dict[str, Any]] = []
    if image_paths:
        paths = [p for p in image_paths if p]
        if not paths:
            return {"ok": False, "error": "image_paths list is empty"}
        # dashscope image count cap: 9 (also our hard ceiling)
        for p in paths[:9]:
            loaded = _load_image_data_uri(p)
            if not loaded.get("ok"):
                return {"ok": False, "error": loaded.get("error"),
                        "error_type": "image_load_failed", "image_path": p}
            image_refs.append({"url": loaded["data_uri"]})
    elif image_path:
        loaded = _load_image_data_uri(image_path)
        if not loaded.get("ok"):
            return {"ok": False, "error": loaded.get("error"), "error_type": "image_load_failed"}
        image_refs.append({"url": loaded["data_uri"]})
    elif image_url:
        clean = _sanitize_public_url(image_url)
        if not clean:
            return {"ok": False, "error": "image_url must be http(s) URL", "error_type": "invalid_url"}
        image_refs.append({"url": clean})
    else:
        return {"ok": False, "error": "image_path / image_paths / image_url required"}

    multi_frame = len(image_refs) > 1
    content_parts: List[Dict[str, Any]] = [
        {"type": "text", "text": _extraction_prompt(exercise, multi_frame=multi_frame)},
    ]
    for ref in image_refs:
        content_parts.append({"type": "image_url", "image_url": ref})
    tried: List[Dict[str, Any]] = []
    max_tokens_default = int(os.environ.get("AI_AGENT_VISION_EXTRACT_MAX_TOKENS", "1400"))
    for prov in providers:
        # zhipu glm-4v-flash caps max_tokens=1024
        mt = 1024 if "flash" in prov["model"] else max_tokens_default
        payload = {
            "model": prov["model"],
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": 0.1,
            "max_tokens": mt,
        }
        try:
            resp = requests.post(
                prov["url"],
                headers={"Authorization": f"Bearer {prov['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout or float(os.environ.get("AI_AGENT_VISION_TIMEOUT", "90")),
            )
        except requests.RequestException as exc:
            log.warning("stage1 provider %s network error: %s", prov["provider"], exc)
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"network:{exc}"})
            continue
        if resp.status_code != 200:
            tried.append({"provider": prov["provider"], "model": prov["model"], "http": resp.status_code, "body": resp.text[:200]})
            continue
        try:
            data = resp.json()
            raw_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"response_parse:{exc}"})
            continue
        features = _parse_vision_json(raw_content)
        parse_status = "ok" if features else "parse_failed"
        return {
            "ok": True,
            "stage": "extract",
            "provider": prov["provider"],
            "model": prov["model"],
            "raw_reply": raw_content[:4000],
            "features": features or {},
            "parse_status": parse_status,
            "prompt_max_tokens": mt,
            "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
            "tried_before": tried,
            "frames_sent": len(image_refs),
            "usage": data.get("usage") or {},
        }
    return {
        "ok": False,
        "error": "all vision providers failed",
        "error_type": "vision_all_providers_failed",
        "tried": tried,
        "recoverable": True,
    }


# ------------------------- Stage 2: reasoning / coaching -----------------------


# Sanity-check heuristics: known Stage 1 hallucinations we've seen in the wild.
# When Stage 1 emits one of these while the rule/time-series signal contradicts
# it, we surface the conflict *before* handing it to Stage 2 rather than trusting
# the vision model blindly.
#
# Each entry:
#   cue        : Stage 1 alignment_cue key (matched as substring, lowercase)
#   applies_to : exercise names it makes physical sense for
#   contradiction:
#     type = "angle_range" -> conflict if rule peak_angle falls in [lo, hi]
#     type = "never"        -> always flagged (biomechanically impossible for exercise)
#   note       : short reason attached to the conflict message
_CUE_CONFLICT_RULES = [
    {
        "cue": "lumbar_hyperextension",
        "applies_to": {"squat", "lunge"},
        "contradiction": {"type": "angle_range", "joint": "peak_angle", "lo": 0, "hi": 120},
        "note": "squat/lunge bottom is lumbar flexion, not hyperextension; peak knee interior <120° = actively descending, hyperextension implausible",
    },
    {
        "cue": "hip_pike",
        "applies_to": {"squat", "lunge", "bicep_curl", "shoulder_press"},
        "contradiction": {"type": "never"},
        "note": "hip_pike is a push-up/plank cue; not applicable to standing lower-body/upper-body exercises",
    },
    {
        "cue": "hip_sag",
        "applies_to": {"squat", "lunge", "bicep_curl", "shoulder_press"},
        "contradiction": {"type": "never"},
        "note": "hip_sag is a plank/push-up cue; not applicable here",
    },
    {
        "cue": "shoulder_shrug",
        "applies_to": {"squat", "lunge"},
        "contradiction": {"type": "never"},
        "note": "shoulder shrug is not a scoring cue for lower-body squats/lunges",
    },
]


def _angle_disagreement(
    features: Dict[str, Any],
    rule_summary: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return list of angle-level Stage1<->rule disagreements (e.g. VL 90° vs rule 20°).

    We treat >30° gap on the primary joint as a hard disagreement worth calling out.
    """
    conflicts: List[Dict[str, Any]] = []
    if not rule_summary:
        return conflicts
    rule_peak = rule_summary.get("peak_angle_deg") or rule_summary.get("peak_angle")
    if rule_peak is None:
        return conflicts
    try:
        rule_peak_f = float(rule_peak)
    except (TypeError, ValueError):
        return conflicts
    exercise = (rule_summary.get("exercise") or "").lower()
    joint_map = {
        "squat": ("knee_left", "knee_right"),
        "lunge": ("knee_left", "knee_right"),
        "push_up": ("elbow_left", "elbow_right"),
        "bicep_curl": ("elbow_left", "elbow_right"),
        "shoulder_press": ("elbow_left", "elbow_right"),
    }
    keys = joint_map.get(exercise, ())
    observed = (features.get("observed_angles_deg") or {}) if isinstance(features, dict) else {}
    vals = [observed.get(k) for k in keys if observed.get(k) is not None]
    if not vals:
        return conflicts
    try:
        vl_avg = sum(float(v) for v in vals) / len(vals)
    except (TypeError, ValueError):
        return conflicts
    delta = abs(vl_avg - rule_peak_f)
    if delta >= 30:
        conflicts.append({
            "type": "angle_gap",
            "vl_angle_deg": round(vl_avg, 1),
            "rule_peak_deg": round(rule_peak_f, 1),
            "delta_deg": round(delta, 1),
            "joint": exercise + "_primary",
            "trust": "rule",
            "note": (
                f"视觉报告 {vl_avg:.0f}° 与规则测量 {rule_peak_f:.0f}° 相差 {delta:.0f}°；"
                f"规则值来自 MediaPipe 3D landmarks，以规则值为准"
            ),
        })
    return conflicts


def _cue_conflicts(
    features: Dict[str, Any],
    rule_summary: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Flag qualitative Stage 1 cues that are inconsistent with the exercise/rule."""
    conflicts: List[Dict[str, Any]] = []
    if not isinstance(features, dict):
        return conflicts
    cues = [str(c).lower() for c in (features.get("alignment_cues") or [])]
    exercise = ((rule_summary or {}).get("exercise") or features.get("exercise_visible") or "").lower()
    rule_peak = None
    if rule_summary:
        rp = rule_summary.get("peak_angle_deg") or rule_summary.get("peak_angle")
        try:
            rule_peak = float(rp) if rp is not None else None
        except (TypeError, ValueError):
            rule_peak = None

    for rule in _CUE_CONFLICT_RULES:
        cue = rule["cue"]
        if exercise not in rule["applies_to"]:
            continue
        if not any(cue in c for c in cues):
            continue
        contradiction = rule["contradiction"]
        if contradiction["type"] == "never":
            conflicts.append({
                "type": "impossible_cue",
                "cue": cue,
                "exercise": exercise,
                "trust": "rule",
                "action": "drop_or_downgrade_to_low",
                "note": rule["note"],
            })
        elif contradiction["type"] == "angle_range":
            if rule_peak is None:
                continue
            lo, hi = contradiction["lo"], contradiction["hi"]
            if lo <= rule_peak <= hi:
                conflicts.append({
                    "type": "cue_vs_rule_range",
                    "cue": cue,
                    "exercise": exercise,
                    "rule_peak_deg": round(rule_peak, 1),
                    "trust": "rule",
                    "action": "drop_or_downgrade_to_low",
                    "note": rule["note"],
                })
    return conflicts


def detect_stage1_conflicts(
    features: Dict[str, Any],
    rule_summary: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Public API: return a merged list of Stage 1 hallucination / disagreement flags.

    Downstream (Stage 2 prompt + evidence layers) can consult this to auto-
    downgrade severity or drop implausible cues before writing coaching output.
    """
    if not isinstance(features, dict) or not features:
        return []
    conflicts: List[Dict[str, Any]] = []
    conflicts.extend(_angle_disagreement(features, rule_summary))
    conflicts.extend(_cue_conflicts(features, rule_summary))
    return conflicts


def _default_reasoning_url() -> str:
    return os.environ.get(
        "AI_AGENT_LLM_URL",
        os.environ.get("VOLC_ARK_TEXT_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
    )


def _reasoning_model() -> str:
    return os.environ.get(
        "AI_AGENT_LLM_MODEL",
        os.environ.get("VOLC_TEXT_MODEL", "doubao-1-5-pro-32k-250115"),
    )


def _reasoning_api_key() -> str:
    return (
        os.environ.get("AI_AGENT_LLM_API_KEY", "")
        or os.environ.get("VOLC_ARK_API_KEY", "")
        or os.environ.get("ARK_API_KEY", "")
    )


# ---------- Multi-provider reasoning chain (DeepSeek / Volc / ...) ----------
#
# Stage 2 used to hard-target 火山方舟 doubao text LLM. When the user's doubao
# quota runs out every session-level analysis fails with HTTP 429/402 and the
# App just shows "stage1: 0/N". We now support a small provider chain, defaulting
# to ``deepseek,volc`` so the pipeline keeps working when either vendor is
# down / out of credits. Priority is overridable via ``AI_AGENT_LLM_CHAIN``.

_TEXT_PROVIDER_CATALOG: List[Dict[str, Any]] = [
    # === Aliyun Bailian 2026 flagship lineup (all via dashscope OpenAI-compat, one DASHSCOPE_API_KEY) ===
    # Probe (2026-07-05, sk-033f...ef82): deepseek-v4-pro / kimi-k2.7-code / qwen3.7-max /
    # qwen3.6-flash / deepseek-v4-flash all working. Primary = deepseek-v4-pro
    # (long reasoning chain + clean JSON + ~2s). Backups = kimi-k2.7-code (multimodal +
    # long context), qwen3.7-max (best Chinese fluency), qwen3.6-flash (fastest).
    # Volc doubao coding plan below is kept as last-resort fallback (quota exhausted).
    {
        "provider": "bailian-deepseek-v4-pro",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("BAILIAN_DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    {
        "provider": "bailian-kimi-k2-7-code",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("BAILIAN_KIMI_K27_MODEL", "kimi-k2.7-code"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    {
        "provider": "bailian-qwen3-7-max",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("BAILIAN_QWEN37_MAX_MODEL", "qwen3.7-max"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    {
        "provider": "bailian-qwen3-6-flash",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("BAILIAN_QWEN36_FLASH_MODEL", "qwen3.6-flash"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    {
        "provider": "bailian-deepseek-v4-flash",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("BAILIAN_DEEPSEEK_V4_FLASH_MODEL", "deepseek-v4-flash"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    # === Volc coding plan (doubao Seed 1.6) - fallback only, doubao quota is exhausted ===
    {
        "provider": "volc-coding",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": os.environ.get("VOLC_CODING_TEXT_MODEL", "doubao-seed-1-6-250615"),
        "key_envs": [
            "VOLC_ARK_CODING_API_KEY",
            "VOLC_CODING_API_KEY",
            "VOLCANO_CODING_API_KEY",
            "VOLCANO_ENGINE_API_KEY",  # coding plan key exported as VOLCANO_ENGINE_API_KEY on this host
        ],
    },
    {
        "provider": "volc-coding-flash",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": os.environ.get("VOLC_CODING_FAST_MODEL", "doubao-seed-1-6-flash-250615"),
        "key_envs": [
            "VOLC_ARK_CODING_API_KEY",
            "VOLC_CODING_API_KEY",
            "VOLCANO_CODING_API_KEY",
            "VOLCANO_ENGINE_API_KEY",
        ],
    },
    {
        "provider": "deepseek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "key_envs": ["DEEPSEEK_API_KEY"],
    },
    {
        "provider": "volc",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": os.environ.get("VOLC_TEXT_MODEL", "doubao-1-5-pro-32k-250115"),
        "key_envs": ["VOLC_ARK_API_KEY", "ARK_API_KEY"],
    },
    # 阿里百炼 / DashScope OpenAI-compatible endpoint. When 火山 & DeepSeek both
    # exhaust credit, Qwen text model keeps Stage 2 running end-to-end. Same key
    # already powers qwen-vl-max at Stage 1, so no extra setup needed.
    {
        "provider": "qwen",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": os.environ.get("QWEN_TEXT_MODEL", "qwen-plus"),
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    },
    # 火山 legacy fallback model (v1.5) as last resort when doubao pro quota is empty
    # but ark account still has legacy credit.
    {
        "provider": "volc-legacy",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": os.environ.get("VOLC_TEXT_MODEL_FAST", "doubao-1-5-lite-32k-250115"),
        "key_envs": ["VOLC_ARK_API_KEY", "ARK_API_KEY"],
    },
]


def _first_env(names: List[str]) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _reasoning_providers() -> List[Dict[str, Any]]:
    # Explicit single-provider override (kept for tests / debugging).
    if os.environ.get("AI_AGENT_LLM_URL") and _reasoning_api_key():
        return [{
            "provider": "custom",
            "url": _default_reasoning_url(),
            "model": _reasoning_model(),
            "api_key": _reasoning_api_key(),
        }]
    chain = os.environ.get(
        "AI_AGENT_LLM_CHAIN",
        "bailian-deepseek-v4-pro,bailian-kimi-k2-7-code,bailian-qwen3-7-max,"
        "bailian-qwen3-6-flash,bailian-deepseek-v4-flash,qwen,"
        "volc-coding,volc-coding-flash,volc,volc-legacy",
    ).strip()
    wanted = [p.strip() for p in chain.split(",") if p.strip()]
    indexed = {p["provider"]: p for p in _TEXT_PROVIDER_CATALOG}
    out: List[Dict[str, Any]] = []
    for pid in wanted:
        prov = indexed.get(pid)
        if not prov:
            continue
        key = _first_env(prov["key_envs"])
        if not key:
            continue
        out.append({
            "provider": prov["provider"],
            "url": prov["url"],
            "model": prov["model"],
            "api_key": key,
        })
    return out



def _reasoning_prompt(
    features: Dict[str, Any],
    exercise: Optional[str],
    rule_summary: Optional[Dict[str, Any]],
    user_context: Optional[Dict[str, Any]],
    evidence_citations: Optional[List[Dict[str, Any]]],
    time_series: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    stage1_conflicts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    parts = [
        "你是一位健身姿态与运动完成度分析师。你会拿到:",
        "1) 视觉模型从一张训练画面上提取到的客观姿态特征 (只有观察, 没有评价)。",
        "2) 规则打分器对同一个 rep 的量化输出 (角度分, 控制分, 对称分, 总分, 反馈标签)。",
        "3) 用户的身体信息与训练目标。",
        "4) 有据可查的运动学参考阈值 (可选, 如果 evidence_citations 里给了)。",
        "",
        "请综合以上信息, 输出严格 JSON, 结构如下, 不要 Markdown, 不要多余字符:",
        "{",
        "  \"exercise_confirmed\": \"具体动作 (若视觉与规则一致则写该动作, 若冲突写 conflict:视觉-规则)\",",
        "  \"posture_assessment\": {",
        "     \"strengths\": [\"...\"],",
        "     \"issues\": [{\"issue\": \"...\", \"severity\": \"low|medium|high\", \"evidence\": \"引用哪一条视觉特征或规则数字\", \"citation_key\": \"若有 evidence_citations 里的 key\" }, ...]",
        "  },",
        "  \"completion_score\": {",
        "    \"depth\": 0-100,",
        "    \"control\": 0-100,",
        "    \"symmetry\": 0-100,",
        "    \"overall\": 0-100,",
        "    \"notes\": \"分数来源说明; 若与规则打分器差距>10 请说明为什么信任视觉/规则\"",
        "  },",
        "  \"guidance\": {",
        "     \"immediate_next_rep\": [\"...\"],   // 下一 rep 立刻可以调整的 1-3 条",
        "     \"next_session\": [\"...\"],       // 下一次训练前的准备与热身",
        "     \"progression_or_regression\": \"...\", // 需要加/减难度",
        "     \"cautions\": [\"...\"]            // 疼痛/受伤风险信号, 需要停止或就医的临界",
        "  },",
        "  \"data_gaps\": [\"...\"],  // 视觉/规则都缺少哪些关键信息",
        "  \"confidence\": 0-1",
        "}",
        "",
        "硬约束:",
        "- 不要凭空创造数字; completion_score 必须能从视觉 features + rule_summary 推导出来。",
        "- 视觉与规则冲突时倾向规则给的定量分, 但**允许**在下列两种情况下把 completion_score 下调 5-20 分:",
        "    1) Stage 1 观察到**幅度/深度明显不足** (如 squat 视觉 phase=descent 但 top 帧膝盖仍 >140° 且未到 bottom, 或 push_up 顶帧肘 >150° 且底帧仍 >100°), 且 rule 却给了 depth=100 -> 说明规则测量的 peak 可能只是瞬时噪声, 而 rep 整体幅度不到位; 此时应下调 depth 与 overall。",
        "    2) Stage 1 明确 alignment_cues 里出现 'partial_range' / 'incomplete_range' / 'not_reaching_bottom' 等观察, 且 time_series 的 primary 通道 max-min < 该动作合理 ROM 的 60%。",
        "    下调时必须在 completion_score.notes 里写明: 视觉观察 X + 时序 ROM=Y -> 规则 depth 分不可信, 下调 Z 分。",
        "- 只有在 evidence_citations 里给了 key 时才写 citation_key; 不要发明。",
        "- guidance 里的每一条必须具体到 \"降低下蹲深度 3-5cm\" / \"膝盖向脚尖外侧稍推 5°\" 而不是 \"注意膝盖\"。",
        "- 严禁做医疗诊断; 出现痛/麻/胸闷等信号只能进 cautions 建议停止并就医。",
        "- **冲突处理硬规则 (stage1_conflicts)**: 如果下面给了 stage1_conflicts 列表，里面每一条都已经确认 stage1 视觉输出与规则/时序/生物力学不一致：",
        "    \u2022 对应的 issue 不得写成 high；若仍需保留，必须 severity 降至 low 并在 issue 末尾拼接 \" [conflict:stage1_hallucination]\"",
        "    \u2022 若 conflict.type=impossible_cue 或 cue_vs_rule_range 且 action=drop_or_downgrade_to_low，优先从 issues 里删除该 cue，不要写到 posture_assessment.issues。",
        "    \u2022 若 conflict.type=angle_gap，completion_score.notes 必须写上：\"视觉报角度 X° 与规则测量 Y° 相差，以规则为准\"。",
        "    \u2022 stage1_conflicts 本身不能作为 issue 写进输出（它是元信息，不是用户接受的内容）。",
        "",
    ]
    parts.append("========= 视觉特征 (stage 1 输出) =========")
    parts.append(json.dumps(features or {}, ensure_ascii=False, indent=2))
    if rule_summary:
        parts.append("========= 规则打分器输出 =========")
        parts.append(json.dumps(rule_summary, ensure_ascii=False, indent=2))
    if user_context:
        parts.append("========= 用户上下文 =========")
        parts.append(json.dumps(user_context, ensure_ascii=False, indent=2))
    if evidence_citations:
        parts.append("========= 可引用的运动学阈值 =========")
        parts.append(json.dumps(evidence_citations, ensure_ascii=False, indent=2))
    if time_series:
        parts.append("========= 时域信息 (整个 rep 的关节角序列, 不只峰值帧) =========")
        parts.append(json.dumps(time_series, ensure_ascii=False, indent=2)[:3500])
    if history:
        parts.append("========= 近期参考 rep 的量化摘要 (用于趋势判断) =========")
        parts.append(json.dumps(history[:12], ensure_ascii=False, indent=2))
    if stage1_conflicts:
        parts.append("========= stage1_conflicts (已预先检测的 Stage 1 幻觉 / 冲突) =========")
        parts.append(json.dumps(stage1_conflicts, ensure_ascii=False, indent=2))
    if exercise:
        parts.append(f"用户/系统告知的目标动作: {exercise}")
    return "\n".join(parts)


def _parse_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        segs = stripped.split("```")
        if len(segs) >= 2:
            stripped = segs[1]
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:]
    m = re.search(r"\{.*\}", stripped, re.S)
    if m:
        stripped = m.group(0)
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        # Salvage attempts for common LLM JSON quirks:
        #   1) JS expressions like Math.round(...) / (a+b)/n / sums as values
        #   2) Trailing commas before } or ]
        salvaged = stripped
        # Replace `"key": <js-expression>,` with `"key": 0,` when expression
        # contains Math. / arithmetic operators / parentheses (numeric context only).
        # Iterate: Math.foo(...) may have nested parens; each pass removes the
        # innermost, replace repeatedly until stable.
        for _ in range(6):
            new = re.sub(
                r'(:\s*)(Math\.[a-zA-Z]+\s*\((?:[^()]|\([^()]*\))*\))',
                r'\g<1>0',
                salvaged,
            )
            if new == salvaged:
                break
            salvaged = new
        # Bare parenthesized arithmetic expression as a numeric value:
        # e.g., ": (85+90)/2," or ": (a+b)/n)" (with unmatched paren fallback).
        salvaged = re.sub(
            r'(:\s*)\d*\s*[+\-*/]\s*\d[^,}\n]*',
            lambda m: (m.group(1) + '0') if re.search(r'[+\-*/]', m.group(0)[len(m.group(1)):]) else m.group(0),
            salvaged,
        )
        # Trailing commas before } or ]
        salvaged = re.sub(r',(\s*[}\]])', r'\1', salvaged)
        try:
            obj = json.loads(salvaged)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}


def reason_about_pose(
    features: Dict[str, Any],
    exercise: Optional[str] = None,
    rule_summary: Optional[Dict[str, Any]] = None,
    user_context: Optional[Dict[str, Any]] = None,
    evidence_citations: Optional[List[Dict[str, Any]]] = None,
    time_series: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    stage1_conflicts: Optional[List[Dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Stage 2: text-only reasoning over the extracted features + context.

    ``time_series`` should be a compact angle-over-time dict (e.g. output of
    ``get_rep_analysis``) so stage 2 can reason about motion, not just the peak
    frame. ``history`` should be a small list of previous rep summaries so
    stage 2 can address trend rather than a single snapshot.
    """
    if stage1_conflicts is None:
        stage1_conflicts = detect_stage1_conflicts(features, rule_summary)
    providers = _reasoning_providers()
    if not providers:
        return {"ok": False, "error": "no text LLM provider configured (set DEEPSEEK_API_KEY or VOLC_ARK_API_KEY)", "recoverable": True}
    if not isinstance(features, dict) or not features:
        return {"ok": False, "error": "features (stage-1 output) required and must be non-empty dict"}
    prompt = _reasoning_prompt(features, exercise, rule_summary, user_context, evidence_citations, time_series, history, stage1_conflicts)
    max_tokens = int(os.environ.get("AI_AGENT_VISION_REASON_MAX_TOKENS", "6000"))
    tried: List[Dict[str, Any]] = []
    for prov in providers:
        payload = {
            "model": prov["model"],
            "messages": [
                {"role": "system", "content": "你只输出符合 schema 的 JSON, 严禁编造数据, 严禁做医疗诊断。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(
                prov["url"],
                headers={"Authorization": f"Bearer {prov['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout or float(os.environ.get("AI_AGENT_VISION_REASON_TIMEOUT", "45")),
            )
        except requests.RequestException as exc:
            log.warning("stage2 reasoning %s network error: %s", prov["provider"], exc)
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"network:{exc}"})
            continue
        if resp.status_code != 200:
            log.warning("stage2 reasoning %s HTTP %s: %s", prov["provider"], resp.status_code, resp.text[:200])
            tried.append({"provider": prov["provider"], "model": prov["model"], "http": resp.status_code, "body": resp.text[:200]})
            continue
        try:
            data = resp.json()
            raw_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            tried.append({"provider": prov["provider"], "model": prov["model"], "error": f"response_parse:{exc}"})
            continue
        parsed = _parse_json_object(raw_content)
        return {
            "ok": True,
            "stage": "reason",
            "provider": prov["provider"],
            "model": prov["model"],
            "raw_reply": raw_content[:3000],
            "analysis": parsed,
            "stage1_conflicts": stage1_conflicts,
            "tried_before": tried,
        }
    return {
        "ok": False,
        "error": "all text LLM providers failed",
        "error_type": "llm_all_providers_failed",
        "tried": tried,
        "recoverable": True,
        "stage1_conflicts": stage1_conflicts,
    }


def two_stage_pose_analysis(
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
    exercise: Optional[str] = None,
    rule_summary: Optional[Dict[str, Any]] = None,
    user_context: Optional[Dict[str, Any]] = None,
    evidence_citations: Optional[List[Dict[str, Any]]] = None,
    time_series: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Chain stage 1 (extract) → stage 2 (reason). Returns both intermediate outputs.

    Frame input priority: ``image_paths`` (multi-frame) > ``image_path`` (single)
    > ``image_url`` (single). ``time_series`` (optional): compact angle-over-time
    payload (see ``get_rep_analysis``) so stage 2 can reason about motion, not
    just the peak frame. ``history`` (optional): summary of previous reps in the
    same session or across recent sessions so stage 2 can talk about trend.
    """
    stage1 = extract_pose_features(
        image_path=image_path,
        image_url=image_url,
        exercise=exercise,
        image_paths=image_paths,
    )
    if not stage1.get("ok"):
        return {"ok": False, "stage_failed": "extract", "error": stage1.get("error"), "detail": stage1}
    features = stage1.get("features") or {}
    parse_status = stage1.get("parse_status")
    raw_reply = stage1.get("raw_reply") or ""
    # Loss-mitigation: if features JSON parse failed but we still have raw text,
    # forward the raw text to stage 2 as a fallback observation blob rather than
    # aborting the whole pipeline.
    if not features and raw_reply.strip():
        features = {
            "exercise_visible": exercise or "unknown",
            "raw_vision_text": raw_reply[:1500],
            "parse_failed": True,
        }
    if not features:
        return {
            "ok": False,
            "stage_failed": "extract",
            "error": "stage 1 returned no features and no raw text",
            "detail": stage1,
        }
    stage2 = reason_about_pose(
        features=features,
        exercise=exercise,
        rule_summary=rule_summary,
        user_context=user_context,
        evidence_citations=evidence_citations,
        time_series=time_series,
        history=history,
    )
    return {
        "ok": stage2.get("ok", False),
        "stage_extract": stage1,
        "stage_reason": stage2,
        "stage1_conflicts": stage2.get("stage1_conflicts") or [],
        "parse_status": parse_status,
        "note": "视觉观察 + 文本推理; 定量分优先信任规则打分器 (若已给); Stage 1 幻觉已预先检测并告知 Stage 2 降级。",
    }


# ======================== Session-level (组级) analysis ========================

_session_log = logging.getLogger('vision_pipeline.session')

def _session_reasoning_prompt(
    exercise: str,
    rep_summaries: List[Dict[str, Any]],
    stage1_results: List[Dict[str, Any]],
    user_context: Optional[Dict[str, Any]] = None,
    evidence_citations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a prompt for session-level Stage 2 text LLM."""
    parts = [
        '你是一位专业的健身教练与姿态分析师。',
        '',
        '下面是一组训练 (一组连续完成的 reps) 的完整数据。每一条包含:',
        '  1) 规则打分器对该 rep 的量化评分 (depth/control/symmetry/total, 0-100)',
        '  2) 视觉模型从该 rep 关键帧提取的客观姿态特征 (关节角度、对齐、对称性等)',
        '',
        f'目标动作: {exercise}',
        f'共 {len(rep_summaries)} 个 reps',
        '',
        '请综合所有信息, 输出严格 JSON, 结构如下, 不要 Markdown, 不要多余字符:',
        '{',
        '  "exercise": "squat",',
        '  "rep_count": 8,',
        '  "overall_assessment": {',
        '    "strengths": ["..."],',
        '    "common_issues": [{"issue": "...", "severity": "low|medium|high", "affected_reps": [1,3,5], "evidence": "..."}, ...],',
        '    "inconsistencies": ["第3-5个 rep 逐渐变浅, 第6个又恢复深度"],',
        '    "reps_with_concern": [2, 7],',
        '    "overall_score": 0-100,',
        '    "performance_rating": "excellent|good|fair|needs_improvement",',
        '  },',
        '  "rep_by_rep_notes": [',
        '    "rep1: 完成度高, 膝盖角度 82deg 达到 parallel",',
        '    "rep3: 深蹲过深 (91deg), 髂腰肌代偿风险, 建议控制下蹲深度",',
        '  ],',
        '  "guidance": {',
        '    "immediate_corrections": ["..."],',
        '    "next_session_focus": ["..."],',
        '    "progression_or_regression": "...",',
        '    "cautions": ["..."],',
        '  },',
        '  "data_gaps": ["..."],',
        '  "confidence": 0-1,',
        '}',
        '',
        '硬约束:',
        '- rep_by_rep_notes 里如果某 rep 有具体问题, 必须写 rep 编号 (1-indexed)。',
        '- 如果某个异常只出现在少量 rep 上, 在 common_issues 里注明 affected_reps。',
        '- guidance 每条必须具体到可执行的动作, 不要空话。',
        '- 严禁医疗诊断, 出现疼痛/不适进 cautions。',
        '- 不要凭空造数字, 只引用规则打分器给出的量化分。',
        '- **所有数值字段必须是 JSON 数字字面量, 严禁写 Math.round/Math.avg/表达式/公式/文字, 例如 overall_score 只能写 88, 不能写 (a+b)/n 或 Math.round(...)。**',
        '- **严禁尾随逗号 (trailing comma), JSON 必须严格合法可 json.loads 解析。**',
        '',
    ]
    if evidence_citations:
        parts.append('========= 可引用的运动学阈值 =========')
        parts.append(json.dumps(evidence_citations, ensure_ascii=False, indent=2))
    if user_context:
        parts.append('========= 用户上下文 =========')
        parts.append(json.dumps(user_context, ensure_ascii=False, indent=2))

    for i, (rep, s1) in enumerate(zip(rep_summaries, stage1_results)):
        parts.append(f'\n----- Rep {i+1} -----')
        parts.append('【规则打分】')
        parts.append(json.dumps(rep, ensure_ascii=False, indent=2))
        parts.append('【视觉特征】')
        parts.append(json.dumps(s1, ensure_ascii=False, indent=2))

    return '\n'.join(parts)


def analyze_session_two_stage(
    exercise: str,
    rep_summaries: List[Dict[str, Any]],
    rep_image_paths: List[Optional[str]],
    user_context: Optional[Dict[str, Any]] = None,
    evidence_citations: Optional[List[Dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Two-stage analysis for a whole session/set of reps."""
    # Stage 1: run vision on each rep key frame
    stage1_results: List[Dict[str, Any]] = []
    for i, img_path in enumerate(rep_image_paths):
        if not img_path or not os.path.isfile(img_path):
            stage1_results.append({
                'ok': False,
                'error': f'rep {i+1}: image not found: {img_path}',
                'rep_index': i,
            })
            continue
        result = extract_pose_features(
            image_path=img_path,
            exercise=exercise,
        )
        features = result.get('features') or {}
        raw = result.get('raw_reply') or ''
        if not features and raw.strip():
            features = {
                'exercise_visible': exercise or 'unknown',
                'raw_vision_text': raw[:1500],
                'parse_failed': True,
            }
        stage1_results.append({
            'ok': bool(result.get('ok')) or bool(features),
            'rep_index': i,
            'provider': result.get('provider'),
            'model': result.get('model'),
            'features': features,
            'raw_reply': raw[:2000],
            'error': result.get('error') if not result.get('ok') and not features else None,
        })

    # Stage 2: one text LLM call with all context (with provider fallback)
    providers = _reasoning_providers()
    if not providers:
        return {'ok': False, 'error': 'no text LLM provider configured (set DEEPSEEK_API_KEY or VOLC_ARK_API_KEY)', 'recoverable': True, 'stage1_results': stage1_results}

    prompt = _session_reasoning_prompt(
        exercise=exercise,
        rep_summaries=rep_summaries,
        stage1_results=[s['features'] for s in stage1_results],
        user_context=user_context,
        evidence_citations=evidence_citations,
    )
    max_tokens = int(os.environ.get('AI_AGENT_VISION_REASON_MAX_TOKENS', '6000'))
    tried: List[Dict[str, Any]] = []
    for prov in providers:
        payload = {
            'model': prov['model'],
            'messages': [
                {'role': 'system', 'content': '你只输出符合 schema 的 JSON, 严禁编造数据, 严禁做医疗诊断。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.2,
            'max_tokens': max_tokens,
        }
        try:
            resp = requests.post(
                prov['url'],
                headers={'Authorization': f'Bearer {prov["api_key"]}', 'Content-Type': 'application/json'},
                json=payload,
                timeout=timeout or float(os.environ.get('AI_AGENT_VISION_REASON_TIMEOUT', '120')),
            )
        except requests.RequestException as exc:
            log.warning('session stage2 %s network error: %s', prov['provider'], exc)
            tried.append({'provider': prov['provider'], 'model': prov['model'], 'error': f'network:{exc}'})
            continue
        if resp.status_code != 200:
            log.warning('session stage2 %s HTTP %s: %s', prov['provider'], resp.status_code, resp.text[:200])
            tried.append({'provider': prov['provider'], 'model': prov['model'], 'http': resp.status_code, 'body': resp.text[:200]})
            continue
        try:
            data = resp.json()
            raw_content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        except Exception as exc:
            tried.append({'provider': prov['provider'], 'model': prov['model'], 'error': f'response_parse:{exc}'})
            continue
        parsed = _parse_json_object(raw_content)
        return {
            'ok': True,
            'exercise': exercise,
            'rep_count': len(rep_summaries),
            'stage1_results': stage1_results,
            'stage_reason': {
                'provider': prov['provider'],
                'model': prov['model'],
                'raw_reply': raw_content[:4000],
                'analysis': parsed,
                'tried_before': tried,
            },
        }
    return {
        'ok': False,
        'error': 'all text LLM providers failed',
        'error_type': 'llm_all_providers_failed',
        'tried': tried,
        'recoverable': True,
        'stage1_results': stage1_results,
    }
