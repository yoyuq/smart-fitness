"""Runtime orchestration for the Smart Fitness Agent.

The runtime owns run creation/status persistence around the core LLM/tool loop.
This keeps the public API compatible while giving approval-resume and future
background work a durable foundation.
"""
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

import ai_planner

from . import state
from .compact import compact_trace
from .loop import respond_with_loop
from .memory import add_run_summary_memory


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


def start_run(conn: sqlite3.Connection, user_id: int, message: str, mode: str = "auto", history=None) -> Dict[str, Any]:
    run_id = state.create_run(conn, user_id, message, mode=mode)
    try:
        res = respond_with_loop(conn, user_id, message, mode=mode, history=history, run_id=run_id)
        pending = res.get("pending_approvals") or []
        status = "waiting_approval" if pending else "completed"
        trace = compact_trace((res.get("agent_loop") or {}).get("trace") or [])

        todos = (res.get("agent_loop") or {}).get("todos") or []
        state.update_run(
            conn,
            run_id,
            user_id,
            status=status,
            final_text=res.get("reply") or "",
            domains=res.get("domains") or [],
            trace=trace,
            todos=todos,
            pending_approval_ids=[p.get("approval_id") for p in pending if p.get("approval_id")],
        )
        if status == "completed":
            _maybe_add_run_summary_memory(conn, user_id, run_id, message, res.get("reply") or "", trace)
        state.append_event(conn, run_id, user_id, "assistant_final", {"status": status, "reply": res.get("reply")})
        res["run_id"] = run_id
        res["run_status"] = status
        return res
    except Exception as exc:
        state.update_run(conn, run_id, user_id, status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        state.append_event(conn, run_id, user_id, "error", {"type": type(exc).__name__, "message": str(exc)})
        raise


def resume_run_after_approval(
    conn: sqlite3.Connection,
    user_id: int,
    approval: Dict[str, Any],
    tool_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Resume a waiting run after the user approved and the write tool executed.

    This is intentionally conservative: after an approval, the agent gets the
    original user request, previous trace, and the approved tool result, then
    produces a final user-facing summary. It does not grant extra write actions
    during resume; any further write would need another explicit user request.
    """
    run_id = approval.get("run_id")
    if not run_id:
        return {"ok": False, "resumed": False, "message": "approval has no run_id"}
    run = state.get_run(conn, user_id, run_id)
    if not run:
        return {"ok": False, "resumed": False, "message": "run not found", "run_id": run_id}

    trace = compact_trace(list(run.get("trace") or []))
    approval_id = approval.get("approval_id")
    pending_ids = [x for x in (run.get("pending_approval_ids") or []) if x != approval_id]
    executed_item = {
        "name": approval.get("tool_name"),
        "args": approval.get("args") or {},
        "permission": {
            "behavior": "approved",
            "reason": "用户已在 App 中确认执行",
            "approval_id": approval_id,
        },
        "result": tool_result,
        "resume": True,
    }
    trace.append(executed_item)
    state.append_event(conn, run_id, user_id, "approval_executed", executed_item)

    reply = _build_resume_reply(run, approval, tool_result, trace)
    status = "waiting_approval" if pending_ids else "completed"
    if not tool_result.get("ok"):
        status = "failed"
    state.update_run(
        conn,
        run_id,
        user_id,
        status=status,
        final_text=reply,
        trace=trace,
        pending_approval_ids=pending_ids,
        error=None if tool_result.get("ok") else {"type": "tool_failed", "message": str(tool_result.get("error") or tool_result)},
    )
    if status == "completed" and tool_result.get("ok"):
        _maybe_add_run_summary_memory(conn, user_id, run_id, run.get("user_message") or "", reply, trace)
    state.append_event(conn, run_id, user_id, "assistant_resume_final", {"status": status, "reply": reply})
    return {"ok": bool(tool_result.get("ok")), "resumed": True, "run_id": run_id, "run_status": status, "reply": reply, "trace": trace}


def resume_run_after_denial(conn: sqlite3.Connection, user_id: int, approval: Dict[str, Any]) -> Dict[str, Any]:
    run_id = approval.get("run_id")
    if not run_id:
        return {"ok": True, "resumed": False, "message": "approval denied"}
    run = state.get_run(conn, user_id, run_id)
    if not run:
        return {"ok": True, "resumed": False, "message": "run not found", "run_id": run_id}
    approval_id = approval.get("approval_id")
    pending_ids = [x for x in (run.get("pending_approval_ids") or []) if x != approval_id]
    trace = compact_trace(list(run.get("trace") or []))
    denied_item = {
        "name": approval.get("tool_name"),
        "args": approval.get("args") or {},
        "permission": {"behavior": "denied", "reason": "用户拒绝执行", "approval_id": approval_id},
        "result": {"ok": False, "message": "用户已拒绝执行该修改"},
        "resume": True,
    }
    trace.append(denied_item)
    reply = f"已取消：{approval.get('summary') or approval.get('tool_name')}。我不会修改你的数据。"
    status = "waiting_approval" if pending_ids else "completed"
    state.update_run(conn, run_id, user_id, status=status, final_text=reply, trace=trace, pending_approval_ids=pending_ids)
    state.append_event(conn, run_id, user_id, "approval_denied", denied_item)
    state.append_event(conn, run_id, user_id, "assistant_resume_final", {"status": status, "reply": reply})
    return {"ok": True, "resumed": True, "run_id": run_id, "run_status": status, "reply": reply, "trace": trace}


def _build_resume_reply(run: Dict[str, Any], approval: Dict[str, Any], tool_result: Dict[str, Any], trace: List[Dict[str, Any]]) -> str:
    fallback = _fallback_resume_reply(approval, tool_result)
    messages = [
        {
            "role": "system",
            "content": """你是 Smart Fitness 健身 Agent。用户刚刚在 App 中批准了一个写工具，后端已经执行完毕。
请基于原始请求和工具结果，给用户一个简短中文回复：说明结果、下一步建议、不要声称执行额外未批准的修改。
只输出 JSON：{"final":"..."}""",
        },
        {
            "role": "user",
            "content": "原始请求：\n"
            + str(run.get("user_message") or "")
            + "\n\n批准的工具：\n"
            + json.dumps({"approval": approval, "tool_result": tool_result}, ensure_ascii=False)
            + "\n\n最近 trace：\n"
            + json.dumps(compact_trace(trace)[-6:], ensure_ascii=False),
        },
    ]
    try:
        raw = ai_planner._call_llm(messages, max_tokens=800, temperature=0.25, chain="deepseek,qwen,volc-coding,hunyuan")
    except Exception:
        raw = ""
    obj = _extract_json_object(raw or "")
    final = obj.get("final") or obj.get("reply") or obj.get("answer") if obj else None
    if isinstance(final, str) and final.strip():
        return final.strip()
    if raw and not obj:
        return raw.strip()[:1200]
    return fallback


def _fallback_resume_reply(approval: Dict[str, Any], tool_result: Dict[str, Any]) -> str:
    summary = approval.get("summary") or approval.get("tool_name") or "该操作"
    if tool_result.get("ok"):
        return f"已完成：{summary}。你可以在对应页面查看最新数据；如果要继续，我可以基于这次更新重新分析训练或调整计划。"
    return f"执行失败：{summary}。原因：{tool_result.get('error') or tool_result.get('message') or '未知错误'}"


def _maybe_add_run_summary_memory(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: str,
    user_message: str,
    reply: str,
    trace: List[Dict[str, Any]],
) -> None:
    """Persist a short run_summary for non-trivial completed runs.

    This is a low-risk system memory: it records what the Agent did, not a new
    user preference/fact. User facts still go through save_coach_memory approval.
    """
    tool_names = [str(t.get("name") or "") for t in (trace or []) if t.get("name")]
    if not tool_names and len(user_message or "") < 30:
        return
    summary = (
        f"用户请求：{(user_message or '').strip()[:120]}；"
        f"Agent 使用工具：{', '.join(tool_names[:6]) or '无'}；"
        f"最终回复：{(reply or '').strip()[:180]}"
    )
    try:
        add_run_summary_memory(conn, user_id, run_id, summary, confidence=0.75)
        state.append_event(conn, run_id, user_id, "memory_run_summary_saved", {"summary": summary})
    except Exception as exc:
        state.append_event(conn, run_id, user_id, "memory_run_summary_failed", {"error": str(exc)})


def get_run(conn: sqlite3.Connection, user_id: int, run_id: str) -> Optional[Dict[str, Any]]:
    return state.get_run(conn, user_id, run_id)


def list_runs(conn: sqlite3.Connection, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    return state.list_runs(conn, user_id, limit=limit)
