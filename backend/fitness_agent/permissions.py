"""Permission pipeline for Smart Fitness Agent tools.

Implements the s03-style gate order:
1. hard deny
2. rules that require user approval
3. allow

Approval requests are persisted and executed only after the current user confirms
from the App. This module never trusts the model to approve its own writes.
"""
import json
import sqlite3
import time
import uuid
from typing import Any, Dict, Optional

WRITE_TOOLS = {
    "save_coach_memory",
    "update_body_metrics",
    "create_workout_plan",
    "delete_workout_plan",
}

DENY_TOOLS = {
    "delete_all_user_data",
    "raw_sql",
    "bash",
    "read_file",
    "write_file",
    "http_get",
    "fetch_url",
    "open_url",
}

REASONS = {
    "save_coach_memory": "保存长期教练记忆",
    "update_body_metrics": "修改/新增身体指标",
    "create_workout_plan": "创建训练计划",
    "delete_workout_plan": "删除训练计划",
}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_tool_approvals (
    approval_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_agent_tool_approvals_user_status
    ON agent_tool_approvals(user_id, status, created_at DESC);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_permission_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    _ensure_column(conn, "agent_tool_approvals", "run_id", "run_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tool_approvals_run_id ON agent_tool_approvals(run_id)")
    conn.commit()


def check_permission(tool_name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return allow/ask/deny decision for a tool call."""
    args = args or {}
    if tool_name in DENY_TOOLS:
        return {"behavior": "deny", "reason": f"工具被硬拒绝: {tool_name}"}
    if tool_name in WRITE_TOOLS:
        return {"behavior": "ask", "reason": REASONS.get(tool_name, "修改用户数据")}
    return {"behavior": "allow", "reason": "只读/规划工具，直接允许"}


def create_approval(conn: sqlite3.Connection, user_id: int, tool_name: str, args: Dict[str, Any], reason: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    ensure_permission_schema(conn)
    approval_id = "appr_" + uuid.uuid4().hex[:16]
    created_at = int(time.time())
    conn.execute(
        """
        INSERT INTO agent_tool_approvals (approval_id, user_id, run_id, tool_name, args_json, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (approval_id, user_id, run_id, tool_name, json.dumps(args or {}, ensure_ascii=False), reason, created_at),
    )
    conn.commit()
    return {
        "approval_id": approval_id,
        "run_id": run_id,
        "tool_name": tool_name,
        "args": args or {},
        "reason": reason,
        "status": "pending",
        "created_at": created_at,
        "summary": summarize_tool_call(tool_name, args or {}),
    }


def summarize_tool_call(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "save_coach_memory":
        return f"保存教练记忆：{args.get('note', '')}"
    if tool_name == "update_body_metrics":
        parts = []
        if args.get("weight_kg") is not None:
            parts.append(f"体重 {args.get('weight_kg')}kg")
        if args.get("height_cm") is not None:
            parts.append(f"身高 {args.get('height_cm')}cm")
        if args.get("body_fat_pct") is not None:
            parts.append(f"体脂 {args.get('body_fat_pct')}%")
        return "新增身体指标：" + ("，".join(parts) or "未提供数值")
    if tool_name == "create_workout_plan":
        exercises = args.get("exercises") or []
        return f"创建训练计划：{args.get('name', '未命名计划')}（{len(exercises)} 项）"
    if tool_name == "delete_workout_plan":
        return f"删除训练计划：{args.get('plan_id', '')}"
    return f"执行 {tool_name}"


def list_pending_approvals(conn: sqlite3.Connection, user_id: int, limit: int = 20):
    ensure_permission_schema(conn)
    limit = max(1, min(int(limit or 20), 50))
    rows = conn.execute(
        """
        SELECT approval_id, user_id, run_id, tool_name, args_json, reason, status, result_json, created_at, decided_at
        FROM agent_tool_approvals
        WHERE user_id=? AND status='pending'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_approval(conn: sqlite3.Connection, user_id: int, approval_id: str):
    ensure_permission_schema(conn)
    row = conn.execute(
        """
        SELECT approval_id, user_id, run_id, tool_name, args_json, reason, status, result_json, created_at, decided_at
        FROM agent_tool_approvals
        WHERE user_id=? AND approval_id=?
        """,
        (user_id, approval_id),
    ).fetchone()
    return _row_to_item(row) if row else None


def mark_approval(conn: sqlite3.Connection, user_id: int, approval_id: str, status: str, result: Optional[Dict[str, Any]] = None):
    ensure_permission_schema(conn)
    if status not in {"approved", "denied", "executed", "failed"}:
        raise ValueError("invalid approval status")
    conn.execute(
        """
        UPDATE agent_tool_approvals
        SET status=?, result_json=?, decided_at=?
        WHERE user_id=? AND approval_id=? AND status='pending'
        """,
        (status, json.dumps(result or {}, ensure_ascii=False), int(time.time()), user_id, approval_id),
    )
    conn.commit()


def _row_to_item(row) -> Dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, sqlite3.Row):
        args = json.loads(row["args_json"] or "{}")
        result_raw = row["result_json"]
        tool_name = row["tool_name"]
        return {
            "approval_id": row["approval_id"],
            "user_id": row["user_id"],
            "run_id": row["run_id"],
            "tool_name": tool_name,
            "args": args,
            "reason": row["reason"],
            "status": row["status"],
            "result": _parse_result(result_raw),
            "created_at": row["created_at"],
            "decided_at": row["decided_at"],
            "summary": summarize_tool_call(tool_name, args),
        }
    args = json.loads(row[4] or "{}")
    tool_name = row[3]
    return {
        "approval_id": row[0],
        "user_id": row[1],
        "run_id": row[2],
        "tool_name": tool_name,
        "args": args,
        "reason": row[5],
        "status": row[6],
        "result": _parse_result(row[7]),
        "created_at": row[8],
        "decided_at": row[9],
        "summary": summarize_tool_call(tool_name, args),
    }


def _parse_result(result_raw: Optional[str]):
    if not result_raw:
        return None
    try:
        return json.loads(result_raw)
    except Exception:
        return {"raw": result_raw}
