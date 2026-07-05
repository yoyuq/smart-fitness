"""Background/Cron helpers for the Smart Fitness Agent.

This module is intentionally conservative: scheduled jobs only create
user-facing inbox items (reminders, reports, plan drafts). They never modify
body metrics, memories, or official workout plans. If the user wants to import a
plan draft later, the existing Agent approval path should be used.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_background_items (
    item_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    job TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    requires_approval INTEGER DEFAULT 0,
    dedupe_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    read_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_background_dedupe
    ON agent_background_items(user_id, dedupe_key);
CREATE INDEX IF NOT EXISTS idx_agent_background_user_created
    ON agent_background_items(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_background_user_status
    ON agent_background_items(user_id, status, created_at DESC);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(raw: Any, default: Any):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def ensure_background_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    conn.commit()


def _row_to_item(row) -> Dict[str, Any]:
    item = dict(row)
    item["payload"] = _loads(item.pop("payload_json", None), {})
    item["requires_approval"] = bool(item.get("requires_approval"))
    return item


def _period_key(now: Optional[int], period: str) -> str:
    dt = datetime.fromtimestamp(int(now or time.time()))
    if period == "week":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return dt.strftime("%Y-%m-%d")


def _start_of_local_day(ts: int) -> int:
    lt = time.localtime(ts)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return default


def _insert_item(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    job: str,
    kind: str,
    title: str,
    message: str,
    payload: Optional[Dict[str, Any]],
    dedupe_key: str,
    now: int,
    requires_approval: bool = False,
) -> Optional[Dict[str, Any]]:
    ensure_background_schema(conn)
    item_id = "bg_" + uuid.uuid4().hex[:16]
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO agent_background_items (
            item_id, user_id, job, kind, title, message, payload_json, status,
            requires_approval, dedupe_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            item_id,
            int(user_id),
            job,
            kind,
            title[:160],
            message[:3000],
            _dumps(payload or {}),
            1 if requires_approval else 0,
            dedupe_key,
            now,
            now,
        ),
    )
    conn.commit()
    if int(cur.rowcount or 0) <= 0:
        return None
    row = conn.execute("SELECT * FROM agent_background_items WHERE item_id=?", (item_id,)).fetchone()
    return _row_to_item(row) if row else None


def _training_summary(conn: sqlite3.Connection, user_id: int, since_ts: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS sessions,
               COALESCE(SUM(reps), 0) AS total_reps,
               COALESCE(SUM(duration_s), 0) AS total_duration_s,
               AVG(avg_form_score) AS avg_form_score,
               MAX(created_at) AS last_workout_at
        FROM exercise_log
        WHERE user_id=? AND created_at>=?
        """,
        (int(user_id), int(since_ts)),
    ).fetchone()
    by_type_rows = conn.execute(
        """
        SELECT exercise_type,
               COUNT(*) AS sessions,
               COALESCE(SUM(reps), 0) AS reps,
               COALESCE(SUM(duration_s), 0) AS duration_s,
               AVG(avg_form_score) AS avg_form_score
        FROM exercise_log
        WHERE user_id=? AND created_at>=? AND exercise_type IS NOT NULL
        GROUP BY exercise_type
        ORDER BY sessions DESC, reps DESC
        LIMIT 8
        """,
        (int(user_id), int(since_ts)),
    ).fetchall()
    summary = {
        "sessions": _safe_int(row["sessions"] if row else 0),
        "total_reps": _safe_int(row["total_reps"] if row else 0),
        "total_minutes": round(_safe_float(row["total_duration_s"] if row else 0) / 60.0, 1),
        "avg_form_score": round(_safe_float(row["avg_form_score"], 0), 1) if row and row["avg_form_score"] is not None else None,
        "last_workout_at": _safe_int(row["last_workout_at"] if row else 0) or None,
        "by_type": [],
    }
    for r in by_type_rows:
        summary["by_type"].append({
            "exercise_type": r["exercise_type"],
            "sessions": _safe_int(r["sessions"]),
            "reps": _safe_int(r["reps"]),
            "minutes": round(_safe_float(r["duration_s"]) / 60.0, 1),
            "avg_form_score": round(_safe_float(r["avg_form_score"]), 1) if r["avg_form_score"] is not None else None,
        })
    return summary


def _last_workout_at(conn: sqlite3.Connection, user_id: int) -> Optional[int]:
    row = conn.execute("SELECT MAX(created_at) AS ts FROM exercise_log WHERE user_id=?", (int(user_id),)).fetchone()
    ts = _safe_int(row["ts"] if row else 0)
    return ts or None


def _draft_weekly_plan(summary: Dict[str, Any]) -> Dict[str, Any]:
    trained = int(summary.get("sessions") or 0)
    by_type = [str(x.get("exercise_type") or "") for x in summary.get("by_type") or []]
    has_cardio = any(x.lower() in {"running", "run", "jog", "cardio"} or "跑" in x for x in by_type)
    exercises: List[Dict[str, Any]] = []

    # Keep it generic and editable; do not write into workout_plans.
    exercises.append({"day": 1, "type": "easy_run" if has_cardio else "brisk_walk", "title": "轻松有氧", "category": "cardio", "duration_min": 25, "intensity": "低-中", "note": "能完整说话的强度，作为一周启动。"})
    exercises.append({"day": 2, "type": "bodyweight_strength", "title": "自重力量", "category": "strength", "sets": 3, "reps": 12, "intensity": "中", "note": "深蹲/俯卧撑/引体按当前能力拆组。"})
    exercises.append({"day": 3, "type": "mobility", "title": "拉伸恢复", "category": "mobility", "duration_min": 15, "intensity": "低", "note": "髋、踝、小腿和肩背活动度。"})
    exercises.append({"day": 4, "type": "tempo_or_intervals", "title": "质量训练", "category": "cardio", "duration_min": 20, "intensity": "中-高", "note": "状态好做短间歇；疲劳则改轻松跑。"})
    exercises.append({"day": 5, "type": "strength_core", "title": "力量+核心", "category": "strength", "sets": 3, "reps": 10, "intensity": "中", "note": "保留2次余力，不追求力竭。"})
    exercises.append({"day": 6, "type": "long_easy", "title": "稍长有氧", "category": "cardio", "duration_min": 35 if trained >= 3 else 25, "intensity": "低", "note": "只加时长不加速度。"})
    exercises.append({"day": 7, "type": "rest", "title": "休息/散步", "category": "recovery", "duration_min": 0, "intensity": "低", "note": "睡眠和补碳水，准备下周。"})
    return {
        "name": "Agent 周计划建议草案",
        "goal": "保持训练连续性并控制疲劳",
        "weeks": 1,
        "exercises": exercises,
        "imported": False,
    }


def _weekly_report_message(summary: Dict[str, Any]) -> str:
    sessions = int(summary.get("sessions") or 0)
    minutes = summary.get("total_minutes") or 0
    reps = int(summary.get("total_reps") or 0)
    avg = summary.get("avg_form_score")
    top = summary.get("by_type") or []
    lines = [f"本周训练 {sessions} 次，总时长约 {minutes} 分钟，总次数 {reps}。"]
    if avg is not None:
        lines.append(f"平均动作评分约 {avg}。")
    if top:
        top_text = "、".join(f"{x['exercise_type']} {x['sessions']}次" for x in top[:3])
        lines.append(f"主要项目：{top_text}。")
    if sessions == 0:
        lines.append("这周没有训练记录，下周先以恢复连续性为目标。")
    elif sessions <= 2:
        lines.append("训练频率偏低，下周优先保证 3 次轻中强度训练。")
    elif sessions >= 5:
        lines.append("训练频率较高，下周注意至少安排 1 天恢复，避免堆疲劳。")
    else:
        lines.append("频率不错，下周可以维持节奏，质量课不要连续安排。")
    return "\n".join(lines)


def run_background_checks(conn: sqlite3.Connection, user_id: int, job: str = "daily_checkin", now: Optional[int] = None) -> Dict[str, Any]:
    """Run one conservative background job for a user.

    Supported jobs:
    - daily_checkin: create one inactivity/training reminder per day.
    - weekly_review: create one weekly report and one plan draft per ISO week.
    - all: run both.
    """
    ensure_background_schema(conn)
    now_i = int(now or time.time())
    job = (job or "daily_checkin").strip().lower()
    if job == "all":
        items: List[Dict[str, Any]] = []
        total_created = 0
        for sub in ("daily_checkin", "weekly_review"):
            res = run_background_checks(conn, user_id, job=sub, now=now_i)
            items.extend(res.get("items") or [])
            total_created += int(res.get("created") or 0)
        return {"ok": True, "job": "all", "created": total_created, "items": items}
    if job not in {"daily_checkin", "weekly_review"}:
        return {"ok": False, "error": f"unsupported job: {job}", "created": 0, "items": []}

    created: List[Dict[str, Any]] = []
    if job == "daily_checkin":
        day_key = _period_key(now_i, "day")
        today_start = _start_of_local_day(now_i)
        today = _training_summary(conn, user_id, today_start)
        last_ts = _last_workout_at(conn, user_id)
        days_since = None if not last_ts else max(0, int((now_i - last_ts) // 86400))
        if today["sessions"] <= 0:
            if last_ts is None:
                title = "今天还没有训练记录"
                message = "今天还没有训练记录。建议先完成 20-30 分钟低强度跑/快走，或做 3 组自重力量，重点是恢复连续性。"
            elif days_since and days_since >= 3:
                title = f"已连续约 {days_since} 天没有训练记录"
                message = f"已连续约 {days_since} 天没有训练记录。今天别直接上高强度，建议 15-25 分钟轻松有氧 + 简单拉伸，先把节奏接回来。"
            else:
                title = "今天还没有训练记录"
                message = "今天还没有训练记录。如果身体状态正常，建议安排一次轻中强度训练；如果疲劳，就做拉伸恢复也算完成。"
            item = _insert_item(
                conn,
                user_id,
                job=job,
                kind="inactivity_reminder",
                title=title,
                message=message,
                payload={"today": today, "last_workout_at": last_ts, "days_since_last_workout": days_since},
                dedupe_key=f"daily_checkin:{day_key}",
                now=now_i,
            )
            if item:
                created.append(item)
        else:
            item = _insert_item(
                conn,
                user_id,
                job=job,
                kind="daily_encouragement",
                title="今天训练已记录",
                message=f"今天已记录 {today['sessions']} 次训练，约 {today['total_minutes']} 分钟。后面注意补水、补碳水和睡眠。",
                payload={"today": today},
                dedupe_key=f"daily_done:{day_key}",
                now=now_i,
            )
            if item:
                created.append(item)

    if job == "weekly_review":
        week_key = _period_key(now_i, "week")
        since = now_i - 7 * 86400
        summary = _training_summary(conn, user_id, since)
        report = _insert_item(
            conn,
            user_id,
            job=job,
            kind="weekly_report",
            title="本周训练周报",
            message=_weekly_report_message(summary),
            payload={"summary": summary, "period_days": 7},
            dedupe_key=f"weekly_report:{week_key}",
            now=now_i,
        )
        if report:
            created.append(report)
        draft = _draft_weekly_plan(summary)
        plan = _insert_item(
            conn,
            user_id,
            job=job,
            kind="weekly_plan_suggestion",
            title="下周训练计划草案",
            message="我根据最近 7 天训练生成了一份下周计划草案。它只是建议，不会自动写入正式训练计划；需要导入时再由你确认。",
            payload={"summary": summary, "draft": draft},
            dedupe_key=f"weekly_plan_suggestion:{week_key}",
            now=now_i,
        )
        if plan:
            created.append(plan)

    return {"ok": True, "job": job, "created": len(created), "items": created}


def list_background_items(conn: sqlite3.Connection, user_id: int, status: str = "pending", limit: int = 20) -> List[Dict[str, Any]]:
    ensure_background_schema(conn)
    limit_i = max(1, min(int(limit or 20), 100))
    status = (status or "pending").strip().lower()
    if status == "all":
        rows = conn.execute(
            "SELECT * FROM agent_background_items WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (int(user_id), limit_i),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_background_items WHERE user_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (int(user_id), status, limit_i),
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def mark_background_item_read(conn: sqlite3.Connection, user_id: int, item_id: str) -> bool:
    ensure_background_schema(conn)
    now = int(time.time())
    cur = conn.execute(
        "UPDATE agent_background_items SET status='read', read_at=?, updated_at=? WHERE user_id=? AND item_id=?",
        (now, now, int(user_id), item_id),
    )
    conn.commit()
    return int(cur.rowcount or 0) > 0


def dismiss_background_item(conn: sqlite3.Connection, user_id: int, item_id: str) -> bool:
    ensure_background_schema(conn)
    now = int(time.time())
    cur = conn.execute(
        "UPDATE agent_background_items SET status='dismissed', updated_at=? WHERE user_id=? AND item_id=?",
        (now, int(user_id), item_id),
    )
    conn.commit()
    return int(cur.rowcount or 0) > 0
