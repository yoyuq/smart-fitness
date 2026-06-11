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
import os, json, time, uuid, secrets, base64, logging
from typing import Optional, Dict, Any
from fastapi import Request
from fastapi.responses import JSONResponse

import auth
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
    exercises = body.get("exercises") or "[]"
    if not isinstance(exercises, str):
        exercises = json.dumps(exercises, ensure_ascii=False)
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
        rows = c.execute(
            "SELECT id, exercise_type, reps, sets, duration_seconds, avg_form_score, performed_at "
            "FROM user_exercise_log WHERE user_id=? AND performed_at>=? "
            "ORDER BY performed_at DESC LIMIT ?",
            (u["user_id"], since, limit)
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
            "       COALESCE(SUM(reps),0)              AS total_reps, "
            "       COUNT(DISTINCT COALESCE(session_id, id)) AS sessions, "
            "       COALESCE(SUM(duration_seconds),0)  AS total_seconds, "
            "       AVG(avg_form_score)                AS avg_form "
            "FROM user_exercise_log WHERE user_id=? AND performed_at>=? "
            "GROUP BY exercise_type ORDER BY total_reps DESC",
            (u["user_id"], since)
        ).fetchall()
        return JSONResponse({"ok": True, "days": days, "by_type": [dict(r) for r in rows]})
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
