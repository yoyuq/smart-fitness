"""Safe, app-specific tools for the Smart Fitness Agent loop.

No shell, no arbitrary URL fetch, no arbitrary SQL. These tools only read or
update the current user's fitness data through whitelisted queries. The only
network-capable tool is a constrained fitness web search that accepts a query,
not a URL or HTTP method.
"""
import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

import ai_planner
from .knowledge_loader import knowledge_ids, search_knowledge
from .todos import run_todo_write
from .web_search import search_fitness_web


TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "get_user_context_snapshot",
        "description": "读取当前用户的身体数据、今日训练、近14天记录、近28天分动作统计、训练计划和教练记忆。",
        "args": {},
        "read_only": True,
    },
    {
        "name": "get_body_metrics",
        "description": "读取最近身体指标记录。参数: limit 1-20。",
        "args": {"limit": "int optional, default 5"},
        "read_only": True,
    },
    {
        "name": "get_recent_workouts",
        "description": "读取最近训练明细。参数: days 1-90, exercise 可选动作名, limit 1-100。",
        "args": {"days": "int optional", "exercise": "string optional", "limit": "int optional"},
        "read_only": True,
    },
    {
        "name": "get_exercise_summary",
        "description": "按动作汇总训练次数、总次数、总时长、平均评分、最佳次数。参数: days 1-180。",
        "args": {"days": "int optional, default 28"},
        "read_only": True,
    },
    {
        "name": "get_active_plans",
        "description": "读取最近训练计划。参数: limit 1-5。",
        "args": {"limit": "int optional, default 3"},
        "read_only": True,
    },
    {
        "name": "get_coach_memory",
        "description": "读取教练长期记忆。参数: limit 1-30。",
        "args": {"limit": "int optional, default 10"},
        "read_only": True,
    },
    {
        "name": "search_fitness_kb",
        "description": "按 query/domains 从健身知识库按需加载相关片段。参数: query 必填或 domains 可选。只允许加载注册过的知识库 id。",
        "args": {"query": "string", "domains": "list optional"},
        "read_only": True,
    },
    {
        "name": "search_fitness_web",
        "description": "受控联网搜索健身/营养/训练相关公开信息。只接受搜索 query，不接受 URL；用于补充本地知识库没有的新知识。最终回答必须引用来源 URL，并提醒网络信息需以权威来源为准。",
        "args": {"query": "fitness/nutrition/exercise related string", "limit": "int optional, 1-5"},
        "read_only": True,
        "network": "restricted_search_only",
    },
    {
        "name": "todo_write",
        "description": "为复杂健身任务创建/更新带状态的 TODO 列表，只修改本次 Agent run 的规划状态，不修改用户健身数据。状态: pending/in_progress/waiting_approval/completed/cancelled。",
        "args": {"todos": "list of {content,status}"},
        "read_only": True,
    },
    {
        "name": "save_coach_memory",
        "description": "保存值得长期记住的用户目标、伤病、偏好或训练瓶颈。修改类工具，执行前必须获得用户确认。参数: note, category。",
        "args": {"note": "string", "category": "goal|injury|preference|observation|general"},
        "read_only": False,
        "requires_approval": True,
    },
    {
        "name": "update_body_metrics",
        "description": "新增一条身体指标记录。修改类工具，执行前必须获得用户确认。参数: weight_kg 可选, height_cm 可选, body_fat_pct 可选, notes 可选。",
        "args": {"weight_kg": "number optional", "height_cm": "number optional", "body_fat_pct": "number optional", "notes": "string optional"},
        "read_only": False,
        "requires_approval": True,
    },
    {
        "name": "create_workout_plan",
        "description": "创建训练计划。修改类工具，执行前必须获得用户确认。参数: name, exercises(list)。",
        "args": {"name": "string", "exercises": "list of objects/strings"},
        "read_only": False,
        "requires_approval": True,
    },
    {
        "name": "delete_workout_plan",
        "description": "删除一个训练计划。修改类工具，执行前必须获得用户确认。参数: plan_id。",
        "args": {"plan_id": "string"},
        "read_only": False,
        "requires_approval": True,
    },
]


_ALLOWED = {t["name"] for t in TOOL_SPECS}
_CATEGORIES = {"goal", "injury", "preference", "observation", "general"}


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(lo, min(n, hi))


def _rows_to_dicts(rows) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(r))
    return out


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


def execute_tool(conn: sqlite3.Connection, user_id: int, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute one whitelisted fitness tool for the current user."""
    args = args or {}
    if name not in _ALLOWED:
        return {"ok": False, "error": f"tool not allowed: {name}"}

    if name == "get_user_context_snapshot":
        ctx = ai_planner._load_user_context(conn, user_id)
        return {"ok": True, "context": _context_snapshot(ctx)}

    if name == "get_body_metrics":
        limit = _clamp_int(args.get("limit"), 5, 1, 20)
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
        return {"ok": True, "metrics": _rows_to_dicts(rows)}

    if name == "get_recent_workouts":
        days = _clamp_int(args.get("days"), 14, 1, 90)
        limit = _clamp_int(args.get("limit"), 50, 1, 100)
        exercise = (args.get("exercise") or "").strip()
        since = int(time.time()) - days * 86400
        if exercise:
            rows = conn.execute(
                """
                SELECT exercise_type, reps, duration_s, avg_form_score, created_at
                FROM exercise_log
                WHERE user_id=? AND created_at>=? AND exercise_type=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, since, exercise, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT exercise_type, reps, duration_s, avg_form_score, created_at
                FROM exercise_log
                WHERE user_id=? AND created_at>=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, since, limit),
            ).fetchall()
        return {"ok": True, "days": days, "exercise": exercise or None, "workouts": _rows_to_dicts(rows)}

    if name == "get_exercise_summary":
        days = _clamp_int(args.get("days"), 28, 1, 180)
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
        out = _rows_to_dicts(rows)
        for item in out:
            if item.get("avg_form_score") is not None:
                item["avg_form_score"] = round(float(item["avg_form_score"]), 1)
            if item.get("total_duration_s") is not None:
                item["total_minutes"] = round(float(item["total_duration_s"]) / 60.0, 1)
        return {"ok": True, "days": days, "summary": out}

    if name == "get_active_plans":
        limit = _clamp_int(args.get("limit"), 3, 1, 5)
        rows = conn.execute(
            """
            SELECT plan_id, name, exercises, created_at
            FROM workout_plans
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        plans = []
        for r in rows:
            item = {k: r[k] for k in r.keys()} if isinstance(r, sqlite3.Row) else dict(r)
            try:
                item["exercises"] = json.loads(item.get("exercises") or "[]")
            except Exception:
                item["exercises"] = []
            plans.append(item)
        return {"ok": True, "plans": plans}

    if name == "get_coach_memory":
        limit = _clamp_int(args.get("limit"), 10, 1, 30)
        return {"ok": True, "memories": ai_planner.get_coach_memories(conn, user_id, limit=limit)}

    if name == "search_fitness_kb":
        query = (args.get("query") or "").strip()
        raw_domains = args.get("domains") or []
        domains = [str(d) for d in raw_domains if str(d) in set(knowledge_ids())]
        return search_knowledge(query=query, domains=domains)

    if name == "search_fitness_web":
        query = (args.get("query") or "").strip()
        limit = _clamp_int(args.get("limit"), 5, 1, 5)
        return search_fitness_web(query=query, limit=limit)

    if name == "todo_write":
        return run_todo_write(args)

    if name == "save_coach_memory":
        note = (args.get("note") or "").strip()
        category = (args.get("category") or "general").strip()
        if category not in _CATEGORIES:
            category = "general"
        if len(note) < 4:
            return {"ok": False, "error": "note too short"}
        ai_planner.add_coach_memory(conn, user_id, note[:200], category=category)
        return {"ok": True, "saved": {"category": category, "note": note[:200]}}

    if name == "update_body_metrics":
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

    if name == "create_workout_plan":
        plan_name = (args.get("name") or "Agent 生成计划").strip()[:80]
        exercises = args.get("exercises") or []
        if not isinstance(exercises, list) or not exercises:
            return {"ok": False, "error": "exercises must be a non-empty list"}
        plan_id = "plan_agent_" + uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO workout_plans (plan_id, user_id, name, exercises, created_at) VALUES (?, ?, ?, ?, ?)",
            (plan_id, user_id, plan_name, json.dumps(exercises[:30], ensure_ascii=False), time.time()),
        )
        conn.commit()
        return {"ok": True, "plan_id": plan_id, "name": plan_name, "exercises": exercises[:30]}

    if name == "delete_workout_plan":
        plan_id = (args.get("plan_id") or "").strip()
        if not plan_id:
            return {"ok": False, "error": "plan_id required"}
        cur = conn.execute("DELETE FROM workout_plans WHERE plan_id=? AND user_id=?", (plan_id, user_id))
        conn.commit()
        return {"ok": True, "deleted": int(cur.rowcount or 0), "plan_id": plan_id}

    return {"ok": False, "error": "unhandled tool"}


def tool_specs_for_prompt() -> str:
    return json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2)
