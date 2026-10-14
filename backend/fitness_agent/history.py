"""Persistent chat history for the Smart Fitness Agent."""
import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional


def _sanitize_agent_content(content: str) -> str:
    """Remove stale/provider-specific wording from persisted assistant history.

    Older builds allowed the model to claim a concrete external model/architecture.
    History is shown directly in the App, so sanitize on write/read to avoid
    keeping misleading product copy alive after the prompt/runtime fix.
    """
    text = content or ""
    if "GPT-4" in text or "OpenAI" in text:
        text = re.sub(r"基于\s*GPT-4\s*架构", "由后端配置的 LLM 调用链驱动", text, flags=re.I)
        text = re.sub(r"不是\s*GPT-4[、,，\s]*OpenAI\s*或任何特定模型架构[。.]?", "", text, flags=re.I)
        text = text.replace("GPT-4", "后端配置的 LLM 调用链")
        text = text.replace("OpenAI", "后端配置的模型服务")
    return text.strip()


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    mode TEXT DEFAULT 'auto',
    domains_json TEXT DEFAULT '[]',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_chat_user_id_id
    ON agent_chat_messages(user_id, id);
"""


def ensure_agent_chat_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    conn.commit()


def add_agent_chat_message(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    content: str,
    mode: str = "auto",
    domains: Optional[List[str]] = None,
    created_at: Optional[int] = None,
) -> int:
    ensure_agent_chat_schema(conn)
    role = role if role in {"user", "assistant"} else "user"
    content = (content or "").strip()
    content = _sanitize_agent_content(content) if role == "assistant" else content
    if not content:
        return 0
    domains_json = json.dumps(domains or [], ensure_ascii=False)
    ts = int(created_at or time.time())
    cur = conn.execute(
        """
        INSERT INTO agent_chat_messages (user_id, role, content, mode, domains_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, role, content, mode or "auto", domains_json, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_agent_chat_history(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_agent_chat_schema(conn)
    limit = max(1, min(int(limit or 50), 200))
    rows = conn.execute(
        """
        SELECT id, role, content, mode, domains_json, created_at
        FROM agent_chat_messages
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in reversed(rows):
        try:
            domains = json.loads(r["domains_json"] or "[]")
        except Exception:
            domains = []
        out.append({
            "id": r["id"],
            "role": r["role"],
            "content": _sanitize_agent_content(r["content"]) if r["role"] == "assistant" else r["content"],
            "mode": r["mode"] or "auto",
            "domains": domains if isinstance(domains, list) else [],
            "created_at": r["created_at"],
        })
    return out


def delete_agent_chat_history(conn: sqlite3.Connection, user_id: int) -> int:
    ensure_agent_chat_schema(conn)
    cur = conn.execute("DELETE FROM agent_chat_messages WHERE user_id=?", (user_id,))
    conn.commit()
    return int(cur.rowcount or 0)


def to_llm_history(messages: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, str]]:
    return [
        {"role": m.get("role", "user"), "content": _sanitize_agent_content(m.get("content", "")) if m.get("role") == "assistant" else m.get("content", "")}
        for m in messages[-limit:]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
