"""main_v2_extra.py -补齐 APP 必需但被遗漏的 12 个 v2 路由 (2026-05-28).

由 main_v2_routes.py 末尾 import 触发挂载.
覆盖:
  /api/v2/plans (GET/POST), /api/v2/plans/{plan_id} (DELETE)
  /api/v2/stats/daily, /api/v2/stats/weekly
  /api/v2/sessions/history
  /api/v2/metrics/latest
  /api/v2/exercise/log (GET/POST), /api/v2/exercise/summary
  /api/v2/devices/bind (POST), /bindings (GET), /bind/{device_id} (DELETE)
  /api/v2/vision/infer (POST - 简版, /full 已在 main_v2_routes)
"""
import os, json, time, uuid, secrets, base64, logging, calendar
from typing import Optional, Dict, Any
from fastapi import Request
from fastapi.responses import JSONResponse

import auth
import fitness_agent
from main import app

log = logging.getLogger("v2_extra")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "fitness.db")


def _db():
    import sqlite3
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _user(req: Request) -> Optional[Dict]:
    h = req.headers.get("Authorization") or req.headers.get("authorization")
    return auth.require_auth(h)


def _unauth():
    return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)


def _rep_image_urls(row: Dict[str, Any], include_frames: bool = True) -> Dict[str, Any]:
    """Build public /repdata URLs for one rep's saved keyframes/clip frames."""
    keyframes = []
    for key in ("start_frame", "peak_frame", "end_frame"):
        v = row.get(key)
        if not v:
            continue
        p = str(v).replace("\\", "/")
        idx = p.find("data/")
        keyframes.append("/repdata/" + p[idx + 5:] if idx >= 0 else p)

    frames = []
    frame_count = 0
    clip_dir = row.get("clip_dir")
    if clip_dir:
        p = str(clip_dir).replace("\\", "/")
        idx = p.find("data/")
        if idx >= 0:
            sub = p[idx + 5:]
            d = os.path.join(ROOT, p[idx:])
        else:
            sub = p
            d = os.path.join(ROOT, p)
        if os.path.isdir(d):
            names = [fn for fn in sorted(os.listdir(d)) if fn.lower().endswith((".jpg", ".jpeg", ".png"))]
            frame_count = len(names)
            if include_frames:
                frames = [f"/repdata/{sub}/{fn}" for fn in names]
    return {"keyframes": keyframes, "frames": frames, "frame_count": frame_count, "has_images": bool(keyframes or frame_count)}


def _period_since(period: str) -> float:
    """Return local-period start timestamp for day/week/month/year."""
    period = (period or "week").lower()
    now = time.time()
    lt = time.localtime(now)
    if period == "day":
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if period == "month":
        return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    if period == "year":
        return time.mktime((lt.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))
    # week: Monday 00:00 local time
    monday = time.localtime(now - lt.tm_wday * 86400)
    return time.mktime((monday.tm_year, monday.tm_mon, monday.tm_mday, 0, 0, 0, 0, 0, -1))


def _normalize_plan_exercises(exercises):
    if isinstance(exercises, str):
        try:
            exercises = json.loads(exercises or "[]")
        except Exception:
            return exercises or "[]"
    if not isinstance(exercises, list):
        return "[]"
    out = []
    for item in exercises[:50]:
        if isinstance(item, str):
            out.append({"type": item.strip(), "title": item.strip(), "category": _infer_plan_category(item.strip()), "sets": 1, "reps": 0, "duration_min": 0, "distance_km": 0.0, "intensity": "", "note": ""})
            continue
        if not isinstance(item, dict):
            continue
        typ = item.get("type") or item.get("exercise_type") or item.get("exercise") or item.get("name") or "custom"
        sets = item.get("sets") if item.get("sets") is not None else item.get("target_sets")
        reps = item.get("reps") if item.get("reps") is not None else item.get("target_reps")
        note = item.get("note") or item.get("intensity_note") or item.get("notes") or ""
        title = item.get("title") or item.get("name") or item.get("exercise") or typ
        category = item.get("category") or _infer_plan_category(str(typ))
        duration = item.get("duration_min") if item.get("duration_min") is not None else item.get("duration")
        distance = item.get("distance_km") if item.get("distance_km") is not None else item.get("distance")
        intensity = item.get("intensity") or item.get("intensity_note") or ""
        obj = {
            "type": str(typ).strip()[:40] or "custom",
            "title": str(title).strip()[:80] or str(typ).strip()[:40] or "自定义项目",
            "category": str(category).strip()[:30] or "custom",
            "sets": int(sets or 0),
            "reps": int(reps or 0),
            "duration_min": int(float(duration or 0)),
            "distance_km": float(distance or 0),
            "intensity": str(intensity).strip()[:40],
            "note": str(note).strip()[:300],
        }
        if item.get("week") is not None:
            obj["week"] = int(item.get("week") or 1)
        if item.get("day") is not None:
            obj["day"] = int(item.get("day") or 1)
        out.append(obj)
    return json.dumps(out, ensure_ascii=False)


def _infer_plan_category(typ: str) -> str:
    t = (typ or "").lower()
    if any(x in t for x in ["run", "running", "jog", "swim", "cycling", "bike", "cardio", "跑", "游泳", "骑行"]):
        return "cardio"
    if any(x in t for x in ["stretch", "mobility", "yoga", "拉伸", "灵活", "瑜伽"]):
        return "mobility"
    if any(x in t for x in ["rest", "recovery", "休息", "恢复"]):
        return "recovery"
    if any(x in t for x in ["squat", "push", "pull", "lunge", "plank", "curl", "press", "力量", "深蹲", "俯卧撑", "引体"]):
        return "strength"
    return "custom"


# ============================================================
# Plans
# ============================================================
@app.get("/api/v2/plans")
async def x_plans_list(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        rows = c.execute(
            "SELECT plan_id, name, exercises, created_at FROM workout_plans WHERE user_id=? ORDER BY created_at DESC",
            (u["user_id"],)
        ).fetchall()
        return JSONResponse({"plans": [dict(r) for r in rows]})
    finally:
        c.close()


@app.post("/api/v2/plans")
async def x_plans_create(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "message": "name required"}, status_code=400)
    plan_id = "plan_" + uuid.uuid4().hex[:12]
    exercises = _normalize_plan_exercises(body.get("exercises") or "[]")
    c = _db()
    try:
        c.execute(
            "INSERT INTO workout_plans (plan_id, user_id, name, exercises) VALUES (?, ?, ?, ?)",
            (plan_id, u["user_id"], name, exercises)
        )
        c.commit()
        return JSONResponse({"ok": True, "plan_id": plan_id, "name": name})
    finally:
        c.close()


@app.put("/api/v2/plans/{plan_id}")
async def x_plans_update(plan_id: str, req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    name = (body.get("name") or "").strip()
    exercises = body.get("exercises")
    if not name and exercises is None:
        return JSONResponse({"ok": False, "message": "name or exercises required"}, status_code=400)
    fields = []
    vals = []
    if name:
        fields.append("name=?")
        vals.append(name[:120])
    if exercises is not None:
        fields.append("exercises=?")
        vals.append(_normalize_plan_exercises(exercises))
    vals.extend([plan_id, u["user_id"]])
    c = _db()
    try:
        cur = c.execute(f"UPDATE workout_plans SET {', '.join(fields)} WHERE plan_id=? AND user_id=?", vals)
        c.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        row = c.execute(
            "SELECT plan_id, name, exercises, created_at FROM workout_plans WHERE plan_id=? AND user_id=?",
            (plan_id, u["user_id"]),
        ).fetchone()
        return JSONResponse({"ok": True, "plan": dict(row) if row else None})
    finally:
        c.close()


@app.delete("/api/v2/plans/{plan_id}")
async def x_plans_delete(plan_id: str, req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        cur = c.execute(
            "DELETE FROM workout_plans WHERE plan_id=? AND user_id=?",
            (plan_id, u["user_id"])
        )
        c.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "message": "deleted"})
    finally:
        c.close()


@app.post("/api/v2/plans/ai_draft")
async def x_plans_ai_draft(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    prompt = (body.get("prompt") or body.get("goal") or "").strip()
    if not prompt:
        return JSONResponse({"ok": False, "message": "prompt required"}, status_code=400)
    try:
        weeks = int(body.get("weeks") or 2)
    except Exception:
        weeks = 2
    weeks = max(1, min(8, weeks))

    plan_name = (body.get("plan_name") or body.get("name") or "").strip()
    categories = body.get("categories") or []
    selected_items = body.get("selected_items") or body.get("exercise_options") or []
    weekly_days = body.get("weekly_training_days")
    session_minutes = body.get("session_minutes")

    def _item_label(x):
        if isinstance(x, dict):
            return str(x.get("title") or x.get("name") or x.get("type") or x.get("id") or "").strip()
        return str(x).strip()

    builder_lines = []
    if plan_name:
        builder_lines.append(f"计划名称: {plan_name}")
    builder_lines.append(f"训练周期: {weeks} 周")
    if weekly_days:
        builder_lines.append(f"每周训练天数: {weekly_days}")
    if session_minutes:
        builder_lines.append(f"单次训练时长: {session_minutes} 分钟")
    if isinstance(categories, list) and categories:
        builder_lines.append("已选运动分类: " + "、".join([str(c).strip() for c in categories if str(c).strip()]))
    if isinstance(selected_items, list) and selected_items:
        labels = [_item_label(x) for x in selected_items]
        builder_lines.append("已选训练项目: " + "、".join([x for x in labels if x]))
    builder_prompt = prompt
    if builder_lines:
        builder_prompt = prompt + "\n\n制定训练计划向导信息:\n" + "\n".join(builder_lines)

    c = _db()
    try:
        import ai_planner
        res = ai_planner.generate_plan(c, u["user_id"], builder_prompt[:1200], weeks=weeks, import_to_plans=False)
        if not res.get("ok"):
            return JSONResponse(res)
        exercises = _normalize_plan_exercises(res.get("plans") or [])
        try:
            exercises_list = json.loads(exercises or "[]")
        except Exception:
            exercises_list = []
        return JSONResponse({
            "ok": True,
            "draft": True,
            "name": res.get("plan_name") or plan_name or f"AI 计划-{prompt[:20]} {weeks}周",
            "reason": res.get("reason") or "已结合你的身体数据、训练记录和目标生成计划。",
            "exercises": exercises_list,
            "goal": prompt,
            "weeks": weeks,
        })
    finally:
        c.close()


@app.post("/api/v2/plans/{plan_id}/checkin")
async def x_plans_checkin(plan_id: str, req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    item = body.get("item") if isinstance(body.get("item"), dict) else body
    typ = (item.get("type") or item.get("exercise_type") or item.get("title") or "custom").strip()[:60]
    if not typ:
        return JSONResponse({"ok": False, "message": "type required"}, status_code=400)
    reps = int(item.get("reps") or 0)
    sets = int(item.get("sets") or 1)
    duration_s = int(float(item.get("duration_s") or (float(item.get("duration_min") or 0) * 60)))
    note = (body.get("note") or item.get("note") or "").strip()[:300]
    c = _db()
    try:
        row = c.execute("SELECT plan_id FROM workout_plans WHERE plan_id=? AND user_id=?", (plan_id, u["user_id"])).fetchone()
        if not row:
            return JSONResponse({"ok": False, "message": "plan not found"}, status_code=404)
        c.execute(
            "INSERT INTO user_exercise_log (user_id, session_id, exercise_type, reps, sets, duration_seconds, avg_form_score, calories_kcal, performed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (u["user_id"], f"plan_checkin_{uuid.uuid4().hex[:10]}", typ, reps, max(1, sets), duration_s, None, None, time.time())
        )
        c.commit()
        return JSONResponse({"ok": True, "message": "checked in", "exercise_type": typ, "note": note})
    finally:
        c.close()


# ============================================================
# Stats: daily / weekly
# ============================================================
def _stats_summary(user_id: int, since_ts: float):
    """聚合训练统计.

    注意: 实际训练链路 (workout/summary) 写入的是 exercise_log 表;
    user_exercise_log 只有手动记录 API 在写. 此前从 user_exercise_log
    聚合导致 Today's Summary 永远为 0.
    """
    c = _db()
    try:
        # 概要
        row = c.execute(
            "SELECT COUNT(*)                      AS sessions_count, "
            "       COALESCE(SUM(reps), 0)        AS total_reps, "
            "       COALESCE(SUM(duration_s), 0)  AS total_seconds, "
            "       AVG(avg_form_score)           AS avg_score "
            "FROM exercise_log WHERE user_id=? AND created_at>=?",
            (user_id, since_ts)
        ).fetchone()

        # 最近 session 列表 (兼容 sessions 表 + user_exercise_log)
        sess_rows = c.execute(
            "SELECT s.session_id, s.exercise_type, s.start_time, s.end_time, "
            "       s.total_reps, s.avg_form_score, s.status, s.device_id "
            "FROM sessions s "
            "WHERE s.user_id=? AND s.start_time>=? ORDER BY s.start_time DESC LIMIT 20",
            (str(user_id), since_ts)
        ).fetchall()
        sess_list = [dict(r) for r in sess_rows]
    finally:
        c.close()

    return {
        "sessions_count": int(row["sessions_count"] or 0),
        "total_reps": int(row["total_reps"] or 0),
        "total_minutes": round(float(row["total_seconds"] or 0) / 60.0, 2),
        "avg_score": round(float(row["avg_score"] or 0.0), 1),
        "sessions": sess_list,
    }


@app.get("/api/v2/stats/daily")
async def x_stats_daily(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    # 今天 00:00 (本地时间)
    import time as _t
    now = _t.time()
    lt = _t.localtime(now)
    midnight = _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return JSONResponse({"ok": True, "stats": _stats_summary(u["user_id"], midnight)})


@app.get("/api/v2/stats/weekly")
async def x_stats_weekly(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    import time as _t
    seven_days_ago = _t.time() - 7 * 86400
    return JSONResponse({"ok": True, "stats": _stats_summary(u["user_id"], seven_days_ago)})


# ============================================================
# Sessions history
# ============================================================
@app.get("/api/v2/sessions/history")
async def x_sessions_history(req: Request, user_id: Optional[int] = None, limit: int = 50):
    u = _user(req)
    if not u:
        return _unauth()
    target_uid = u["user_id"]  # 只允许查自己的, user_id 参数忽略
    c = _db()
    try:
        rows = c.execute(
            "SELECT session_id, device_id, user_id, exercise_type, start_time, end_time, "
            "       total_reps, avg_form_score, status "
            "FROM sessions WHERE user_id=? ORDER BY start_time DESC LIMIT ?",
            (str(target_uid), limit)
        ).fetchall()
        return JSONResponse({"sessions": [dict(r) for r in rows]})
    finally:
        c.close()


# ============================================================
# Body Metrics: latest (POST/GET 已在 main_v2_routes)
# ============================================================
@app.get("/api/v2/metrics/latest")
async def x_metrics_latest(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        row = c.execute(
            "SELECT id, timestamp, weight_kg, height_cm, body_fat_pct, resting_hr, notes "
            "FROM user_body_metrics WHERE user_id=? ORDER BY timestamp DESC LIMIT 1",
            (u["user_id"],)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": True, "latest": None})
        d = dict(row)
        # BMI 计算
        try:
            w, h = d.get("weight_kg"), d.get("height_cm")
            if w and h and h > 0:
                d["bmi"] = round(float(w) / ((float(h) / 100.0) ** 2), 1)
        except Exception:
            d["bmi"] = None
        return JSONResponse({"ok": True, "latest": d})
    finally:
        c.close()


# ============================================================
# Exercise Log: GET / POST / Summary
# ============================================================
@app.post("/api/v2/exercise/log")
async def x_exer_log_add(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    et = (body.get("exercise_type") or "").strip()
    if not et:
        return JSONResponse({"ok": False, "message": "exercise_type required"}, status_code=400)
    c = _db()
    try:
        c.execute(
            "INSERT INTO user_exercise_log (user_id, session_id, exercise_type, reps, sets, "
            "                               duration_seconds, avg_form_score, calories_kcal, performed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (u["user_id"], body.get("session_id"), et,
             int(body.get("reps") or 0), int(body.get("sets") or 1),
             float(body.get("duration_seconds") or 0.0),
             body.get("avg_form_score"), body.get("calories_kcal"),
             float(time.time()))
        )
        c.commit()
        return JSONResponse({"ok": True, "message": "logged"})
    finally:
        c.close()


@app.get("/api/v2/exercise/log")
async def x_exer_log_list(req: Request, limit: int = 50, days: int = 30):
    u = _user(req)
    if not u:
        return _unauth()
    since = time.time() - max(1, days) * 86400
    c = _db()
    try:
        # 真实训练链路写 exercise_log, 手动补录写 user_exercise_log: 两表合并
        rows = c.execute(
            "SELECT id, exercise_type, reps, sets, duration_seconds, avg_form_score, performed_at "
            "FROM user_exercise_log WHERE user_id=? AND performed_at>=? "
            "UNION ALL "
            "SELECT log_id AS id, exercise_type, reps, 1 AS sets, duration_s AS duration_seconds, "
            "       avg_form_score, created_at AS performed_at "
            "FROM exercise_log WHERE user_id=? AND created_at>=? "
            "ORDER BY performed_at DESC LIMIT ?",
            (u["user_id"], since, u["user_id"], since, limit)
        ).fetchall()
        return JSONResponse({"ok": True, "log": [dict(r) for r in rows]})
    finally:
        c.close()


@app.get("/api/v2/exercise/summary")
async def x_exer_summary(req: Request, days: int = 7):
    u = _user(req)
    if not u:
        return _unauth()
    since = time.time() - max(1, days) * 86400
    c = _db()
    try:
        rows = c.execute(
            "SELECT exercise_type, "
            "       COALESCE(SUM(reps),0)             AS total_reps, "
            "       COUNT(*)                          AS sessions, "
            "       COALESCE(SUM(duration_seconds),0) AS total_seconds, "
            "       AVG(avg_form_score)               AS avg_form "
            "FROM ("
            "  SELECT exercise_type, reps, duration_seconds, avg_form_score "
            "  FROM user_exercise_log WHERE user_id=? AND performed_at>=? "
            "  UNION ALL "
            "  SELECT exercise_type, reps, duration_s, avg_form_score "
            "  FROM exercise_log WHERE user_id=? AND created_at>=?"
            ") GROUP BY exercise_type ORDER BY total_reps DESC",
            (u["user_id"], since, u["user_id"], since)
        ).fetchall()
        return JSONResponse({"ok": True, "days": days, "by_type": [dict(r) for r in rows]})
    finally:
        c.close()


@app.get("/api/v2/training/data")
async def x_training_data(req: Request, period: str = "week"):
    """App-facing training data page: summary + sessions + reps + image availability."""
    u = _user(req)
    if not u:
        return _unauth()
    period = (period or "week").lower()
    if period not in ("day", "week", "month", "year"):
        period = "week"
    since = _period_since(period)
    c = _db()
    try:
        summary_row = c.execute(
            "SELECT COUNT(*) AS sessions_count, COALESCE(SUM(reps),0) AS total_reps, "
            "       COALESCE(SUM(duration_s),0) AS total_seconds, AVG(avg_form_score) AS avg_score "
            "FROM exercise_log WHERE user_id=? AND created_at>=?",
            (u["user_id"], since)
        ).fetchone()
        type_rows = c.execute(
            "SELECT exercise_type, COALESCE(SUM(reps),0) AS total_reps, COUNT(*) AS sessions, "
            "       COALESCE(SUM(duration_s),0) AS total_seconds, AVG(avg_form_score) AS avg_form "
            "FROM exercise_log WHERE user_id=? AND created_at>=? "
            "GROUP BY exercise_type ORDER BY total_reps DESC",
            (u["user_id"], since)
        ).fetchall()
        sess_rows = c.execute(
            "SELECT session_id, device_id, exercise_type, start_time, end_time, total_reps, avg_form_score, status "
            "FROM sessions WHERE user_id=? AND start_time>=? ORDER BY start_time DESC LIMIT 80",
            (str(u["user_id"]), since)
        ).fetchall()
        sessions = []
        for s in sess_rows:
            sd = dict(s)
            rep_rows = c.execute(
                "SELECT id, session_id, rep_index, exercise, total, depth, control, symmetry, peak_angle, "
                "       duration_s, feedback, ts, true_label, error_type, start_frame, peak_frame, end_frame, clip_dir "
                "FROM rep_scores WHERE session_id=? ORDER BY rep_index ASC, id ASC",
                (sd["session_id"],)
            ).fetchall()
            reps = []
            for rr in rep_rows:
                rd = dict(rr)
                images = _rep_image_urls(rd, include_frames=False)
                reps.append({
                    "id": rd.get("id"),
                    "rep_index": rd.get("rep_index"),
                    "exercise": rd.get("exercise"),
                    "total": rd.get("total"),
                    "depth": rd.get("depth"),
                    "control": rd.get("control"),
                    "symmetry": rd.get("symmetry"),
                    "peak_angle": rd.get("peak_angle"),
                    "duration_s": rd.get("duration_s"),
                    "feedback": rd.get("feedback"),
                    "ts": rd.get("ts"),
                    "true_label": rd.get("true_label"),
                    "error_type": rd.get("error_type"),
                    "has_images": images["has_images"],
                    "image_count": len(images["keyframes"]) + int(images["frame_count"] or 0),
                    "keyframes": images["keyframes"],
                })
            sd["reps"] = reps
            sd["rep_count"] = len(reps)
            sd["has_images"] = any(r.get("has_images") for r in reps)
            sessions.append(sd)
    finally:
        c.close()
    return JSONResponse({
        "ok": True,
        "period": period,
        "since": since,
        "summary": {
            "sessions_count": int(summary_row["sessions_count"] or 0),
            "total_reps": int(summary_row["total_reps"] or 0),
            "total_minutes": round(float(summary_row["total_seconds"] or 0) / 60.0, 2),
            "avg_score": round(float(summary_row["avg_score"] or 0.0), 1),
        },
        "by_type": [dict(r) for r in type_rows],
        "sessions": sessions,
    })


@app.get("/api/v2/training/rep/{rep_id}/images")
async def x_training_rep_images(rep_id: int, req: Request):
    """Return saved keyframes and clip-frame URLs for one rep owned by current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        row = c.execute(
            "SELECT r.id, r.session_id, r.rep_index, r.exercise, r.total, r.depth, r.control, r.symmetry, "
            "       r.peak_angle, r.duration_s, r.feedback, r.ts, r.true_label, r.error_type, "
            "       r.start_frame, r.peak_frame, r.end_frame, r.clip_dir "
            "FROM rep_scores r JOIN sessions s ON s.session_id=r.session_id "
            "WHERE r.id=? AND s.user_id=?",
            (rep_id, str(u["user_id"]))
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        d = dict(row)
        images = _rep_image_urls(d, include_frames=True)
        rep = {k: d.get(k) for k in ("id", "session_id", "rep_index", "exercise", "total", "depth", "control", "symmetry", "peak_angle", "duration_s", "feedback", "ts", "true_label", "error_type")}
        return JSONResponse({"ok": True, "rep": rep, **images})
    finally:
        c.close()


@app.post("/api/v2/training/rep/{rep_id}/ai_coach")
async def x_training_rep_ai_coach(rep_id: int, req: Request):
    """Two-stage vision + LLM coach analysis for one rep owned by current user.

    Body (optional): {"frames": 1|3|5|7|9}. Default 5. Uses smart sampling from
    the rep clip (bottom pinned to middle frame) + Stage 1 (火山/qwen-vl) +
    Stage 2 (text LLM) + evidence-cited rule scoring + Stage-1 conflict guard.
    """
    u = _user(req)
    if not u:
        return _unauth()
    try:
        body = await req.json()
    except Exception:
        body = {}
    args: Dict[str, Any] = {"rep_id": rep_id}
    if body.get("frames") is not None:
        args["frames"] = body.get("frames")
    from fitness_agent.toolkit.vision_tools import analyze_rep_two_stage_tool
    c = _db()
    try:
        result = analyze_rep_two_stage_tool(c, u["user_id"], args)
    finally:
        c.close()
    # Only surface fields the App needs (drop raw_reply / prompt payloads)
    stage2 = result.get("stage_reason") or {}
    analysis = stage2.get("analysis") or {}
    stage1 = result.get("stage_extract") or {}
    features = stage1.get("features") or {}
    slim = {
        "ok": bool(result.get("ok")),
        "rep_id": rep_id,
        "frames_used": result.get("frames_used") or {},
        "stage1_conflicts": result.get("stage1_conflicts") or [],
        "observation": {
            "exercise_visible": features.get("exercise_visible"),
            "alignment_cues": features.get("alignment_cues") or [],
            "observed_angles_deg": (
                features.get("observed_angles_deg_at_bottom")
                or features.get("observed_angles_deg")
                or {}
            ),
            "tempo": features.get("tempo_observation"),
            "confidence": features.get("confidence"),
            "camera_angle": features.get("camera_angle"),
            "provider": stage1.get("provider"),
            "model": stage1.get("model"),
            "frames_sent": stage1.get("frames_sent") or 1,
        },
        "analysis": analysis,
        "note": result.get("note"),
    }
    if not result.get("ok"):
        slim["error"] = result.get("error") or stage2.get("error") or stage1.get("error") or "analysis failed"
    return JSONResponse(slim)


@app.post("/api/v2/agent/chat")
async def x_fitness_agent_chat(req: Request):
    """Dedicated user fitness agent: routes to coach/analysis/plan/nutrition knowledge."""
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
    mode = (body.get("mode") or "auto").strip()
    c = _db()
    try:
        # Persist user/assistant turns on the server side so switching App tabs,
        # fragment recreation, or App restart does not erase Agent context.
        recent_history = fitness_agent.get_agent_chat_history(c, u["user_id"], limit=20)
        compact_history = fitness_agent.prepare_llm_history_with_summary(c, u["user_id"], recent_history, recent_limit=10)
        fitness_agent.add_agent_chat_message(c, u["user_id"], "user", message, mode=mode)
        res = fitness_agent.start_run(
            c,
            u["user_id"],
            message,
            mode=mode,
            history=compact_history,
        )
        reply = (res.get("reply") or "").strip()
        if reply:
            fitness_agent.add_agent_chat_message(
                c,
                u["user_id"],
                "assistant",
                reply,
                mode=mode,
                domains=res.get("domains") or [],
            )
    finally:
        c.close()
    return JSONResponse(res)


@app.get("/api/v2/agent/history")
async def x_fitness_agent_history(req: Request, limit: int = 50):
    """Return persisted Fitness Agent chat history for the current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        messages = fitness_agent.get_agent_chat_history(c, u["user_id"], limit=limit)
        return JSONResponse({"ok": True, "messages": messages})
    finally:
        c.close()


@app.delete("/api/v2/agent/history")
async def x_fitness_agent_history_clear(req: Request):
    """Clear persisted Fitness Agent chat history for the current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        deleted = fitness_agent.delete_agent_chat_history(c, u["user_id"])
        return JSONResponse({"ok": True, "message": "cleared", "deleted": deleted})
    finally:
        c.close()


@app.get("/api/v2/agent/approvals")
async def x_fitness_agent_approvals(req: Request, limit: int = 20):
    """List pending Agent tool approvals for the current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        approvals = fitness_agent.list_pending_approvals(c, u["user_id"], limit=limit)
        return JSONResponse({"ok": True, "approvals": approvals})
    finally:
        c.close()


@app.post("/api/v2/agent/approvals/{approval_id}/approve")
async def x_fitness_agent_approval_approve(approval_id: str, req: Request):
    """Approve and execute one pending Agent write-tool request."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        item = fitness_agent.get_approval(c, u["user_id"], approval_id)
        if not item:
            return JSONResponse({"ok": False, "error": "approval not found"}, status_code=404)
        if item.get("status") != "pending":
            return JSONResponse({"ok": False, "error": "approval already decided", "approval": item}, status_code=409)
        result = fitness_agent.execute_tool(c, u["user_id"], item["tool_name"], item.get("args") or {})
        fitness_agent.mark_approval(c, u["user_id"], approval_id, "executed" if result.get("ok") else "failed", result)
        resume = fitness_agent.resume_run_after_approval(c, u["user_id"], item, result)
        reply = (resume.get("reply") or "").strip()
        if reply:
            fitness_agent.add_agent_chat_message(
                c,
                u["user_id"],
                "assistant",
                reply,
                mode="approval_resume",
                domains=[],
            )
        return JSONResponse({
            "ok": bool(result.get("ok")),
            "approval_id": approval_id,
            "run_id": item.get("run_id"),
            "result": result,
            "resume": resume,
            "reply": reply,
            "message": reply or ("executed" if result.get("ok") else "failed"),
            "run_status": resume.get("run_status"),
        })
    finally:
        c.close()


@app.post("/api/v2/agent/approvals/{approval_id}/deny")
async def x_fitness_agent_approval_deny(approval_id: str, req: Request):
    """Deny one pending Agent write-tool request."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        item = fitness_agent.get_approval(c, u["user_id"], approval_id)
        if not item:
            return JSONResponse({"ok": False, "error": "approval not found"}, status_code=404)
        if item.get("status") != "pending":
            return JSONResponse({"ok": False, "error": "approval already decided", "approval": item}, status_code=409)
        fitness_agent.mark_approval(c, u["user_id"], approval_id, "denied", {"ok": False, "message": "user denied"})
        resume = fitness_agent.resume_run_after_denial(c, u["user_id"], item)
        reply = (resume.get("reply") or "").strip()
        if reply:
            fitness_agent.add_agent_chat_message(
                c,
                u["user_id"],
                "assistant",
                reply,
                mode="approval_resume",
                domains=[],
            )
        return JSONResponse({
            "ok": True,
            "approval_id": approval_id,
            "run_id": item.get("run_id"),
            "message": reply or "denied",
            "resume": resume,
            "reply": reply,
            "run_status": resume.get("run_status"),
        })
    finally:
        c.close()


@app.post("/api/v2/agent/nutrition_plan")
async def x_fitness_agent_nutrition(req: Request):
    """Shortcut endpoint for the initial nutritionist capability."""
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    goal = (body.get("goal") or "维持训练表现并优化体成分").strip()
    c = _db()
    try:
        msg = f"帮我规划饮食，先给各类营养目标量，再给具体食堂三餐和加餐建议。目标: {goal}"
        recent_history = fitness_agent.get_agent_chat_history(c, u["user_id"], limit=20)
        compact_history = fitness_agent.prepare_llm_history_with_summary(c, u["user_id"], recent_history, recent_limit=10)
        fitness_agent.add_agent_chat_message(c, u["user_id"], "user", msg, mode="nutrition")
        res = fitness_agent.start_run(
            c,
            u["user_id"],
            msg,
            mode="nutrition",
            history=compact_history,
        )
        reply = (res.get("reply") or "").strip()
        if reply:
            fitness_agent.add_agent_chat_message(
                c,
                u["user_id"],
                "assistant",
                reply,
                mode="nutrition",
                domains=res.get("domains") or [],
            )
    finally:
        c.close()
    return JSONResponse(res)


@app.get("/api/v2/agent/kb")
async def x_fitness_agent_kb(req: Request):
    """Expose agent knowledge domains for the App UI."""
    u = _user(req)
    if not u:
        return _unauth()
    return JSONResponse({
        "ok": True,
        "domains": fitness_agent.get_domain_catalog()
    })


@app.get("/api/v2/agent/background/items")
async def x_fitness_agent_background_items(req: Request, status: str = "pending", limit: int = 20):
    """List Fitness Agent background reminders/reports for the current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        items = fitness_agent.list_background_items(c, u["user_id"], status=status, limit=limit)
        return JSONResponse({"ok": True, "items": items})
    finally:
        c.close()


@app.post("/api/v2/agent/background/run")
async def x_fitness_agent_background_run(req: Request):
    """Manually trigger conservative Agent background checks for this user.

    Intended for App refresh/debug and tests. Production scheduling can call the
    same fitness_agent.run_background_checks() helper. Jobs only create inbox
    items and never write official training plans or body data.
    """
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    job = (body.get("job") or "daily_checkin").strip()
    c = _db()
    try:
        res = fitness_agent.run_background_checks(c, u["user_id"], job=job)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    finally:
        c.close()


@app.post("/api/v2/agent/background/items/{item_id}/read")
async def x_fitness_agent_background_read(item_id: str, req: Request):
    """Mark a background reminder/report as read."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        ok = fitness_agent.mark_background_item_read(c, u["user_id"], item_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "item not found"}, status_code=404)
        return JSONResponse({"ok": True, "item_id": item_id, "status": "read"})
    finally:
        c.close()


@app.post("/api/v2/agent/background/items/{item_id}/dismiss")
async def x_fitness_agent_background_dismiss(item_id: str, req: Request):
    """Dismiss a background reminder/report without deleting it."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        ok = fitness_agent.dismiss_background_item(c, u["user_id"], item_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "item not found"}, status_code=404)
        return JSONResponse({"ok": True, "item_id": item_id, "status": "dismissed"})
    finally:
        c.close()


@app.get("/api/v2/agent/health")
async def x_fitness_agent_health(req: Request, window_sec: int = 3600):
    """Agent production-health snapshot: provider cooldowns and recent run stats."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        window = max(60, min(int(window_sec or 3600), 86400))
        return JSONResponse({
            "ok": True,
            "providers": fitness_agent.list_provider_health(c),
            "recent": fitness_agent.recent_agent_stats(c, u["user_id"], window_sec=window),
        })
    finally:
        c.close()


@app.get("/api/v2/agent/runs")
async def x_fitness_agent_runs(req: Request, limit: int = 20):
    """List recent durable Agent runs for the current user."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        return JSONResponse({"ok": True, "runs": fitness_agent.list_runs(c, u["user_id"], limit=limit)})
    finally:
        c.close()


@app.get("/api/v2/agent/runs/{run_id}")
async def x_fitness_agent_run_detail(run_id: str, req: Request):
    """Return one durable Agent run with trace/todos/status."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        run = fitness_agent.get_run(c, u["user_id"], run_id)
        if not run:
            return JSONResponse({"ok": False, "error": "run not found"}, status_code=404)
        return JSONResponse({"ok": True, "run": run})
    finally:
        c.close()


# ============================================================
# Device Binding
# ============================================================
@app.post("/api/v2/devices/bind")
async def x_dev_bind(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    name = (body.get("name") or "").strip() or "设备"
    if not device_id:
        return JSONResponse({"ok": False, "message": "device_id required"}, status_code=400)
    token = secrets.token_hex(16)
    c = _db()
    try:
        # 1. 设备主表 (如果不存在则插)
        c.execute(
            "INSERT INTO devices (device_id, name, status, user_id) VALUES (?, ?, 'bound', ?) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, user_id=excluded.user_id",
            (device_id, name, u["user_id"])
        )
        # 2. 绑定表
        c.execute(
            "INSERT INTO device_user_binding (device_id, user_id, token, active) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(device_id, user_id) DO UPDATE SET token=excluded.token, active=1, last_used_at=julianday('now')",
            (device_id, u["user_id"], token)
        )
        c.commit()
        return JSONResponse({"ok": True, "device_id": device_id, "token": token, "message": "bound"})
    finally:
        c.close()


@app.get("/api/v2/devices/bindings")
async def x_dev_bindings(req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        rows = c.execute(
            "SELECT device_id, bound_at, last_used_at, active "
            "FROM device_user_binding WHERE user_id=? ORDER BY bound_at DESC",
            (u["user_id"],)
        ).fetchall()
        return JSONResponse({"ok": True, "bindings": [dict(r) for r in rows]})
    finally:
        c.close()


@app.delete("/api/v2/devices/bind/{device_id}")
async def x_dev_unbind(device_id: str, req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        cur = c.execute(
            "UPDATE device_user_binding SET active=0 WHERE device_id=? AND user_id=?",
            (device_id, u["user_id"])
        )
        c.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "message": "binding not found"}, status_code=404)
        return JSONResponse({"ok": True, "message": "unbound"})
    finally:
        c.close()


# ============================================================
# Vision Infer (no-summary 简版, 复用 PoseEngine)
# ============================================================
@app.post("/api/v2/vision/infer")
async def x_vision_infer(req: Request):
    """简版推理: 只返 keypoints + 角度 + form_score, 不含 summary/paused 等控制字段."""
    u = _user(req)
    if not u:
        return _unauth()
    t0 = time.time()
    body = await req.json()
    image_b64 = body.get("image") or body.get("image_base64") or ""
    try:
        from main_v2_routes import get_pose_engine
        eng = get_pose_engine()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"engine load: {e}"}, status_code=500)

    detected = False
    landmarks = []
    angles = {}
    form_score = None
    exercise_pred = None
    try:
        if image_b64 and eng is not None:
            import numpy as np, cv2
            raw = base64.b64decode(image_b64.split(",")[-1])
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                res = eng.infer_from_image(img)
                detected = res.get("detected", False)
                landmarks = res.get("landmarks") or []
                angles = res.get("angles") or {}
                form_score = res.get("form_score")
                exercise_pred = res.get("exercise")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({
        "ok": True,
        "detected": detected,
        "landmarks": landmarks,
        "angles": angles,
        "exercise_type": exercise_pred,
        "form_score": form_score,
        "inference_ms": int((time.time() - t0) * 1000),
        "user_id": u["user_id"],
    })


log.info("main_v2_extra loaded: +12 routes (plans/stats/sessions/metrics/exercise/bind/vision)")


# ============================================================
# Fix: /api/v2/devices - 覆盖 main_v2_routes 里报 500 的版本
# ============================================================
from fastapi import Request as _Req
from fastapi.responses import JSONResponse as _JR

async def x_devices_list_fixed(req: _Req):
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        rows = c.execute(
            "SELECT d.device_id, d.name, d.device_type, d.user_id, "
            "       CASE WHEN d.status = 'online' OR d.status = 'bound' THEN 1 ELSE 0 END AS is_active, "
            "       d.last_seen, "
            "       COALESCE(b.token, '') AS token "
            "FROM devices d "
            "LEFT JOIN device_user_binding b ON b.device_id = d.device_id AND b.user_id = d.user_id "
            "WHERE d.user_id = ? "
            "ORDER BY d.last_seen DESC NULLS LAST",
            (u["user_id"],)
        ).fetchall()
        return _JR({"ok": True, "devices": [dict(r) for r in rows]})
    finally:
        c.close()

# 覆盖路由 (FastAPI 没有原生 replace, 直接清掉再加)
_routes_to_remove = []
for r in list(app.router.routes):
    if hasattr(r, 'path') and r.path == "/api/v2/devices" and "GET" in getattr(r, 'methods', set()):
        _routes_to_remove.append(r)
for r in _routes_to_remove:
    app.router.routes.remove(r)
app.add_api_route("/api/v2/devices", x_devices_list_fixed, methods=["GET"])
log.info(f"replaced /api/v2/devices: removed {len(_routes_to_remove)} old route(s)")


# ============================================================
# Workout Summary (for post-training dialog)
# ============================================================
@app.post("/api/v2/workout/summary")
async def x_workout_summary(req: Request):
    """训练结束总结. POST {device_id, exercise, reps, duration_s, avg_form_score?}
    返回 {ok, totals, coach_remark, badges, kcal_est}."""
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    exercise = (body.get("exercise") or body.get("exercise_type") or "unknown").strip()
    reps = int(body.get("reps") or 0)
    duration_s = float(body.get("duration_s") or 0)
    avg_form = body.get("avg_form_score")
    try:
        avg_form = float(avg_form) if avg_form is not None else None
    except Exception:
        avg_form = None

    # kcal: 粗估 MET 公式, 体重默认 60kg
    met_table = {
        "squat": 5.0, "push_up": 8.0, "lunge": 4.5, "plank": 3.5,
        "bicep_curl": 3.5, "shoulder_press": 4.0, "jumping_jack": 8.0,
    }
    met = met_table.get(exercise, 4.0)
    weight = 60.0
    kcal = round(met * weight * (duration_s / 3600.0), 1)

    # 教练点评: 优先 LLM (workout_coach_remark 内部已含规则 fallback)
    try:
        import ai_planner
        remark = ai_planner.workout_coach_remark(exercise, reps, duration_s, avg_form)
    except Exception as e:
        log.warning(f"workout_coach_remark fallback: {e}")
        remark = None
    if not remark:
        if avg_form is not None and avg_form >= 85:
            remark = f"姿势漂亮! {exercise} {reps} 个一气呵成, 平均评分 {avg_form:.0f} 分, 保持这个节奏."
        elif avg_form is not None and avg_form >= 70:
            remark = f"完成了 {reps} 个 {exercise}, 评分 {avg_form:.0f} 分还有进步空间, 注意核心收紧."
        elif avg_form is not None:
            remark = f"{reps} 个完成, 但 form 评分只有 {avg_form:.0f}, 下次放慢节奏盯准动作要点."
        else:
            remark = f"完成了 {reps} 个 {exercise}, 用时 {int(duration_s)} 秒, 继续保持."

    # 徽章
    badges = []
    if reps >= 30: badges.append({"name": "30 reps club", "icon": "trophy"})
    if avg_form is not None and avg_form >= 90: badges.append({"name": "Perfect Form", "icon": "star"})
    if duration_s >= 600: badges.append({"name": "10min Warrior", "icon": "fire"})

    # 写入 exercise_log (复用现成表)
    c = _db()
    try:
        c.execute(
            "INSERT INTO exercise_log (user_id, device_id, exercise_type, reps, duration_s, avg_form_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (u["user_id"], device_id, exercise, reps, duration_s, avg_form, int(time.time()))
        )
        c.commit()
    except Exception as e:
        log.warning(f"summary log insert failed: {e}")
    finally:
        c.close()

    return JSONResponse({
        "ok": True,
        "totals": {
            "reps": reps,
            "duration_s": round(duration_s, 1),
            "avg_form_score": avg_form,
            "exercise": exercise,
        },
        "coach_remark": remark,
        "badges": badges,
        "kcal_est": kcal,
    })


# ============================================================
# Calendar Heatmap (Profile page)
# ============================================================
@app.get("/api/v2/stats/calendar")
async def x_stats_calendar(req: Request):
    """返回最近 N 天的训练量, 用于日历热图. 默认 84 天 (12 周)."""
    u = _user(req)
    if not u:
        return _unauth()
    days = int(req.query_params.get("days") or 84)
    cutoff = int(time.time()) - days * 86400
    c = _db()
    try:
        rows = c.execute(
            """SELECT date(created_at, 'unixepoch', 'localtime') as d,
                       SUM(reps) as reps, SUM(duration_s) as dur, COUNT(*) as sessions
                FROM exercise_log
                WHERE user_id=? AND created_at>=?
                GROUP BY d ORDER BY d""",
            (u["user_id"], cutoff)
        ).fetchall()
        return JSONResponse({"days": [dict(r) for r in rows]})
    finally:
        c.close()


# ============================================================
# WS Push (Admin/Test only) - F-07 完整实现
# ============================================================
@app.post("/api/v2/ws/push")
async def x_ws_push(req: Request):
    """管理/测试接口: 向指定 WS 频道推送消息. target 格式 session:xxx 或 user:NN.
    Body: {target: str, message: object}"""
    u = _user(req)
    if not u:
        return _unauth()
    body = await req.json()
    target = (body.get("target") or "").strip()
    message = body.get("message") or {}
    if not target or ":" not in target:
        return JSONResponse({"ok": False, "error": "invalid target"}, status_code=400)
    kind, val = target.split(":", 1)
    # 复用 main 中的 ws hub. 找不到就降级 noop 成功返回(测试场景).
    delivered = 0
    try:
        import main_v2_routes as mod
        hub = getattr(mod, "_ws_hub", None) or getattr(mod, "ws_hub", None)
        if hub is not None:
            if kind == "session":
                delivered = await hub.broadcast_session(val, message)
            elif kind == "user":
                try: uid = int(val)
                except: uid = -1
                if uid > 0: delivered = await hub.broadcast_user(uid, message)
    except Exception as e:
        log.warning(f"ws_push hub: {e}")
    return JSONResponse({"ok": True, "delivered": delivered, "target": target})


# ============================================================
# Personal Best (PB) - 个人最佳记录
# ============================================================
@app.get("/api/v2/stats/pb")
async def x_stats_pb(req: Request):
    """返回每个 exercise 的最佳成绩 (max reps, max avg_form, longest duration)."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        rows = c.execute(
            """SELECT exercise_type,
                       MAX(reps) as best_reps,
                       MAX(avg_form_score) as best_form,
                       MAX(duration_s) as longest_s,
                       COUNT(*) as total_sessions
                FROM exercise_log
                WHERE user_id=? AND exercise_type IS NOT NULL
                GROUP BY exercise_type
                ORDER BY total_sessions DESC""",
            (u["user_id"],)
        ).fetchall()
        return JSONResponse({"ok": True, "pb": [dict(r) for r in rows]})
    finally:
        c.close()


# ============================================================
# Streak (连续训练天数)
# ============================================================
@app.get("/api/v2/stats/streak")
async def x_stats_streak(req: Request):
    """返回当前连续训练天数 + 历史最长连续天数."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        rows = c.execute(
            """SELECT DISTINCT date(created_at, 'unixepoch', 'localtime') as d
                FROM exercise_log WHERE user_id=? ORDER BY d DESC LIMIT 365""",
            (u["user_id"],)
        ).fetchall()
        if not rows:
            return JSONResponse({"ok": True, "current_streak": 0, "longest_streak": 0, "last_active": None})
        from datetime import datetime, timedelta
        days = [datetime.strptime(r["d"], "%Y-%m-%d").date() for r in rows]
        today = datetime.now().date()
        # current streak: 从今天/昨天往回数连续
        current = 0
        expected = today
        for d in days:
            if d == expected:
                current += 1
                expected = expected - timedelta(days=1)
            elif d == expected + timedelta(days=1) and current == 0:
                # 用户今天还没训练, 但昨天有
                continue
            else:
                break
        # longest streak: 全部历史
        longest = 0
        run = 1
        for i in range(1, len(days)):
            if (days[i-1] - days[i]).days == 1:
                run += 1; longest = max(longest, run)
            else:
                run = 1
        longest = max(longest, 1)
        return JSONResponse({
            "ok": True,
            "current_streak": current,
            "longest_streak": longest,
            "last_active": str(days[0]) if days else None,
        })
    finally:
        c.close()


# ============================================================
# Achievements (成就系统)
# ============================================================
@app.get("/api/v2/achievements")
async def x_achievements(req: Request):
    """返回用户已解锁/未解锁的成就列表."""
    u = _user(req)
    if not u:
        return _unauth()
    c = _db()
    try:
        # 统计原始数据
        total_reps = (c.execute("SELECT SUM(reps) FROM exercise_log WHERE user_id=?", (u["user_id"],)).fetchone()[0] or 0)
        total_sessions = (c.execute("SELECT COUNT(*) FROM exercise_log WHERE user_id=?", (u["user_id"],)).fetchone()[0] or 0)
        total_dur = (c.execute("SELECT SUM(duration_s) FROM exercise_log WHERE user_id=?", (u["user_id"],)).fetchone()[0] or 0)
        max_reps_single = (c.execute("SELECT MAX(reps) FROM exercise_log WHERE user_id=?", (u["user_id"],)).fetchone()[0] or 0)
        unique_ex = (c.execute("SELECT COUNT(DISTINCT exercise_type) FROM exercise_log WHERE user_id=? AND exercise_type IS NOT NULL", (u["user_id"],)).fetchone()[0] or 0)
    finally:
        c.close()

    catalog = [
        ("first_workout", "First Workout", "Complete your first workout", "rocket", total_sessions >= 1),
        ("reps_100", "Century", "Accumulate 100 reps total", "100", total_reps >= 100),
        ("reps_1000", "Iron Will", "Accumulate 1000 reps total", "iron", total_reps >= 1000),
        ("reps_10000", "Beast Mode", "Accumulate 10000 reps total", "beast", total_reps >= 10000),
        ("session_50", "Half Century", "Finish 50 training sessions", "trophy", total_sessions >= 50),
        ("single_30", "30 in a Row", "Hit 30 reps in a single workout", "fire", max_reps_single >= 30),
        ("single_100", "Centurion", "Hit 100 reps in a single workout", "crown", max_reps_single >= 100),
        ("all_seven", "All-Rounder", "Try all 7 exercise types", "star", unique_ex >= 7),
        ("hour_total", "Hour Warrior", "Train for 1 hour total", "clock", total_dur >= 3600),
    ]
    return JSONResponse({
        "ok": True,
        "achievements": [
            {"id": k, "name": n, "desc": d, "icon": ic, "unlocked": bool(ok)}
            for (k, n, d, ic, ok) in catalog
        ],
        "stats": {
            "total_reps": total_reps,
            "total_sessions": total_sessions,
            "total_duration_s": total_dur,
            "max_single_reps": max_reps_single,
            "unique_exercises": unique_ex,
        }
    })



# ============================================================
# Export CSV (用户数据导出)
# ============================================================
@app.get("/api/v2/export/csv")
async def x_export_csv(req: Request):
    """导出 exercise_log 所有数据为 CSV."""
    u = _user(req)
    if not u:
        return _unauth()
    days = int(req.query_params.get("days") or 365)
    cutoff = int(time.time()) - days * 86400
    c = _db()
    try:
        rows = c.execute(
            """SELECT log_id, exercise_type, reps, duration_s, avg_form_score,
                       device_id, created_at,
                       datetime(created_at, 'unixepoch', 'localtime') as ts_local
                FROM exercise_log WHERE user_id=? AND created_at>=?
                ORDER BY created_at DESC""",
            (u["user_id"], cutoff)
        ).fetchall()
    finally:
        c.close()

    import io, csv
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["log_id","exercise_type","reps","duration_s","avg_form_score","device_id","created_at","timestamp"])
    for r in rows:
        w.writerow([r["log_id"], r["exercise_type"] or "", r["reps"] or 0,
                    r["duration_s"] or 0, r["avg_form_score"] or "",
                    r["device_id"] or "", r["created_at"], r["ts_local"]])

    from fastapi.responses import Response
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=workout_{u['user_id']}_{int(time.time())}.csv"}
    )



# ======================== Session-level AI Coach endpoint ========================


@app.post("/api/v2/training/session/{session_id}/ai_coach")
async def x_training_session_ai_coach(session_id: str, req: Request):
    """Session-level two-stage AI coach: analyze ALL reps in a set at once.

    Stage 1: vision model on each rep's key frame.
    Stage 2: text LLM synthesizes a comprehensive report with per-rep notes
    (e.g. "rep 3: squat too deep"), group trends, and personalized guidance.

    Body (optional): {} (no parameters yet, uses default 1 frame per rep).

    Returns structured JSON with overall_assessment, rep_by_rep_notes, guidance.
    """
    u = _user(req)
    if not u:
        return _unauth()
    try:
        body = await req.json()
    except Exception:
        body = {}
    from fitness_agent.toolkit.vision_tools import analyze_session_two_stage_tool
    c = _db()
    args = {"session_id": session_id}
    if body.get("frames") is not None:
        args["frames"] = body.get("frames")
    try:
        result = analyze_session_two_stage_tool(c, u["user_id"], args)
    finally:
        c.close()

    # Slim down response for the App
    stage_reason = result.get("stage_reason") or {}
    analysis = stage_reason.get("analysis") or {}
    slim = {
        "ok": bool(result.get("ok")),
        "session_id": session_id,
        "exercise": result.get("exercise"),
        "rep_count": result.get("rep_count") or 0,
        "reps_count": result.get("reps_count") or 0,
        "overall_assessment": analysis.get("overall_assessment"),
        "rep_by_rep_notes": analysis.get("rep_by_rep_notes") or [],
        "guidance": analysis.get("guidance"),
        "data_gaps": analysis.get("data_gaps") or [],
        "confidence": analysis.get("confidence"),
        "stage2_provider": stage_reason.get("provider"),
        "stage2_model": stage_reason.get("model"),
        "stage1_results": [
            {"rep_index": s.get("rep_index"), "ok": s.get("ok"), "error": s.get("error"),
             "provider": s.get("provider"), "model": s.get("model")}
            for s in (result.get("stage1_results") or [])
        ],
        "stage1_ok_count": sum(1 for s in (result.get("stage1_results") or []) if s.get("ok")),
        "stage1_total": len(result.get("stage1_results") or []),
    }

    # Auto-save report to ai_coach_reports table (best-effort)
    try:
        _ensure_ai_coach_reports_table()
        report_id = f"rpt_{u['user_id']}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
        c2 = _db()
        try:
            c2.execute(
                """INSERT INTO ai_coach_reports
                   (report_id, user_id, session_id, exercise, rep_count, frames_per_rep,
                    overall_score, performance_rating, stage1_ok_count, stage1_total,
                    report_json, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id, u["user_id"], session_id,
                    slim.get("exercise"), slim.get("rep_count") or 0,
                    int(body.get("frames") or 1),
                    (slim.get("overall_assessment") or {}).get("overall_score"),
                    (slim.get("overall_assessment") or {}).get("performance_rating"),
                    slim.get("stage1_ok_count") or 0,
                    slim.get("stage1_total") or 0,
                    json.dumps(slim, ensure_ascii=False),
                    None,
                    time.time(),
                ),
            )
            c2.commit()
            slim["report_id"] = report_id
            slim["saved"] = True
        finally:
            c2.close()
    except Exception as e:
        log.warning("failed to save ai_coach_report: %s", e)
        slim["saved"] = False

    return slim


# ---------------------------------------------------------------------------
# AI Coach Reports 报告档案
# ---------------------------------------------------------------------------

_AI_COACH_REPORTS_READY = False


def _ensure_ai_coach_reports_table():
    global _AI_COACH_REPORTS_READY
    if _AI_COACH_REPORTS_READY:
        return
    c = _db()
    try:
        c.execute(
            """CREATE TABLE IF NOT EXISTS ai_coach_reports (
                report_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                session_id TEXT,
                exercise TEXT,
                rep_count INTEGER,
                frames_per_rep INTEGER,
                overall_score REAL,
                performance_rating TEXT,
                stage1_ok_count INTEGER,
                stage1_total INTEGER,
                report_json TEXT,
                note TEXT,
                created_at REAL NOT NULL
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_coach_reports_user ON ai_coach_reports(user_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_coach_reports_session ON ai_coach_reports(session_id)")
        c.commit()
        _AI_COACH_REPORTS_READY = True
    finally:
        c.close()


@app.get("/api/v2/ai_coach/reports")
async def x_ai_coach_reports_list(req: Request):
    """List saved AI coach reports for the current user (newest first).

    Query: ?session_id=... to filter by session, ?limit=50 default.
    Returns compact list; full report_json fetched via /reports/{report_id}.
    """
    u = _user(req)
    if not u:
        return _unauth()
    _ensure_ai_coach_reports_table()
    session_id = req.query_params.get("session_id")
    try:
        limit = min(200, max(1, int(req.query_params.get("limit") or 50)))
    except Exception:
        limit = 50
    c = _db()
    try:
        if session_id:
            rows = c.execute(
                """SELECT report_id, session_id, exercise, rep_count, frames_per_rep,
                          overall_score, performance_rating, stage1_ok_count, stage1_total,
                          note, created_at
                   FROM ai_coach_reports
                   WHERE user_id=? AND session_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (u["user_id"], session_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT report_id, session_id, exercise, rep_count, frames_per_rep,
                          overall_score, performance_rating, stage1_ok_count, stage1_total,
                          note, created_at
                   FROM ai_coach_reports
                   WHERE user_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (u["user_id"], limit),
            ).fetchall()
    finally:
        c.close()
    return {
        "ok": True,
        "reports": [
            {
                "report_id": r["report_id"],
                "session_id": r["session_id"],
                "exercise": r["exercise"],
                "rep_count": r["rep_count"],
                "frames_per_rep": r["frames_per_rep"],
                "overall_score": r["overall_score"],
                "performance_rating": r["performance_rating"],
                "stage1_ok_count": r["stage1_ok_count"],
                "stage1_total": r["stage1_total"],
                "note": r["note"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@app.get("/api/v2/ai_coach/reports/{report_id}")
async def x_ai_coach_report_get(report_id: str, req: Request):
    """Get a single AI coach report including full report_json."""
    u = _user(req)
    if not u:
        return _unauth()
    _ensure_ai_coach_reports_table()
    c = _db()
    try:
        r = c.execute(
            "SELECT * FROM ai_coach_reports WHERE report_id=? AND user_id=?",
            (report_id, u["user_id"]),
        ).fetchone()
    finally:
        c.close()
    if not r:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        report = json.loads(r["report_json"]) if r["report_json"] else {}
    except Exception:
        report = {}
    return {
        "ok": True,
        "report_id": r["report_id"],
        "session_id": r["session_id"],
        "exercise": r["exercise"],
        "rep_count": r["rep_count"],
        "frames_per_rep": r["frames_per_rep"],
        "overall_score": r["overall_score"],
        "performance_rating": r["performance_rating"],
        "stage1_ok_count": r["stage1_ok_count"],
        "stage1_total": r["stage1_total"],
        "note": r["note"],
        "created_at": r["created_at"],
        "report": report,
    }


@app.patch("/api/v2/ai_coach/reports/{report_id}")
async def x_ai_coach_report_update(report_id: str, req: Request):
    """Update note on a report."""
    u = _user(req)
    if not u:
        return _unauth()
    _ensure_ai_coach_reports_table()
    try:
        body = await req.json()
    except Exception:
        body = {}
    note = body.get("note")
    c = _db()
    try:
        r = c.execute(
            "SELECT report_id FROM ai_coach_reports WHERE report_id=? AND user_id=?",
            (report_id, u["user_id"]),
        ).fetchone()
        if not r:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        c.execute(
            "UPDATE ai_coach_reports SET note=? WHERE report_id=?",
            (note, report_id),
        )
        c.commit()
    finally:
        c.close()
    return {"ok": True, "report_id": report_id, "note": note}


@app.delete("/api/v2/ai_coach/reports/{report_id}")
async def x_ai_coach_report_delete(report_id: str, req: Request):
    u = _user(req)
    if not u:
        return _unauth()
    _ensure_ai_coach_reports_table()
    c = _db()
    try:
        r = c.execute(
            "SELECT report_id FROM ai_coach_reports WHERE report_id=? AND user_id=?",
            (report_id, u["user_id"]),
        ).fetchone()
        if not r:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        c.execute("DELETE FROM ai_coach_reports WHERE report_id=?", (report_id,))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "report_id": report_id, "deleted": True}
