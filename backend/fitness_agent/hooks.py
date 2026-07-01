"""Lightweight hooks for the Smart Fitness Agent loop.

Hooks keep the loop stable while permission, logging and output checks live as
small callbacks. This intentionally stays app-local and does not load external
scripts.
"""
import json
import time
from typing import Any, Callable, Dict, List, Optional

from .permissions import check_permission, create_approval

HookCallback = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]

EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
HOOKS: Dict[str, List[HookCallback]] = {event: [] for event in EVENTS}
MAX_TOOL_OUTPUT_CHARS = 12000


def register_hook(event: str, callback: HookCallback) -> None:
    if event not in HOOKS:
        raise ValueError(f"unknown hook event: {event}")
    HOOKS[event].append(callback)


def clear_hooks(event: Optional[str] = None) -> None:
    if event is None:
        for callbacks in HOOKS.values():
            callbacks.clear()
        return
    if event not in HOOKS:
        raise ValueError(f"unknown hook event: {event}")
    HOOKS[event].clear()


def trigger_hooks(event: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if event not in HOOKS:
        raise ValueError(f"unknown hook event: {event}")
    for callback in HOOKS[event]:
        result = callback(payload)
        if result:
            return result
    return None


def prompt_audit_hook(payload: Dict[str, Any]) -> None:
    payload.setdefault("hook_log", []).append({
        "event": "UserPromptSubmit",
        "user_id": payload.get("user_id"),
        "mode": payload.get("mode"),
        "message_len": len(payload.get("message") or ""),
        "ts": int(time.time()),
    })
    return None


def permission_hook(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Final security gate for tool calls.

    Read tools pass through. Write tools return an approval result and block
    immediate execution. Hard-denied tools return a denied result.
    """
    name = payload.get("name") or ""
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    decision = check_permission(name, args)
    payload["permission"] = decision
    behavior = decision.get("behavior")
    if behavior == "deny":
        return {
            "stop": True,
            "permission": decision,
            "result": {"ok": False, "permission": "denied", "error": decision.get("reason")},
        }
    if behavior == "ask":
        approval = create_approval(
            payload["conn"],
            int(payload["user_id"]),
            name,
            args,
            decision.get("reason") or "需要用户确认",
            run_id=payload.get("run_id"),
        )
        return {
            "stop": True,
            "permission": decision,
            "result": {"ok": False, "permission": "pending", "approval": approval},
        }
    return None


def tool_log_hook(payload: Dict[str, Any]) -> None:
    payload.setdefault("hook_log", []).append({
        "event": "PreToolUse",
        "name": payload.get("name"),
        "read_only": (payload.get("permission") or {}).get("behavior") == "allow",
        "ts": int(time.time()),
    })
    return None


def large_output_hook(payload: Dict[str, Any]) -> None:
    result = payload.get("result")
    raw = json.dumps(result, ensure_ascii=False, default=str)
    if len(raw) <= MAX_TOOL_OUTPUT_CHARS:
        return None
    payload["result"] = {
        "ok": True,
        "truncated": True,
        "original_chars": len(raw),
        "preview": raw[:MAX_TOOL_OUTPUT_CHARS],
    }
    payload.setdefault("hook_log", []).append({
        "event": "PostToolUse",
        "name": payload.get("name"),
        "warning": "large_output_truncated",
        "original_chars": len(raw),
        "ts": int(time.time()),
    })
    return None


def stop_summary_hook(payload: Dict[str, Any]) -> None:
    trace = payload.get("trace") or []
    pending = [
        item.get("result", {}).get("approval")
        for item in trace
        if item.get("result", {}).get("permission") == "pending" and item.get("result", {}).get("approval")
    ]
    payload.setdefault("hook_log", []).append({
        "event": "Stop",
        "tool_calls": len(trace),
        "pending_approvals": len(pending),
        "ts": int(time.time()),
    })
    return None


def register_default_hooks() -> None:
    """Install built-in hooks once, preserving test-added hooks if any."""
    if not HOOKS["UserPromptSubmit"]:
        register_hook("UserPromptSubmit", prompt_audit_hook)
    if not HOOKS["PreToolUse"]:
        register_hook("PreToolUse", permission_hook)
        register_hook("PreToolUse", tool_log_hook)
    if not HOOKS["PostToolUse"]:
        register_hook("PostToolUse", large_output_hook)
    if not HOOKS["Stop"]:
        register_hook("Stop", stop_summary_hook)


register_default_hooks()
