"""llm_coach.py - B-06 OpenClaw LLM 动作教练

对低 form_score / 错误动作生成 30 字以内中文点评。
依赖: DeepSeek API (env DEEPSEEK_API_KEY)
缓存到内存 + JSON 文件，避免重复调用唤金。
"""
import os, json, time, hashlib, threading, logging
from typing import Optional, Dict, List, Any

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger("llm_coach")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(ROOT, "llm_coach_cache.json")

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
TIMEOUT = 20
MAX_TOKENS = 120

_lock = threading.Lock()
_mem_cache: Dict[str, Dict[str, Any]] = {}
_cache_loaded = False


def _load_cache():
    global _cache_loaded
    if _cache_loaded:
        return
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _mem_cache.update(json.load(f))
        except Exception as e:
            log.warning(f"cache load failed: {e}")
    _cache_loaded = True


def _save_cache():
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_mem_cache, f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"cache save failed: {e}")


def _make_key(exercise_type: str, form_score: float, feedback_summary: str) -> str:
    # 按 (exercise + score 档 + feedback hash) 缓存, 避免重复 LLM 调用
    bucket = int(form_score // 10) if form_score is not None else -1
    raw = f"{exercise_type}|{bucket}|{feedback_summary}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _build_prompt(exercise_type: str, form_score: float, feedback_list: List[Dict], body_context: Optional[Dict], plan_match: Optional[Dict]) -> str:
    fb_text = "\n".join(f"- {fb.get('message_cn','')}" for fb in feedback_list[:3]) or "(无明显问题)"
    bmi_line = ""
    if body_context and body_context.get("bmi"):
        bmi_line = f"用户 BMI={body_context['bmi']}, 强度档={body_context.get('recommended_intensity','normal')}."
    plan_line = ""
    if plan_match and plan_match.get("in_plan"):
        plan_line = f"今日计划: {plan_match.get('plan_name','')}, 完成度 {plan_match.get('progress_pct') or 0}%."
    return f"""你是身体动作教练。用户正在做 {exercise_type}, 动作质量评分 {form_score or 0}/100.
{bmi_line}
{plan_line}
检测到的问题:
{fb_text}

请用 30 字以内中文说一句口语化的纠正提示，只说最重要的一点。不加前缀、不报评分、不加表情。例: “脓头下沉多一点身体别前倾。”"""


def _call_deepseek(prompt: str) -> Optional[str]:
    if not API_KEY or not requests:
        return None
    try:
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": MAX_TOKENS,
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"DeepSeek HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"DeepSeek call failed: {e}")
        return None


def get_coach_tip(exercise_type: str, form_score: Optional[float], feedback_list: List[Dict],
                  body_context: Optional[Dict] = None, plan_match: Optional[Dict] = None,
                  trigger_threshold: float = 80.0) -> Optional[str]:
    """返回 30 字点评 或 None (禁用 / 不需要)。
    - form_score >= threshold 的不生成 (动作已够好)
    - 无 API key 返 None (静默降级)
    - 命中缓存直接返
    """
    if not exercise_type:
        return None
    if form_score is not None and form_score >= trigger_threshold:
        return None
    if not feedback_list:
        return None

    fb_summary = "|".join(fb.get("message_cn", "")[:30] for fb in feedback_list[:3])
    key = _make_key(exercise_type, form_score or 0, fb_summary)

    with _lock:
        _load_cache()
        cached = _mem_cache.get(key)
        if cached and (time.time() - cached.get("ts", 0)) < 7 * 86400:
            return cached.get("tip")

    prompt = _build_prompt(exercise_type, form_score or 0, feedback_list, body_context, plan_match)
    tip = _call_deepseek(prompt)
    if not tip:
        # 降级提示 (不调 LLM 也能给出一句)
        tip = feedback_list[0].get("message_cn", "")[:30] if feedback_list else None
        if tip and len(tip) > 30:
            tip = tip[:30]
    if tip:
        with _lock:
            _mem_cache[key] = {"tip": tip, "ts": time.time()}
            _save_cache()
    return tip


def is_available() -> bool:
    return bool(API_KEY and requests)
