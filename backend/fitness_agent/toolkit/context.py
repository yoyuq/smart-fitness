"""Context and body-metric read/update tools."""
import time
from typing import Any, Dict

import ai_planner

from .base import clamp_int, rows_to_dicts
from .registry import register_tool


def _context_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": ctx.get("user_id"),
        "username": ctx.get("username"),
        "body": ctx.get("body"),
        "streak_days": ctx.get("streak_days"),
        "today_exercises": ctx.get("today_exercises", [])[:12],
        "weekly_summary": ctx.get("weekly_summary", [])[:14],
        "per_exercise": ctx.get("per_exercise", [])[:20],
        "plans": ctx.get("plans", [])[:5],
        "coach_memory": ctx.get("coach_memory", [])[:20],
    }


@register_tool(
    name="get_user_context_snapshot",
    description="读取当前用户的身体数据、今日训练、近14天记录、近28天分动作统计、训练计划和教练记忆。",
)
def get_user_context_snapshot(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    ctx = ai_planner._load_user_context(conn, user_id)
    return {"ok": True, "context": _context_snapshot(ctx)}


@register_tool(
    name="get_body_metrics",
    description="读取最近身体指标记录。参数: limit 1-20。",
    args={"limit": "int optional, default 5"},
)
def get_body_metrics(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = clamp_int(args.get("limit"), 5, 1, 20)
    rows = conn.execute(
        """
        SELECT weight_kg, height_cm, body_fat_pct, timestamp
        FROM user_body_metrics
        WHERE user_id=?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return {"ok": True, "metrics": rows_to_dicts(rows)}


@register_tool(
    name="update_body_metrics",
    description="新增一条身体指标记录。修改类工具，执行前必须获得用户确认。参数: weight_kg 可选, height_cm 可选, body_fat_pct 可选, notes 可选。",
    args={"weight_kg": "number optional", "height_cm": "number optional", "body_fat_pct": "number optional", "notes": "string optional"},
    read_only=False,
    requires_approval=True,
)
def update_body_metrics(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    vals = {
        "weight_kg": args.get("weight_kg"),
        "height_cm": args.get("height_cm"),
        "body_fat_pct": args.get("body_fat_pct"),
        "notes": (args.get("notes") or "Agent 更新").strip()[:200],
    }
    if vals["weight_kg"] is None and vals["height_cm"] is None and vals["body_fat_pct"] is None:
        return {"ok": False, "error": "no metric value provided"}
    conn.execute(
        "INSERT INTO user_body_metrics (user_id, weight_kg, height_cm, body_fat_pct, notes, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, vals["weight_kg"], vals["height_cm"], vals["body_fat_pct"], vals["notes"], int(time.time())),
    )
    conn.commit()
    return {"ok": True, "saved": vals}
