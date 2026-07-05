"""Runtime state persistence for Smart Fitness Agent runs.

A run is one user request plus the agent's tool trace/todos/status. This is the
foundation for approval-resume and long-running agent work.
"""
import json
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

def _sanitize_agent_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = str(text)
    if "GPT-4" in out or "OpenAI" in out:
        out = re.sub(r"基于\s*GPT-4\s*架构", "由后端配置的 LLM 调用链驱动", out, flags=re.I)
        out = re.sub(r"不是\s*GPT-4[、,，\s]*OpenAI\s*或任何特定模型架构[。.]?", "", out, flags=re.I)
        out = out.replace("GPT-4", "后端配置的 LLM 调用链")
        out = out.replace("OpenAI", "后端配置的模型服务")
    return out.strip()


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    mode TEXT DEFAULT 'auto',
    user_message TEXT NOT NULL,
    final_text TEXT,
    domains_json TEXT DEFAULT '[]',
    trace_json TEXT DEFAULT '[]',
    todos_json TEXT DEFAULT '[]',
    pending_approval_ids_json TEXT DEFAULT '[]',
    error_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created
    ON agent_runs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run_id
    ON agent_run_events(run_id, id);

CREATE TABLE IF NOT EXISTS agent_provider_health (
    provider TEXT PRIMARY KEY,
    consecutive_failures INTEGER DEFAULT 0,
    cooldown_until INTEGER DEFAULT 0,
    last_success_at INTEGER,
    last_failure_at INTEGER,
    last_error_type TEXT,
    last_error_message TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    updated_at INTEGER NOT NULL
);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, default=str)


def _loads(raw: Any, default: Any):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def ensure_agent_state_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    conn.commit()


def create_run(conn: sqlite3.Connection, user_id: int, message: str, mode: str = "auto") -> str:
    ensure_agent_state_schema(conn)
    run_id = "run_" + uuid.uuid4().hex[:16]
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO agent_runs (run_id, user_id, status, mode, user_message, created_at, updated_at)
        VALUES (?, ?, 'running', ?, ?, ?, ?)
        """,
        (run_id, user_id, mode or "auto", message, now, now),
    )
    append_event(conn, run_id, user_id, "user_message", {"message": message, "mode": mode})
    conn.commit()
    return run_id


def append_event(conn: sqlite3.Connection, run_id: str, user_id: int, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    ensure_agent_state_schema(conn)
    conn.execute(
        "INSERT INTO agent_run_events (run_id, user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, user_id, event_type, _dumps(payload or {}), int(time.time())),
    )


def update_run(
    conn: sqlite3.Connection,
    run_id: str,
    user_id: int,
    *,
    status: Optional[str] = None,
    final_text: Optional[str] = None,
    domains: Optional[List[str]] = None,
    trace: Optional[List[Dict[str, Any]]] = None,
    todos: Optional[List[Dict[str, Any]]] = None,
    pending_approval_ids: Optional[List[str]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    ensure_agent_state_schema(conn)
    fields = ["updated_at=?"]
    vals: List[Any] = [int(time.time())]
    if status is not None:
        fields.append("status=?")
        vals.append(status)
        if status in {"completed", "failed", "cancelled"}:
            fields.append("completed_at=?")
            vals.append(int(time.time()))
    if final_text is not None:
        fields.append("final_text=?")
        vals.append(_sanitize_agent_text(final_text))
    if domains is not None:
        fields.append("domains_json=?")
        vals.append(_dumps(domains))
    if trace is not None:
        fields.append("trace_json=?")
        vals.append(_dumps(trace))
    if todos is not None:
        fields.append("todos_json=?")
        vals.append(_dumps(todos))
    if pending_approval_ids is not None:
        fields.append("pending_approval_ids_json=?")
        vals.append(_dumps(pending_approval_ids))
    if error is not None:
        fields.append("error_json=?")
        vals.append(_dumps(error))
    vals.extend([user_id, run_id])
    conn.execute(f"UPDATE agent_runs SET {', '.join(fields)} WHERE user_id=? AND run_id=?", vals)
    conn.commit()


def get_run(conn: sqlite3.Connection, user_id: int, run_id: str) -> Optional[Dict[str, Any]]:
    ensure_agent_state_schema(conn)
    row = conn.execute("SELECT * FROM agent_runs WHERE user_id=? AND run_id=?", (user_id, run_id)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(conn: sqlite3.Connection, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_agent_state_schema(conn)
    limit = max(1, min(int(limit or 20), 100))
    rows = conn.execute("SELECT * FROM agent_runs WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
    return [_row_to_run(r) for r in rows]


def record_provider_success(conn: sqlite3.Connection, provider: str) -> None:
    ensure_agent_state_schema(conn)
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO agent_provider_health (provider, consecutive_failures, cooldown_until, last_success_at, success_count, updated_at)
        VALUES (?, 0, 0, ?, 1, ?)
        ON CONFLICT(provider) DO UPDATE SET
            consecutive_failures=0,
            cooldown_until=0,
            last_success_at=excluded.last_success_at,
            success_count=success_count+1,
            updated_at=excluded.updated_at
        """,
        (provider, now, now),
    )
    conn.commit()


def record_provider_failure(
    conn: sqlite3.Connection,
    provider: str,
    *,
    threshold: int,
    cooldown_sec: int,
    error_type: str = "error",
    message: str = "",
) -> Dict[str, Any]:
    ensure_agent_state_schema(conn)
    now = int(time.time())
    row = conn.execute("SELECT consecutive_failures FROM agent_provider_health WHERE provider=?", (provider,)).fetchone()
    consecutive = int(row["consecutive_failures"] if row else 0) + 1
    cooldown_until = now + int(cooldown_sec) if consecutive >= int(threshold) else 0
    conn.execute(
        """
        INSERT INTO agent_provider_health (
            provider, consecutive_failures, cooldown_until, last_failure_at,
            last_error_type, last_error_message, failure_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(provider) DO UPDATE SET
            consecutive_failures=excluded.consecutive_failures,
            cooldown_until=excluded.cooldown_until,
            last_failure_at=excluded.last_failure_at,
            last_error_type=excluded.last_error_type,
            last_error_message=excluded.last_error_message,
            failure_count=failure_count+1,
            updated_at=excluded.updated_at
        """,
        (provider, consecutive, cooldown_until, now, error_type[:80], message[:500], now),
    )
    conn.commit()
    return {"consecutive_failures": consecutive, "cooldown_until": cooldown_until, "circuit_opened": cooldown_until > 0}


def get_provider_health(conn: sqlite3.Connection, provider: str) -> Optional[Dict[str, Any]]:
    ensure_agent_state_schema(conn)
    row = conn.execute("SELECT * FROM agent_provider_health WHERE provider=?", (provider,)).fetchone()
    return dict(row) if row else None


def list_provider_health(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    ensure_agent_state_schema(conn)
    rows = conn.execute("SELECT * FROM agent_provider_health ORDER BY provider").fetchall()
    now = int(time.time())
    out = []
    for row in rows:
        item = dict(row)
        item["cooling_down"] = int(item.get("cooldown_until") or 0) > now
        out.append(item)
    return out


def recent_agent_stats(conn: sqlite3.Connection, user_id: Optional[int] = None, window_sec: int = 3600) -> Dict[str, Any]:
    ensure_agent_state_schema(conn)
    since = int(time.time()) - int(window_sec)
    where = "created_at>=?"
    params: List[Any] = [since]
    if user_id is not None:
        where += " AND user_id=?"
        params.append(user_id)
    status_rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM agent_runs WHERE {where} GROUP BY status",
        params,
    ).fetchall()
    total = sum(int(r["count"]) for r in status_rows)
    event_params = list(params)
    event_where = "created_at>=?"
    if user_id is not None:
        event_where += " AND user_id=?"
    event_rows = conn.execute(
        f"SELECT event_type, COUNT(*) AS count FROM agent_run_events WHERE {event_where} GROUP BY event_type",
        event_params,
    ).fetchall()
    return {
        "window_sec": int(window_sec),
        "total_runs": total,
        "by_status": {r["status"]: int(r["count"]) for r in status_rows},
        "events": {r["event_type"]: int(r["count"]) for r in event_rows},
    }


def _row_to_run(row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "mode": row["mode"],
        "user_message": row["user_message"],
        "final_text": _sanitize_agent_text(row["final_text"]),
        "domains": _loads(row["domains_json"], []),
        "trace": _loads(row["trace_json"], []),
        "todos": _loads(row["todos_json"], []),
        "pending_approval_ids": _loads(row["pending_approval_ids_json"], []),
        "error": _loads(row["error_json"], None) if row["error_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }
