"""Workout plan tools."""
import json
import time
import uuid
from typing import Any, Dict, List

import ai_planner

from .base import clamp_int
from .registry import register_tool


def _normalize_exercises(exercises: Any) -> List[Dict[str, Any]]:
    if not isinstance(exercises, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in exercises[:50]:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"type": name[:40], "title": name[:80], "category": _infer_category(name), "sets": 1, "reps": 0, "duration_min": 0, "distance_km": 0.0, "intensity": "", "note": ""})
            continue
        if not isinstance(item, dict):
            continue
        typ = item.get("type") or item.get("exercise_type") or item.get("exercise") or item.get("name") or "custom"
        sets = item.get("sets") if item.get("sets") is not None else item.get("target_sets")
        reps = item.get("reps") if item.get("reps") is not None else item.get("target_reps")
        note = item.get("note") or item.get("intensity_note") or item.get("notes") or ""
        try:
            sets_i = int(sets or 0)
        except Exception:
            sets_i = 0
        try:
            reps_i = int(reps or 0)
        except Exception:
            reps_i = 0
        obj: Dict[str, Any] = {
            "type": str(typ).strip()[:40] or "custom",
            "title": str(item.get("title") or typ).strip()[:80] or str(typ).strip()[:40] or "自定义项目",
            "category": str(item.get("category") or _infer_category(str(typ))).strip()[:30],
            "sets": max(0, sets_i),
            "reps": max(0, reps_i),
            "duration_min": int(float(item.get("duration_min") or item.get("duration") or 0)),
            "distance_km": float(item.get("distance_km") or item.get("distance") or 0),
            "intensity": str(item.get("intensity") or "").strip()[:40],
            "note": str(note).strip()[:300],
        }
        if item.get("week") is not None:
            try:
                obj["week"] = int(item.get("week") or 1)
            except Exception:
                pass
        if item.get("day") is not None:
            try:
                obj["day"] = int(item.get("day") or 1)
            except Exception:
                pass
        out.append(obj)
    return out


def _infer_category(typ: str) -> str:
    t = (typ or "").lower()
    if any(x in t for x in ["run", "swim", "bike", "cardio", "跑", "游泳", "骑行"]):
        return "cardio"
    if any(x in t for x in ["stretch", "mobility", "yoga", "拉伸", "瑜伽"]):
        return "mobility"
    if any(x in t for x in ["rest", "recovery", "休息", "恢复"]):
        return "recovery"
    if any(x in t for x in ["squat", "push", "pull", "lunge", "plank", "curl", "press", "深蹲", "俯卧撑", "引体"]):
        return "strength"
    return "custom"


@register_tool(
    name="get_active_plans",
    description="读取最近训练计划。参数: limit 1-5。",
    args={"limit": "int optional, default 3"},
)
def get_active_plans(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = clamp_int(args.get("limit"), 3, 1, 5)
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
        item = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else dict(r)
        try:
            item["exercises"] = json.loads(item.get("exercises") or "[]")
        except Exception:
            item["exercises"] = []
        plans.append(item)
    return {"ok": True, "plans": plans}


@register_tool(
    name="draft_workout_plan",
    description="生成训练计划草稿。只返回草稿，不写入用户训练计划；用户确认导入前可编辑。参数: goal, weeks 1-8。",
    args={"goal": "string", "weeks": "int optional, default 2"},
)
def draft_workout_plan(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    goal = (args.get("goal") or "增肌").strip()[:120]
    weeks = clamp_int(args.get("weeks"), 2, 1, 8)
    res = ai_planner.generate_plan(conn, user_id, goal, weeks, import_to_plans=False)
    if not res.get("ok"):
        return res
    exercises = _normalize_exercises(res.get("plans") or [])
    return {
        "ok": True,
        "draft": True,
        "name": res.get("plan_name") or f"AI 计划-{goal[:20]} {weeks}周",
        "goal": goal,
        "weeks": weeks,
        "exercises": exercises,
        "reason": res.get("reason") or "已结合你的身体数据、训练记录和目标生成计划。",
        "message": "已生成训练计划草稿，用户可编辑后导入到训练计划。",
    }


@register_tool(
    name="create_workout_plan",
    description="创建训练计划。修改类工具，执行前必须获得用户确认。参数: name, exercises(list)。",
    args={"name": "string", "exercises": "list of objects/strings"},
    read_only=False,
    requires_approval=True,
)
def create_workout_plan(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    plan_name = (args.get("name") or "Agent 生成计划").strip()[:80]
    exercises = _normalize_exercises(args.get("exercises") or [])
    if not exercises:
        return {"ok": False, "error": "exercises must be a non-empty list"}
    plan_id = "plan_agent_" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO workout_plans (plan_id, user_id, name, exercises, created_at) VALUES (?, ?, ?, ?, ?)",
        (plan_id, user_id, plan_name, json.dumps(exercises[:50], ensure_ascii=False), time.time()),
    )
    conn.commit()
    return {"ok": True, "plan_id": plan_id, "name": plan_name, "exercises": exercises[:50]}


@register_tool(
    name="delete_workout_plan",
    description="删除一个训练计划。修改类工具，执行前必须获得用户确认。参数: plan_id。",
    args={"plan_id": "string"},
    read_only=False,
    requires_approval=True,
)
def delete_workout_plan(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    plan_id = (args.get("plan_id") or "").strip()
    if not plan_id:
        return {"ok": False, "error": "plan_id required"}
    cur = conn.execute("DELETE FROM workout_plans WHERE plan_id=? AND user_id=?", (plan_id, user_id))
    conn.commit()
    return {"ok": True, "deleted": int(cur.rowcount or 0), "plan_id": plan_id}
