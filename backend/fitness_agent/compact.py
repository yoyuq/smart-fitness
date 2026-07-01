"""Context compaction for Smart Fitness Agent.

First version:
- summarize older chat history into a compact user-level summary
- keep only recent turns in the LLM prompt
- compact large tool results before storing run trace
"""
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

import ai_planner

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_context_summaries (
    user_id INTEGER NOT NULL,
    summary_type TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    source_until_id INTEGER DEFAULT 0,
    source_until_ts INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, summary_type)
);
"""

MAX_HISTORY_TURNS = int(os.environ.get("AI_AGENT_CONTEXT_RECENT_TURNS", "10"))
SUMMARIZE_THRESHOLD = int(os.environ.get("AI_AGENT_CONTEXT_SUMMARIZE_THRESHOLD", "16"))
MAX_TOOL_STRING = int(os.environ.get("AI_AGENT_COMPACT_TOOL_STRING", "900"))
MAX_TOOL_LIST_ITEMS = int(os.environ.get("AI_AGENT_COMPACT_LIST_ITEMS", "8"))
MAX_TOOL_DICT_KEYS = int(os.environ.get("AI_AGENT_COMPACT_DICT_KEYS", "18"))
MAX_SUMMARY_CHARS = int(os.environ.get("AI_AGENT_CONTEXT_SUMMARY_CHARS", "1800"))


def ensure_context_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    conn.commit()


def get_context_summary(conn: sqlite3.Connection, user_id: int, summary_type: str = "chat") -> Optional[Dict[str, Any]]:
    ensure_context_schema(conn)
    row = conn.execute(
        "SELECT user_id, summary_type, summary_text, source_until_id, source_until_ts, created_at, updated_at FROM agent_context_summaries WHERE user_id=? AND summary_type=?",
        (user_id, summary_type),
    ).fetchone()
    if not row:
        return None
    return {k: row[k] for k in row.keys()} if isinstance(row, sqlite3.Row) else {
        "user_id": row[0], "summary_type": row[1], "summary_text": row[2], "source_until_id": row[3],
        "source_until_ts": row[4], "created_at": row[5], "updated_at": row[6]
    }


def upsert_context_summary(conn: sqlite3.Connection, user_id: int, summary_text: str, source_until_id: int, source_until_ts: int = 0, summary_type: str = "chat") -> None:
    ensure_context_schema(conn)
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO agent_context_summaries (user_id, summary_type, summary_text, source_until_id, source_until_ts, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, summary_type) DO UPDATE SET
            summary_text=excluded.summary_text,
            source_until_id=excluded.source_until_id,
            source_until_ts=excluded.source_until_ts,
            updated_at=excluded.updated_at
        """,
        (user_id, summary_type, summary_text[:MAX_SUMMARY_CHARS], int(source_until_id or 0), int(source_until_ts or 0), now, now),
    )
    conn.commit()


def _extract_text(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            for key in ("summary", "final", "text"):
                if isinstance(obj.get(key), str):
                    return obj[key].strip()
        except Exception:
            pass
    return text[:MAX_SUMMARY_CHARS]


def _fallback_summary(existing: str, messages: List[Dict[str, Any]]) -> str:
    lines = []
    if existing:
        lines.append(existing.strip())
    for m in messages[-12:]:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip().replace("\n", " ")[:160]
        if content:
            lines.append(f"{role}: {content}")
    out = "\n".join(lines)
    return out[-MAX_SUMMARY_CHARS:]


def summarize_chat_history(conn: sqlite3.Connection, user_id: int, messages: List[Dict[str, Any]], force: bool = False) -> Optional[Dict[str, Any]]:
    """Summarize older chat messages and persist a compact chat summary.

    `messages` should be chronological. Only messages before the recent window
    are summarized. Returns the stored summary object or None.
    """
    ensure_context_schema(conn)
    if len(messages) <= SUMMARIZE_THRESHOLD and not force:
        return get_context_summary(conn, user_id, "chat")
    summary = get_context_summary(conn, user_id, "chat")
    source_until_id = int((summary or {}).get("source_until_id") or 0)
    older = [m for m in messages[:-MAX_HISTORY_TURNS] if int(m.get("id") or 0) > source_until_id]
    if not older and not force:
        return summary
    existing = (summary or {}).get("summary_text") or ""
    last_id = max([int(m.get("id") or 0) for m in older] or [source_until_id])
    last_ts = max([int(m.get("created_at") or 0) for m in older] or [0])
    prompt_messages = [
        {"role": "system", "content": """你是 Smart Fitness Agent 的上下文压缩器。
把旧聊天压缩成不超过 1200 字的中文摘要，只保留：用户目标、身体/饮食/训练偏好、伤病限制、已做决定、未完成事项、最近重要结论。
不要加入新的建议，不要编造。只输出 JSON：{"summary":"..."}"""},
        {"role": "user", "content": "已有摘要：\n" + (existing or "无") + "\n\n新增旧消息：\n" + json.dumps([
            {"role": m.get("role"), "content": m.get("content"), "mode": m.get("mode"), "domains": m.get("domains")}
            for m in older
        ], ensure_ascii=False)},
    ]
    try:
        raw = ai_planner._call_llm(
            prompt_messages,
            max_tokens=1000,
            temperature=0.15,
            chain=os.environ.get("AI_AGENT_COMPACT_CHAIN", os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan")),
        )
        text = _extract_text(raw or "")
    except Exception:
        text = ""
    if not text:
        text = _fallback_summary(existing, older)
    upsert_context_summary(conn, user_id, text, source_until_id=last_id, source_until_ts=last_ts, summary_type="chat")
    return get_context_summary(conn, user_id, "chat")


def prepare_llm_history_with_summary(conn: sqlite3.Connection, user_id: int, messages: List[Dict[str, Any]], recent_limit: int = MAX_HISTORY_TURNS) -> List[Dict[str, str]]:
    summary = summarize_chat_history(conn, user_id, messages)
    out: List[Dict[str, str]] = []
    if summary and summary.get("summary_text"):
        out.append({"role": "user", "content": "【旧对话压缩摘要】\n" + summary["summary_text"]})
    for m in messages[-max(1, recent_limit):]:
        role = m.get("role", "user")
        content = m.get("content") or ""
        if role in {"user", "assistant"} and content:
            out.append({"role": role, "content": content})
    return out


def compact_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "...[depth truncated]"
    if isinstance(value, str):
        return value if len(value) <= MAX_TOOL_STRING else value[:MAX_TOOL_STRING] + f"...[truncated {len(value) - MAX_TOOL_STRING} chars]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = [compact_value(v, depth + 1) for v in value[:MAX_TOOL_LIST_ITEMS]]
        if len(value) > MAX_TOOL_LIST_ITEMS:
            items.append({"_truncated_items": len(value) - MAX_TOOL_LIST_ITEMS})
        return items
    if isinstance(value, dict):
        preferred = ["ok", "error", "message", "summary", "days", "exercise", "query", "search_query", "source", "note", "decision", "approval", "saved", "plan_id", "name", "status"]
        out: Dict[str, Any] = {}
        keys = []
        for k in preferred:
            if k in value:
                keys.append(k)
        for k in value.keys():
            if k not in keys:
                keys.append(k)
        for k in keys[:MAX_TOOL_DICT_KEYS]:
            out[str(k)] = compact_value(value[k], depth + 1)
        if len(keys) > MAX_TOOL_DICT_KEYS:
            out["_truncated_keys"] = len(keys) - MAX_TOOL_DICT_KEYS
        return out
    return str(value)[:MAX_TOOL_STRING]


def compact_trace(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compacted = []
    for item in trace or []:
        if not isinstance(item, dict):
            continue
        compacted.append({
            "name": item.get("name"),
            "args": compact_value(item.get("args") or {}),
            "permission": compact_value(item.get("permission") or {}),
            "result": compact_value(item.get("result") or {}),
            **({"resume": True} if item.get("resume") else {}),
        })
    return compacted
