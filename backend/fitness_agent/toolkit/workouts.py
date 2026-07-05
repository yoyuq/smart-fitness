"""Workout read tools."""
import time
from typing import Any, Dict

from .base import clamp_int, rows_to_dicts
from .registry import register_tool


@register_tool(
    name="get_recent_workouts",
    description="读取最近训练明细（含 session_id，可直接联 get_session_rep_scores/get_rep_analysis 看单rep详情）。参数: days 1-90, exercise 可选动作名, limit 1-100。",
    args={"days": "int optional", "exercise": "string optional", "limit": "int optional"},
)
def get_recent_workouts(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    days = clamp_int(args.get("days"), 14, 1, 90)
    limit = clamp_int(args.get("limit"), 50, 1, 100)
    exercise = (args.get("exercise") or "").strip()
    since = int(time.time()) - days * 86400
    # exercise_log has no session_id column; correlate to sessions via user_id + time window.
    # A workout row belongs to the session whose start_time <= created_at <= end_time+30s.
    if exercise:
        rows = conn.execute(
            """
            SELECT el.exercise_type, el.reps, el.duration_s, el.avg_form_score, el.created_at,
                   (
                     SELECT s.session_id FROM sessions s
                     WHERE s.user_id = CAST(el.user_id AS TEXT)
                       AND s.exercise_type = el.exercise_type
                       AND s.start_time <= el.created_at
                       AND (s.end_time IS NULL OR s.end_time + 30 >= el.created_at)
                     ORDER BY s.start_time DESC LIMIT 1
                   ) AS session_id
            FROM exercise_log el
            WHERE el.user_id=? AND el.created_at>=? AND el.exercise_type=?
            ORDER BY el.created_at DESC
            LIMIT ?
            """,
            (user_id, since, exercise, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT el.exercise_type, el.reps, el.duration_s, el.avg_form_score, el.created_at,
                   (
                     SELECT s.session_id FROM sessions s
                     WHERE s.user_id = CAST(el.user_id AS TEXT)
                       AND s.exercise_type = el.exercise_type
                       AND s.start_time <= el.created_at
                       AND (s.end_time IS NULL OR s.end_time + 30 >= el.created_at)
                     ORDER BY s.start_time DESC LIMIT 1
                   ) AS session_id
            FROM exercise_log el
            WHERE el.user_id=? AND el.created_at>=?
            ORDER BY el.created_at DESC
            LIMIT ?
            """,
            (user_id, since, limit),
        ).fetchall()
    return {"ok": True, "days": days, "exercise": exercise or None, "workouts": rows_to_dicts(rows)}


@register_tool(
    name="get_exercise_summary",
    description="按动作汇总训练次数、总次数、总时长、平均评分、最佳次数。参数: days 1-180。",
    args={"days": "int optional, default 28"},
)
def get_exercise_summary(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    days = clamp_int(args.get("days"), 28, 1, 180)
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT exercise_type, COUNT(*) AS sessions, SUM(reps) AS total_reps,
               SUM(duration_s) AS total_duration_s, AVG(avg_form_score) AS avg_form_score,
               MAX(reps) AS best_reps
        FROM exercise_log
        WHERE user_id=? AND created_at>=? AND exercise_type IS NOT NULL
        GROUP BY exercise_type
        ORDER BY sessions DESC, total_reps DESC
        """,
        (user_id, since),
    ).fetchall()
    out = rows_to_dicts(rows)
    for item in out:
        if item.get("avg_form_score") is not None:
            item["avg_form_score"] = round(float(item["avg_form_score"]), 1)
        if item.get("total_duration_s") is not None:
            item["total_minutes"] = round(float(item["total_duration_s"]) / 60.0, 1)
    return {"ok": True, "days": days, "summary": out}
