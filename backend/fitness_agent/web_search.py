"""Controlled web search for the Smart Fitness Agent.

This is not a general network tool. It only sends sanitized fitness-related
queries to a public search endpoint, returns small snippets, and never accepts
arbitrary URLs or HTTP methods from the model.

Relevance is judged by an LLM guard so the boundary is semantic, not a brittle
keyword list. A small hard-deny list remains for secrets/abuse/off-domain risks.
"""
import html
import json
import os
import re
from typing import Any, Dict, List
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

import ai_planner

HARD_DENY_HINTS = {
    "password", "passwd", "token", "api key", "apikey", "secret", "credential", "cookie",
    "破解", "盗号", "密码", "密钥", "令牌", "凭证", "赌博", "彩票", "成人", "色情",
}

TRUSTED_HINTS = [
    "who.int", "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "mayoclinic.org", "clevelandclinic.org", "healthline.com", "acefitness.org",
    "acsm.org", "sportsmedicine.org", "eatright.org",
]

FALLBACK_FITNESS_HINTS = {
    "健身", "训练", "运动", "跑步", "长跑", "力量", "增肌", "减脂", "减重", "体脂",
    "营养", "饮食", "蛋白", "碳水", "脂肪", "热量", "卡路里", "恢复", "睡眠", "拉伸",
    "深蹲", "俯卧撑", "弓步", "平板支撑", "心率", "耐力", "肌肉", "伤病", "疼痛",
    "fitness", "exercise", "workout", "training", "running", "strength", "hypertrophy",
    "fat loss", "weight loss", "nutrition", "protein", "carbohydrate", "calorie", "recovery",
    "sleep", "stretching", "squat", "push up", "lunge", "plank", "endurance", "sports medicine",
}


def _enabled() -> bool:
    return os.environ.get("AI_AGENT_WEB_SEARCH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _llm_guard_enabled() -> bool:
    return os.environ.get("AI_AGENT_WEB_SEARCH_LLM_GUARD", "true").strip().lower() not in {"0", "false", "no", "off"}


def _strip_tags(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _sanitize_query(query: str) -> str:
    query = (query or "").strip()
    query = re.sub(r"[\r\n\t]+", " ", query)
    query = re.sub(r"\s+", " ", query)
    query = re.sub(r"\b[A-Za-z0-9_\-]{24,}\b", "", query)
    return query[:160].strip()


def _hard_denied(query: str) -> bool:
    q = query.lower()
    return any(h in q for h in HARD_DENY_HINTS)


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    raw = text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.lstrip().startswith("json"):
                raw = raw.lstrip()[4:]
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _fallback_judge(query: str) -> Dict[str, Any]:
    q = query.lower()
    allowed = any(h.lower() in q for h in FALLBACK_FITNESS_HINTS)
    return {
        "allowed": allowed,
        "reason": "fallback fitness hint matched" if allowed else "fallback judge found no clear fitness/training/nutrition relation",
        "category": "fitness" if allowed else "off_domain",
        "search_query": query,
        "fallback": True,
    }


def judge_fitness_web_search(query: str) -> Dict[str, Any]:
    """Use an LLM to decide whether a search query is appropriate for this Agent.

    The LLM is not trusted for hard safety: secrets/abuse hints are denied before
    this function's result is accepted. The LLM only handles semantic relevance.
    """
    query = _sanitize_query(query)
    if _hard_denied(query):
        return {"allowed": False, "reason": "hard-denied sensitive or abusive query", "category": "blocked", "search_query": query}
    if not _llm_guard_enabled():
        return _fallback_judge(query)

    messages = [
        {
            "role": "system",
            "content": """你是 Smart Fitness Agent 的联网搜索守卫。
判断用户/Agent 提出的搜索 query 是否应该允许联网搜索。
允许范围：健身、运动训练、营养、恢复、动作技术、运动伤病预防、运动科学、健康科普中与训练建议相关的内容。
拒绝范围：和健身无关的泛搜索、账号/密钥/隐私/破解/赌博/成人内容、股票金融、政治宣传、任意 URL 抓取请求。
如果允许，请可选改写成更适合搜索的 query。
只输出 JSON：{"allowed":true/false,"category":"nutrition|training|recovery|exercise_technique|sports_health|off_domain|blocked","reason":"...","search_query":"..."}""",
        },
        {"role": "user", "content": f"query: {query}"},
    ]
    try:
        raw = ai_planner._call_llm(
            messages,
            max_tokens=300,
            temperature=0.0,
            chain=os.environ.get("AI_AGENT_WEB_SEARCH_GUARD_CHAIN", os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan")),
        )
        obj = _extract_json_object(raw or "")
    except Exception as exc:
        obj = {"_error": f"{type(exc).__name__}: {exc}"}
    if not obj or not isinstance(obj.get("allowed"), bool):
        out = _fallback_judge(query)
        if obj.get("_error"):
            out["guard_error"] = obj["_error"]
        return out
    search_query = _sanitize_query(str(obj.get("search_query") or query))
    if _hard_denied(search_query):
        return {"allowed": False, "reason": "rewritten query hit hard-deny guard", "category": "blocked", "search_query": query}
    return {
        "allowed": bool(obj.get("allowed")),
        "reason": str(obj.get("reason") or ""),
        "category": str(obj.get("category") or "fitness"),
        "search_query": search_query or query,
    }


def _decode_ddg_url(url: str) -> str:
    url = html.unescape(url or "")
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    return url


def _parse_duckduckgo_html(text: str, limit: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    blocks = re.split(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>', text)
    for block in blocks:
        if len(results) >= limit:
            break
        m = re.search(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S | re.I)
        if not m:
            continue
        url = _decode_ddg_url(m.group(1))
        title = _strip_tags(m.group(2))
        snippet = ""
        sm = re.search(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', block, flags=re.S | re.I)
        if not sm:
            sm = re.search(r'<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', block, flags=re.S | re.I)
        if sm:
            snippet = _strip_tags(sm.group(1))
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if not title or not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": title[:180],
            "url": url[:500],
            "domain": domain,
            "snippet": snippet[:420],
            "trusted_hint": any(h in domain for h in TRUSTED_HINTS),
        })
    return results


def search_fitness_web(query: str, limit: int = 5) -> Dict[str, Any]:
    """Search the web for fitness/health/nutrition information.

    Returns snippets only. No arbitrary URL fetch is exposed to the model.
    """
    if not _enabled():
        return {"ok": False, "error": "web search disabled by AI_AGENT_WEB_SEARCH_ENABLED"}
    query = _sanitize_query(query)
    if len(query) < 3:
        return {"ok": False, "error": "query too short"}

    decision = judge_fitness_web_search(query)
    if not decision.get("allowed"):
        return {"ok": False, "error": "query rejected by LLM web-search guard", "query": query, "decision": decision}

    search_query = _sanitize_query(decision.get("search_query") or query)
    limit = max(1, min(int(limit or 5), int(os.environ.get("AI_AGENT_WEB_SEARCH_MAX_RESULTS", "5"))))
    timeout = float(os.environ.get("AI_AGENT_WEB_SEARCH_TIMEOUT", "8"))
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(search_query)
    headers = {"User-Agent": "Mozilla/5.0 SmartFitnessAgent/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        results = _parse_duckduckgo_html(resp.text, limit=limit)
    except Exception as exc:
        return {"ok": False, "error": f"web search failed: {type(exc).__name__}: {exc}", "query": query, "decision": decision, "results": []}
    return {
        "ok": True,
        "query": query,
        "search_query": search_query,
        "decision": decision,
        "source": "duckduckgo_html",
        "results": results,
        "note": "Search snippets may be incomplete; prefer authoritative sources and cite URLs in the final answer.",
    }
