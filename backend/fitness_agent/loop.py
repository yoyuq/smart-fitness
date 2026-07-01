"""Lightweight tool-calling loop for the Smart Fitness Agent.

The loop stays deliberately small: LLM -> tool_calls -> tool_results -> LLM.
Runtime/state, knowledge loading, permission checks and hooks live around it.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

import ai_planner
from .core import _fallback_nutrition, detect_domains
from .hooks import trigger_hooks
from .knowledge_loader import knowledge_ids, load_knowledge
from .permissions import list_pending_approvals
from .prompts import build_system_prompt
from .tools import execute_tool

MAX_AGENT_TURNS = int(os.environ.get("AI_AGENT_LOOP_MAX_TURNS", "4"))


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


def _approval_reply_for_forced_call(call: Dict[str, Any], item: Dict[str, Any]) -> str:
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
    result = execute_tool(conn, user_id, name, args)
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
    text = ai_planner._call_llm(
        msgs,
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
        "agent_loop": {"enabled": True, "fallback": True, "trace": trace, "todos": _todos_from_trace(trace), "hooks": hook_log or []},
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

    forced_call = _forced_body_metric_tool_call(message)
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
            "agent_loop": {"enabled": True, "turns": 1, "forced_tool": True, "trace": trace, "todos": [], "hooks": hook_log},
        }

    final_text = ""
    used_turns = 0

    for turn in range(1, MAX_AGENT_TURNS + 1):
        used_turns = turn
        raw = ai_planner._call_llm(
            msgs,
            max_tokens=2200,
            temperature=0.25 if turn == 1 else 0.35,
            chain=os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan"),
        )
        if not raw:
            return _fallback_answer(conn, user_id, message, mode, history, domains, ctx, trace, hook_log)
        obj = _extract_json_object(raw)
        if not obj:
            final_text = raw.strip()
            break

        calls = obj.get("tool_calls") or []
        if isinstance(calls, list) and calls:
            msgs.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
            results = []
            for call in calls[:4]:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                item = _run_tool_with_hooks(conn, user_id, name, args, turn, hook_log, run_id=run_id)
                results.append(item)
                trace.append(item)
            msgs.append({"role": "user", "content": "工具结果(JSON)：\n" + json.dumps(results, ensure_ascii=False)})
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
        return _fallback_answer(conn, user_id, message, mode, history, domains, ctx, trace, hook_log)

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
        "agent_loop": {"enabled": True, "turns": used_turns, "trace": trace, "todos": todos, "hooks": hook_log},
    }
