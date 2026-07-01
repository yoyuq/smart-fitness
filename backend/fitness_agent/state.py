"""Runtime state persistence for Smart Fitness Agent runs.

A run is one user request plus the agent's tool trace/todos/status. This is the
foundation for approval-resume and long-running agent work.
"""
import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

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
        vals.append(final_text)
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


def _row_to_run(row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "mode": row["mode"],
        "user_message": row["user_message"],
        "final_text": row["final_text"],
        "domains": _loads(row["domains_json"], []),
        "trace": _loads(row["trace_json"], []),
        "todos": _loads(row["todos_json"], []),
        "pending_approval_ids": _loads(row["pending_approval_ids_json"], []),
        "error": _loads(row["error_json"], None) if row["error_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }
