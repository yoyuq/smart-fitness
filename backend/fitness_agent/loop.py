"""Lightweight tool-calling loop for the Smart Fitness Agent.

The loop stays deliberately small: LLM -> tool_calls -> tool_results -> LLM.
Runtime/state, knowledge loading, permission checks and hooks live around it.
"""
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional

import ai_planner
from . import state
from .compact import compact_value
from .core import _fallback_nutrition, detect_domains
from .hooks import trigger_hooks
from .knowledge_loader import knowledge_ids, load_knowledge
from .memory import infer_memory_kind
from .permissions import list_pending_approvals
from .prompts import build_system_prompt
from .tools import execute_tool

MAX_AGENT_TURNS = int(os.environ.get("AI_AGENT_LOOP_MAX_TURNS", "4"))
AGENT_TOTAL_TIMEOUT_SEC = float(os.environ.get("AI_AGENT_TOTAL_TIMEOUT", "30"))
AGENT_TOOL_TIMEOUT_SEC = float(os.environ.get("AI_AGENT_TOOL_TIMEOUT", "8"))
MAX_INLINE_TOOL_RESULTS = int(os.environ.get("AI_AGENT_INLINE_TOOL_RESULTS", "6"))
PROVIDER_FAILURE_THRESHOLD = int(os.environ.get("AI_AGENT_PROVIDER_FAILURE_THRESHOLD", "3"))
PROVIDER_FAILURE_WINDOW_SEC = int(os.environ.get("AI_AGENT_PROVIDER_FAILURE_WINDOW_SEC", "300"))
PROVIDER_COOLDOWN_SEC = int(os.environ.get("AI_AGENT_PROVIDER_COOLDOWN_SEC", "300"))
_PROVIDER_BREAKERS: Dict[str, Dict[str, Any]] = {}


def _now_ts() -> float:
    return time.time()


def _provider_breaker(provider: str) -> Dict[str, Any]:
    return _PROVIDER_BREAKERS.setdefault(provider, {"failures": [], "cooldown_until": 0.0})


def _provider_in_cooldown(provider: str, now: Optional[float] = None) -> bool:
    state = _provider_breaker(provider)
    return float(state.get("cooldown_until") or 0) > (now if now is not None else _now_ts())


def _record_provider_failure(provider: str, now: Optional[float] = None) -> bool:
    now = now if now is not None else _now_ts()
    state = _provider_breaker(provider)
    failures = [t for t in state.get("failures", []) if now - float(t) <= PROVIDER_FAILURE_WINDOW_SEC]
    failures.append(now)
    state["failures"] = failures
    if len(failures) >= PROVIDER_FAILURE_THRESHOLD:
        state["cooldown_until"] = now + PROVIDER_COOLDOWN_SEC
        return True
    return False


def _record_provider_success(provider: str) -> None:
    state = _provider_breaker(provider)
    state["failures"] = []
    state["cooldown_until"] = 0.0


def _persist_provider_success(conn, provider: str) -> None:
    if conn is None or not provider:
        return
    try:
        state.record_provider_success(conn, provider)
    except Exception:
        pass


def _persist_provider_failure(conn, provider: str, error_type: str, message: str) -> None:
    if conn is None or not provider:
        return
    try:
        state.record_provider_failure(
            conn,
            provider,
            threshold=PROVIDER_FAILURE_THRESHOLD,
            cooldown_sec=PROVIDER_COOLDOWN_SEC,
            error_type=error_type,
            message=message,
        )
    except Exception:
        pass


def _call_llm_compat(messages: List[Dict[str, str]], **kwargs) -> str:
    try:
        return ai_planner._call_llm(messages, **kwargs) or ""
    except TypeError as exc:
        # Older tests/mocks may not accept the newer timeout kwarg.
        if "timeout" in kwargs and "timeout" in str(exc):
            retry = dict(kwargs)
            retry.pop("timeout", None)
            return ai_planner._call_llm(messages, **retry) or ""
        raise


def _recovery_event(event: str, **data: Any) -> Dict[str, Any]:
    return {"event": event, **data}


def _safe_call_llm(messages: List[Dict[str, str]], recovery: List[Dict[str, Any]], **kwargs) -> str:
    """Call the configured LLM chain without letting provider failures abort a run.

    When an explicit comma-separated chain is supplied, try providers one by one
    so a hard failure in the first LLM can switch to the next LLM before the
    agent degrades to local fallback text. Agent chat uses a shorter per-provider
    timeout than the global planner timeout and a small in-memory circuit breaker.

    ``deadline`` is an optional absolute timestamp for the whole Agent run.  The
    provider chain must not reuse the full remaining budget for every provider;
    otherwise 4 providers * 25s can turn one App request into a minute-plus hang.
    """
    call_kwargs = dict(kwargs)
    health_conn = call_kwargs.pop("conn", None)
    deadline = call_kwargs.pop("deadline", None)
    base_timeout = float(call_kwargs.pop("timeout", getattr(ai_planner, "AGENT_LLM_TIMEOUT", 25)) or getattr(ai_planner, "AGENT_LLM_TIMEOUT", 25))
    chain = call_kwargs.get("chain")
    providers = [p.strip() for p in str(chain or "").split(",") if p.strip()]

    def _timeout_for_next_provider() -> Optional[float]:
        if deadline is None:
            return max(0.1, base_timeout)
        remaining = float(deadline) - time.time()
        if remaining <= 0:
            return None
        return max(0.1, min(base_timeout, remaining))

    if len(providers) > 1:
        had_failure = False
        for provider in providers:
            provider_timeout = _timeout_for_next_provider()
            if provider_timeout is None:
                recovery.append(_recovery_event("total_timeout", timeout_sec=AGENT_TOTAL_TIMEOUT_SEC))
                return ""
            if _provider_in_cooldown(provider):
                had_failure = True
                breaker = _provider_breaker(provider)
                recovery.append(_recovery_event(
                    "provider_skipped",
                    provider=provider,
                    reason="cooldown",
                    cooldown_until=int(breaker.get("cooldown_until") or 0),
                ))
                continue
            one_kwargs = dict(call_kwargs)
            one_kwargs["chain"] = provider
            one_kwargs["timeout"] = provider_timeout
            try:
                out = _call_llm_compat(messages, **one_kwargs)
            except Exception as exc:
                had_failure = True
                opened = _record_provider_failure(provider)
                _persist_provider_failure(health_conn, provider, type(exc).__name__, str(exc)[:500])
                recovery.append(_recovery_event(
                    "provider_error",
                    provider=provider,
                    error_type=type(exc).__name__,
                    message=str(exc)[:500],
                    circuit_opened=opened,
                ))
                continue
            if out:
                _record_provider_success(provider)
                _persist_provider_success(health_conn, provider)
                if had_failure:
                    recovery.append(_recovery_event("provider_recovered", provider=provider))
                return out
            had_failure = True
            opened = _record_provider_failure(provider)
            _persist_provider_failure(health_conn, provider, "empty", "provider returned empty response")
            recovery.append(_recovery_event("provider_empty", provider=provider, circuit_opened=opened))
        return ""

    provider = providers[0] if providers else None
    provider_timeout = _timeout_for_next_provider()
    if provider_timeout is None:
        recovery.append(_recovery_event("total_timeout", timeout_sec=AGENT_TOTAL_TIMEOUT_SEC))
        return ""
    if provider and _provider_in_cooldown(provider):
        breaker = _provider_breaker(provider)
        recovery.append(_recovery_event(
            "provider_skipped",
            provider=provider,
            reason="cooldown",
            cooldown_until=int(breaker.get("cooldown_until") or 0),
        ))
        return ""
    call_kwargs["timeout"] = provider_timeout
    try:
        out = _call_llm_compat(messages, **call_kwargs)
        if out and provider:
            _record_provider_success(provider)
            _persist_provider_success(health_conn, provider)
        elif provider:
            opened = _record_provider_failure(provider)
            _persist_provider_failure(health_conn, provider, "empty", "provider returned empty response")
            recovery.append(_recovery_event("provider_empty", provider=provider, circuit_opened=opened))
        return out
    except Exception as exc:
        opened = _record_provider_failure(provider) if provider else False
        if provider:
            _persist_provider_failure(health_conn, provider, type(exc).__name__, str(exc)[:500])
        recovery.append(_recovery_event(
            "provider_error",
            provider=provider,
            error_type=type(exc).__name__,
            message=str(exc)[:500],
            circuit_opened=opened,
        ))
        return ""


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


def _looks_like_agent_json(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or "{" not in raw or "}" not in raw:
        return False
    markers = ["tool_calls", "final", "reply", "answer", "memory_notes"]
    return any(m in raw for m in markers)


def _sanitize_leaked_agent_json(text: str) -> str:
    """Last-resort: if the model returned raw ``{"final": "..."}`` that neither
    the primary JSON extractor nor the repair pass could parse, try to pull the
    ``final`` value out manually so the user does not see raw JSON.
    """
    if not text:
        return text
    stripped = text.strip()
    if not stripped.startswith("{") or '"final"' not in stripped:
        return text
    # Try to find the first "final": "..." string block (allowing literal newlines).
    match = re.search(r'"final"\s*:\s*"(.*?)(?<!\\)"\s*(?:,|})', stripped, re.S)
    if not match:
        return text
    payload = match.group(1)
    # Unescape common escape sequences we know the LLM emits.
    payload = payload.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return payload.strip() or text


def _repair_json_object(raw: str, recovery: List[Dict[str, Any]], conn=None, deadline: Optional[float] = None) -> Dict[str, Any]:
    """One-shot repair for model outputs that are intended as JSON but malformed."""
    if not _looks_like_agent_json(raw):
        return {}
    repair_msgs = [
        {
            "role": "system",
            "content": "你是 JSON 修复器。只修复为严格 JSON 对象，不解释，不添加 Markdown。字段只能保留 tool_calls/final/reply/answer/memory_notes。",
        },
        {
            "role": "user",
            "content": "请修复下面 Smart Fitness Agent 输出为可解析 JSON，只输出 JSON：\n" + raw[:4000],
        },
    ]
    fixed = _safe_call_llm(
        repair_msgs,
        recovery,
        conn=conn,
        deadline=deadline,
        max_tokens=1200,
        temperature=0.0,
        chain=os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan"),
    )
    obj = _extract_json_object(fixed)
    if obj:
        recovery.append(_recovery_event("json_repair", ok=True))
        return obj
    recovery.append(_recovery_event("json_repair", ok=False, sample=(raw or "")[:300]))
    return {}


def _context_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": ctx.get("user_id"),
        "username": ctx.get("username"),
        "body": ctx.get("body"),
        "streak_days": ctx.get("streak_days"),
        "today_exercises": ctx.get("today_exercises", [])[:8],
        "weekly_summary": ctx.get("weekly_summary", [])[:14],
        "per_exercise": ctx.get("per_exercise", [])[:10],
        "plans": ctx.get("plans", [])[:3],
        "coach_memory": ctx.get("coach_memory", [])[:10],
    }


def _build_system(domains: List[str], ctx: Dict[str, Any]) -> str:
    return build_system_prompt(domains, ctx, _fallback_nutrition(ctx))


def _pending_approvals_from_trace(conn, user_id: int, trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    approvals = [
        item.get("result", {}).get("approval")
        for item in trace
        if item.get("result", {}).get("permission") == "pending" and item.get("result", {}).get("approval")
    ]
    if approvals:
        return approvals
    # Defensive fallback: permission hooks persist approvals in DB. If a future
    # hook shape changes and the trace no longer carries approval objects, the
    # API response must still surface them so the App can show the approval UI.
    return list_pending_approvals(conn, user_id, limit=10)


def _todos_from_trace(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in reversed(trace):
        if item.get("name") == "todo_write" and isinstance(item.get("result"), dict):
            todos = item["result"].get("todos") or []
            return todos if isinstance(todos, list) else []
    return []


def _format_todo_status_block(todos: List[Dict[str, Any]]) -> str:
    if not todos:
        return ""
    lines = []
    for idx, todo in enumerate(todos, 1):
        if not isinstance(todo, dict):
            continue
        content = str(todo.get("content") or "").strip()[:120]
        if not content:
            continue
        status = str(todo.get("status") or "pending").strip() or "pending"
        marker = {
            "completed": "[x]",
            "in_progress": "[~]",
            "waiting_approval": "[?]",
            "cancelled": "[-]",
            "pending": "[ ]",
        }.get(status, "[ ]")
        lines.append(f"{idx}. {marker} {content} <{status}>")
    if not lines:
        return ""
    return "当前 TODO 状态（每轮请核对/推进）：\n" + "\n".join(lines)


def _compact_tool_results_for_prompt(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shrink per-turn tool results before feeding them back to the LLM.

    Large search/kb/context results can easily blow the model's context window.
    We keep only ``name/args/permission/result`` and pass through ``compact_value``
    so a single tool cannot dominate the prompt for the next turn.
    """
    compacted: List[Dict[str, Any]] = []
    for item in (results or [])[-MAX_INLINE_TOOL_RESULTS:]:
        if not isinstance(item, dict):
            continue
        compacted.append({
            "name": item.get("name"),
            "args": compact_value(item.get("args") or {}),
            "permission": compact_value(item.get("permission") or {}),
            "result": compact_value(item.get("result") or {}),
        })
    return compacted


def _plan_draft_from_trace(trace: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in reversed(trace):
        if item.get("name") != "draft_workout_plan" or not isinstance(item.get("result"), dict):
            continue
        result = item.get("result") or {}
        if result.get("ok") and result.get("draft") and isinstance(result.get("exercises"), list):
            return {
                "name": result.get("name") or "Agent 生成计划",
                "goal": result.get("goal"),
                "weeks": result.get("weeks"),
                "reason": result.get("reason"),
                "exercises": result.get("exercises") or [],
            }
    return None


def _forced_identity_reply(message: str) -> Optional[str]:
    msg = (message or "").strip().lower()
    if not msg:
        return None
    model_words = ["模型", "架构", "底层", "model", "architecture", "llm", "大模型"]
    ask_words = ["什么", "哪个", "哪种", "是谁", "当前", "用的", "based", "what", "which"]
    if any(w in msg for w in model_words) and any(w in msg for w in ask_words):
        return "我是 Smart Fitness 专属健身 Agent，当前由后端配置的 LLM 调用链驱动。"
    return None


def _forced_body_metric_tool_call(message: str) -> Optional[Dict[str, Any]]:
    """Deterministically route clear body-metric write requests to approval.

    LLMs sometimes answer "please confirm" in prose instead of emitting the
    required write-tool JSON. For explicit metric update commands, skip the
    ambiguity and create the normal App approval request through permissions.
    This does not write data; it only produces a pending approval.
    """
    msg = (message or "").strip()
    if not msg:
        return None
    lower = msg.lower()
    has_metric_word = any(w in lower for w in ["weight", "height", "body fat", "fat %"]) or any(w in msg for w in ["体重", "身高", "体脂"])
    if not has_metric_word:
        return None
    write_intent = any(w in lower for w in ["update", "set", "record", "save", "change"])
    write_intent = write_intent or any(w in msg for w in ["更新", "修改", "改成", "改为", "记录", "保存", "设置", "设为"])
    if not write_intent:
        return None

    args: Dict[str, Any] = {}
    weight_patterns = [
        r"(?:weight|body\s*weight)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)?",
        r"体重[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)?",
    ]
    height_patterns = [
        r"height[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:cm|厘米)?",
        r"身高[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:cm|厘米)?",
    ]
    body_fat_patterns = [
        r"(?:body\s*fat|fat)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*%?",
        r"体脂[^0-9]{0,12}(\d+(?:\.\d+)?)\s*%?",
    ]

    def first_number(patterns: List[str]) -> Optional[float]:
        for pattern in patterns:
            m = re.search(pattern, msg, re.I)
            if m:
                try:
                    return float(m.group(1))
                except Exception:
                    return None
        return None

    weight = first_number(weight_patterns)
    height = first_number(height_patterns)
    body_fat = first_number(body_fat_patterns)
    if weight is not None:
        args["weight_kg"] = weight
    if height is not None:
        args["height_cm"] = height
    if body_fat is not None:
        args["body_fat_pct"] = body_fat
    if not args:
        return None
    args["notes"] = "Agent 根据用户明确请求更新"
    return {"name": "update_body_metrics", "args": args}


def _forced_memory_tool_call(message: str) -> Optional[Dict[str, Any]]:
    """Deterministically route explicit "remember this" requests to approval.

    The normal loop can already call ``save_coach_memory``, but this catches the
    most common App flow even when the LLM answers in prose or is temporarily
    unavailable. It still does not write data; the permission hook creates a
    pending App approval exactly like a model-emitted write tool call.
    """
    msg = (message or "").strip()
    if not msg:
        return None
    lower = msg.lower()
    has_memory_intent = any(
        w in msg for w in ["记住", "记一下", "记下", "保存这条", "保存一下", "以后记得", "你要记得", "帮我记"]
    ) or any(
        w in lower for w in ["remember that", "remember:", "save this", "save note", "note that"]
    )
    if not has_memory_intent:
        return None

    note = msg
    patterns = [
        r"^(?:请)?(?:帮我)?(?:把)?(?:这个|这条)?(?:记住|记一下|记下|保存这条|保存一下|帮我记)(?:一下)?[：:,，\s]*(.+)$",
        r"^(?:以后)?(?:你要)?记得[：:,，\s]*(.+)$",
        r"^remember(?:\s+that)?[：:,\s]+(.+)$",
        r"^save(?:\s+this|\s+note)?[：:,\s]+(.+)$",
        r"^note(?:\s+that)?[：:,\s]+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg, re.I)
        if m:
            note = m.group(1).strip()
            break
    note = re.sub(r"^(我说的是|内容是|这件事是)[：:,，\s]*", "", note).strip()
    note = re.sub(r"[。.!！\s]*(谢谢|thx|thanks)?$", "", note, flags=re.I).strip()
    if len(note) < 4:
        return None
    return {"name": "save_coach_memory", "args": {"note": note[:500], "kind": infer_memory_kind(note)}}


def _approval_reply_for_forced_call(call: Dict[str, Any], item: Dict[str, Any]) -> str:
    if call.get("name") == "save_coach_memory":
        note = (call.get("args") or {}).get("note") or "这条长期记忆"
        if item.get("result", {}).get("permission") == "pending":
            return f"我已准备保存长期记忆：{note}。需要你在 App 弹窗确认后才会写入。"
        result = item.get("result") or {}
        if result.get("ok"):
            return f"已保存长期记忆：{note}。"
        return f"长期记忆未保存：{result.get('error') or result.get('message') or '需要重新确认'}"

    args = call.get("args") or {}
    parts = []
    if args.get("weight_kg") is not None:
        parts.append(f"体重 {args.get('weight_kg')}kg")
    if args.get("height_cm") is not None:
        parts.append(f"身高 {args.get('height_cm')}cm")
    if args.get("body_fat_pct") is not None:
        parts.append(f"体脂 {args.get('body_fat_pct')}%")
    target = "，".join(parts) or "身体指标"
    if item.get("result", {}).get("permission") == "pending":
        return f"我已准备更新身体指标：{target}。需要你在 App 弹窗确认后才会写入。"
    result = item.get("result") or {}
    if result.get("ok"):
        return f"已更新身体指标：{target}。"
    return f"身体指标更新未执行：{result.get('error') or result.get('message') or '需要重新确认'}"


def _db_path_for_conn(conn) -> Optional[str]:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return row[2] if row and len(row) > 2 else None
    except Exception:
        return None


def _open_worker_conn(db_path: Optional[str]):
    if not db_path:
        return None
    worker = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    worker.row_factory = sqlite3.Row
    return worker


def _execute_tool_with_timeout(conn, user_id: int, name: str, args: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    db_path = _db_path_for_conn(conn)
    def _work():
        worker_conn = _open_worker_conn(db_path)
        try:
            if worker_conn is None:
                return {"ok": False, "error": "worker db connection unavailable", "error_type": "tool_exception", "recoverable": True}
            return execute_tool(worker_conn, user_id, name, args)
        finally:
            if worker_conn is not None:
                worker_conn.close()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_work)
    try:
        return future.result(timeout=max(0.1, float(timeout_sec or AGENT_TOOL_TIMEOUT_SEC)))
    except FuturesTimeoutError:
        future.cancel()
        return {"ok": False, "error": f"tool timed out after {timeout_sec}s", "error_type": "tool_timeout", "recoverable": True}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_tool_with_hooks(
    conn,
    user_id: int,
    name: str,
    args: Dict[str, Any],
    turn: int,
    hook_log: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one tool through PreToolUse/PostToolUse hooks."""
    payload = {
        "conn": conn,
        "user_id": user_id,
        "name": name,
        "args": args,
        "turn": turn,
        "hook_log": hook_log,
        "run_id": run_id,
    }
    blocked = trigger_hooks("PreToolUse", payload)
    if blocked:
        decision = blocked.get("permission") or payload.get("permission") or {}
        result = blocked.get("result") or {"ok": False, "error": "blocked by hook"}
        return {"name": name, "args": args, "permission": decision, "result": result}

    decision = payload.get("permission") or {"behavior": "allow", "reason": "只读工具，直接允许"}
    try:
        result = _execute_tool_with_timeout(conn, user_id, name, args, AGENT_TOOL_TIMEOUT_SEC)
        post_payload = {
            "conn": conn,
            "user_id": user_id,
            "name": name,
            "args": args,
            "permission": decision,
            "result": result,
            "turn": turn,
            "hook_log": hook_log,
            "run_id": run_id,
        }
        trigger_hooks("PostToolUse", post_payload)
        result = post_payload.get("result", result)
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc)[:500],
            "error_type": "tool_exception",
            "exception_type": type(exc).__name__,
            "recoverable": True,
        }
        hook_log.append({
            "event": "ToolException",
            "name": name,
            "turn": turn,
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        })
    return {"name": name, "args": args, "permission": decision, "result": result}


def _fallback_answer(
    conn,
    user_id: int,
    message: str,
    mode: str,
    history: Optional[List[Dict[str, str]]],
    domains: List[str],
    ctx: Dict[str, Any],
    trace: List[Dict[str, Any]],
    hook_log: Optional[List[Dict[str, Any]]] = None,
    recovery: Optional[List[Dict[str, Any]]] = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """One-shot fallback when the JSON loop fails or reaches max turns."""
    kb = "\n".join(item["content"] for item in load_knowledge(domains, max_chars=6000))
    ctx_block = ai_planner._build_user_context_block(ctx)
    system = f"""你是 Smart Fitness 的专属健身 Agent。结合用户数据和已加载知识库回答中文，具体可执行，不要说“作为AI”。
{kb}
{ctx_block}"""
    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-8:]:
        msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    if trace:
        msgs.append({"role": "user", "content": "以下是已查询到的工具结果，请据此回答：\n" + json.dumps(trace[-6:], ensure_ascii=False)})
    msgs.append({"role": "user", "content": message})
    recovery = recovery if recovery is not None else []
    text = ""
    remaining_timeout = None
    if deadline is not None:
        remaining = float(deadline) - time.time()
        if remaining <= 0:
            recovery.append(_recovery_event("total_timeout", timeout_sec=AGENT_TOTAL_TIMEOUT_SEC))
        else:
            remaining_timeout = max(0.1, min(float(getattr(ai_planner, "AGENT_LLM_TIMEOUT", 25)), remaining))
    if deadline is None or remaining_timeout is not None:
        text = _safe_call_llm(
            msgs,
            recovery,
            conn=conn,
            deadline=deadline,
            timeout=remaining_timeout or getattr(ai_planner, "AGENT_LLM_TIMEOUT", 25),
            max_tokens=1800,
            temperature=0.35 if "nutrition" in domains else 0.45,
            chain=os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan"),
        )
    if not text and "nutrition" in domains:
        text = _fallback_nutrition(ctx)
    if not text:
        text = "我现在连不上大模型，但可以先看你的训练数据：近 28 天主要训练是 " + \
               ", ".join([f"{x.get('exercise')} {x.get('total_reps')}个" for x in ctx.get("per_exercise", [])[:3]]) + \
               "。你可以稍后再让我生成完整方案。"
    return {
        "ok": True,
        "mode": mode,
        "domains": domains,
        "reply": text,
        "context": _context_snapshot(ctx),
        "pending_approvals": list_pending_approvals(conn, user_id, limit=10),
        "plan_draft": _plan_draft_from_trace(trace),
        "agent_loop": {"enabled": True, "fallback": True, "trace": trace, "todos": _todos_from_trace(trace), "hooks": hook_log or [], "recovery": recovery, "total_timeout_reached": any(x.get("event") == "total_timeout" for x in recovery)},
    }


def respond_with_loop(conn, user_id: int, message: str, mode: str = "auto", history=None, run_id: Optional[str] = None) -> Dict[str, Any]:
    ctx = ai_planner._load_user_context(conn, user_id)
    domains = detect_domains(message if mode == "auto" else f"{mode} {message}")
    valid_domains = set(knowledge_ids())
    if mode in valid_domains and mode not in domains:
        domains.insert(0, mode)
    domains = [d for d in domains if d in valid_domains] or ["coach", "analysis"]

    msgs: List[Dict[str, str]] = [{"role": "system", "content": _build_system(domains, ctx)}]
    for h in (history or [])[-8:]:
        msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    msgs.append({"role": "user", "content": message})

    trace: List[Dict[str, Any]] = []
    hook_log: List[Dict[str, Any]] = []
    recovery: List[Dict[str, Any]] = []
    trigger_hooks("UserPromptSubmit", {
        "conn": conn,
        "user_id": user_id,
        "message": message,
        "mode": mode,
        "domains": domains,
        "history": history or [],
        "hook_log": hook_log,
        "run_id": run_id,
    })

    identity_reply = _forced_identity_reply(message)
    if identity_reply:
        return {
            "ok": True,
            "mode": mode,
            "domains": domains,
            "reply": identity_reply,
            "context": _context_snapshot(ctx),
            "pending_approvals": list_pending_approvals(conn, user_id, limit=10),
            "agent_loop": {"enabled": True, "turns": 0, "forced_identity": True, "trace": [], "todos": [], "hooks": hook_log, "recovery": []},
        }

    forced_call = _forced_body_metric_tool_call(message) or _forced_memory_tool_call(message)
    if forced_call:
        item = _run_tool_with_hooks(
            conn,
            user_id,
            forced_call["name"],
            forced_call["args"],
            1,
            hook_log,
            run_id=run_id,
        )
        trace.append(item)
        result = item.get("result") or {}
        if result.get("error_type") in {"tool_exception", "tool_timeout"}:
            recovery.append(_recovery_event(
                result.get("error_type"),
                tool=item.get("name"),
                message=str(result.get("error") or result.get("message") or "")[:300],
            ))
        final_text = _approval_reply_for_forced_call(forced_call, item)
        pending_approvals = _pending_approvals_from_trace(conn, user_id, trace)
        trigger_hooks("Stop", {
            "conn": conn,
            "user_id": user_id,
            "message": message,
            "mode": mode,
            "domains": domains,
            "trace": trace,
            "todos": [],
            "final_text": final_text,
            "hook_log": hook_log,
            "run_id": run_id,
        })
        return {
            "ok": True,
            "mode": mode,
            "domains": domains,
            "reply": final_text,
            "context": _context_snapshot(ctx),
            "pending_approvals": pending_approvals,
            "agent_loop": {"enabled": True, "turns": 1, "forced_tool": True, "trace": trace, "todos": [], "hooks": hook_log, "recovery": recovery},
        }

    final_text = ""
    used_turns = 0
    max_turns_reached = False
    deadline = time.time() + AGENT_TOTAL_TIMEOUT_SEC

    for turn in range(1, MAX_AGENT_TURNS + 1):
        if time.time() >= deadline:
            recovery.append(_recovery_event("total_timeout", timeout_sec=AGENT_TOTAL_TIMEOUT_SEC))
            break
        used_turns = turn
        remaining_timeout = max(1.0, min(float(getattr(ai_planner, "AGENT_LLM_TIMEOUT", 25)), deadline - time.time()))
        raw = _safe_call_llm(
            msgs,
            recovery,
            conn=conn,
            deadline=deadline,
            timeout=remaining_timeout,
            max_tokens=2200,
            temperature=0.25 if turn == 1 else 0.35,
            chain=os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan"),
        )
        if not raw:
            return _fallback_answer(conn, user_id, message, mode, history, domains, ctx, trace, hook_log, recovery, deadline=deadline)
        obj = _extract_json_object(raw)
        if not obj:
            obj = _repair_json_object(raw, recovery, conn=conn, deadline=deadline)
        if not obj:
            if _looks_like_agent_json(raw):
                recovery.append(_recovery_event("json_parse_failed", sample=raw[:300]))
                return _fallback_answer(conn, user_id, message, mode, history, domains, ctx, trace, hook_log, recovery, deadline=deadline)
            final_text = _sanitize_leaked_agent_json(raw.strip())
            break

        calls = obj.get("tool_calls") or []
        if isinstance(obj.get("tool_calls"), list) and not calls:
            # Model asked for tools but produced an empty array; nudge it once
            # instead of spinning until the turn budget is exhausted.
            recovery.append(_recovery_event("empty_tool_calls", turn=turn))
            msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
            msgs.append({
                "role": "user",
                "content": "tool_calls 数组为空，请直接给出 final 中文回答；如果确实需要工具，请填入至少一个 {name,args}。",
            })
            continue
        if isinstance(calls, list) and calls:
            msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
            results = []
            saw_unknown_tool = False
            for call in calls[:4]:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                item = _run_tool_with_hooks(conn, user_id, name, args, turn, hook_log, run_id=run_id)
                results.append(item)
                trace.append(item)
                result = item.get("result") or {}
                if result.get("error_type") == "unknown_tool":
                    saw_unknown_tool = True
                    recovery.append(_recovery_event(
                        "unknown_tool",
                        tool=name,
                        turn=turn,
                        available=result.get("available_tools") or [],
                    ))
                elif result.get("error_type") in {"tool_exception", "tool_timeout"}:
                    recovery.append(_recovery_event(
                        result.get("error_type"),
                        tool=item.get("name"),
                        message=str(result.get("error") or result.get("message") or "")[:300],
                    ))
            compact_results = _compact_tool_results_for_prompt(results)
            msgs.append({"role": "user", "content": "工具结果(JSON)：\n" + json.dumps(compact_results, ensure_ascii=False)})
            if saw_unknown_tool:
                msgs.append({
                    "role": "user",
                    "content": "上面存在未注册的工具名 (error_type=unknown_tool)。下一轮请只使用系统工具清单中已列出的名称，或直接给出 final 回答。",
                })
            latest_todos = _todos_from_trace(trace)
            todo_block = _format_todo_status_block(latest_todos)
            if todo_block:
                msgs.append({"role": "user", "content": todo_block})
            if time.time() >= deadline:
                recovery.append(_recovery_event("total_timeout", timeout_sec=AGENT_TOTAL_TIMEOUT_SEC))
                break
            if turn == MAX_AGENT_TURNS:
                max_turns_reached = True
                recovery.append(_recovery_event("max_turns_reached", turns=turn))
            continue

        final = obj.get("final") or obj.get("reply") or obj.get("answer")
        if isinstance(final, str) and final.strip():
            final_text = final.strip()
            notes = obj.get("memory_notes") or []
            for n in notes[:2] if isinstance(notes, list) else []:
                if isinstance(n, dict):
                    note = str(n.get("note") or "").strip()
                    category = str(n.get("category") or "observation")
                    if note:
                        args = {"note": note, "category": category}
                        trace.append(_run_tool_with_hooks(conn, user_id, "save_coach_memory", args, used_turns, hook_log, run_id=run_id))
            break

        msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
        msgs.append({"role": "user", "content": "请只输出包含 tool_calls 或 final 字段的 JSON 对象。"})

    if not final_text:
        total_timeout_reached = any(x.get("event") == "total_timeout" for x in recovery)
        if total_timeout_reached:
            final_text = "本次 Agent 分析已达到总耗时上限，我先根据已经拿到的信息给你保守结论。"
            if trace:
                final_text += "\n已完成的查询：" + "、".join(str(t.get("name") or "工具") for t in trace[-3:])
            final_text += "\n建议稍后重试，或把问题缩小到一个具体目标。"
        elif max_turns_reached:
            final_text = "已达到本次分析的工具调用上限，我先根据已经查到的数据给你一个保守结论："
            if trace:
                snippets = []
                for item in trace[-3:]:
                    name = item.get("name") or "工具"
                    result = item.get("result") or {}
                    if result.get("ok") is False:
                        snippets.append(f"{name} 未成功（{result.get('error') or result.get('message') or '未知错误'}）")
                    else:
                        snippets.append(f"{name} 已返回数据")
                final_text += "\n- " + "\n- ".join(snippets)
            else:
                final_text += "目前没有拿到可用工具结果。"
            final_text += "\n建议你缩小问题范围，或稍后重试一次完整分析。"
        else:
            return _fallback_answer(conn, user_id, message, mode, history, domains, ctx, trace, hook_log, recovery, deadline=deadline)

    if "nutrition" in domains:
        must_words = ["蛋白", "碳水", "脂肪", "早餐", "午餐", "晚餐"]
        if not all(w in final_text for w in must_words):
            final_text = _fallback_nutrition(ctx) + "\n\n## 进一步个性化\n" + final_text

    pending_approvals = _pending_approvals_from_trace(conn, user_id, trace)
    todos = _todos_from_trace(trace)
    trigger_hooks("Stop", {
        "conn": conn,
        "user_id": user_id,
        "message": message,
        "mode": mode,
        "domains": domains,
        "trace": trace,
        "todos": todos,
        "final_text": final_text,
        "hook_log": hook_log,
        "run_id": run_id,
    })

    return {
        "ok": True,
        "mode": mode,
        "domains": domains,
        "reply": final_text,
        "context": _context_snapshot(ctx),
        "pending_approvals": pending_approvals,
        "plan_draft": _plan_draft_from_trace(trace),
        "agent_loop": {"enabled": True, "turns": used_turns, "trace": trace, "todos": todos, "hooks": hook_log, "recovery": recovery, "max_turns_reached": max_turns_reached, "total_timeout_reached": any(x.get("event") == "total_timeout" for x in recovery)},
    }
