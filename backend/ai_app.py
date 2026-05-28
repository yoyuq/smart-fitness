"""ai_app.py - 独立 AI Planner FastAPI 服务 (端口 8081)

独立运行, 不依赖 main.py 当前的破损状态.
共用 fitness.db 数据库 + auth 模块.

启动:
    python -m uvicorn ai_app:app --host 0.0.0.0 --port 8081
"""
import os, sqlite3, time, logging
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import json, time
import ai_planner
try:
    import auth
except Exception as e:
    print(f"auth import warning: {e}")
    auth = None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_app")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "fitness.db")

app = FastAPI(title="Smart Fitness AI Planner", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _require_user_id(req: Request) -> Optional[int]:
    if not auth:
        return None
    h = req.headers.get("Authorization", "") or req.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        return None
    tok = h.split(" ", 1)[1].strip()
    try:
        from auth import verify_token
        payload = verify_token(tok)
        if not payload:
            return None
        uid = payload.get("user_id") or payload.get("sub")
        return int(uid) if uid is not None else None
    except Exception as e:
        log.warning(f"auth fail: {e}")
        return None


@app.get("/")
def root():
    return {"ok": True, "service": "ai_planner", "ai_available": ai_planner.is_available()}


@app.get("/api/ai/health")
def health():
    s = ai_planner.provider_status()
    return {
        "ok": True,
        "providers": s,
        "deepseek_key_set": s["deepseek_key_set"],
        "volc_key_set": s["volc_key_set"],
        "model": s["volc_model"] if s["volc_key_set"] else s["deepseek_model"],
        "db_exists": os.path.exists(DB_PATH),
    }


@app.post("/api/ai/daily_summary")
async def daily_summary(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not ai_planner.is_available():
        return JSONResponse({"ok": False, "error": "DEEPSEEK_API_KEY not set"}, status_code=503)
    conn = get_db()
    try:
        return JSONResponse(ai_planner.daily_summary(conn, uid))
    finally:
        conn.close()


@app.post("/api/ai/weekly_report")
async def weekly_report(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    conn = get_db()
    try:
        return JSONResponse(ai_planner.weekly_report(conn, uid))
    finally:
        conn.close()


@app.post("/api/ai/plan_generate")
async def plan_generate(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    goal = (body.get("goal") or "").strip()
    weeks = int(body.get("weeks") or 4)
    if not goal:
        return JSONResponse({"ok": False, "error": "goal required"}, status_code=400)
    weeks = max(1, min(weeks, 12))
    conn = get_db()
    try:
        return JSONResponse(ai_planner.generate_plan(conn, uid, goal, weeks))
    finally:
        conn.close()


@app.post("/api/ai/chat")
async def chat(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    msg = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not msg:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
    conn = get_db()
    try:
        return JSONResponse(ai_planner.chat(conn, uid, msg, history=history))
    finally:
        conn.close()


# ====== Simulator 上传 + 自动写入运动日志 ======

# 简单的会话状态: device_id -> {user_id, exercise, session_id, frames, scores, started_at}
_sim_sessions = {}


@app.post("/api/sim/frame")
async def sim_frame(req: Request):
    """接收模拟器推理结果, 累计训练数据.

    body: {source, exercise, confidence, form_score, angles, ts, device_id?, user_id?}
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    exercise = body.get("exercise") or "unknown"
    score = body.get("form_score")
    confidence = body.get("confidence") or 0
    device_id = body.get("device_id") or "sim-001"
    user_id = body.get("user_id") or 31  # 默认 hjl
    ts = body.get("ts") or time.time()

    # 内存累加
    key = f"{user_id}:{device_id}:{exercise}"
    s = _sim_sessions.get(key)
    if s is None:
        s = {"user_id": user_id, "exercise": exercise, "started_at": ts, "frames": 0, "scores": []}
        _sim_sessions[key] = s
    s["frames"] += 1
    if isinstance(score, (int, float)):
        s["scores"].append(float(score))
    s["last_ts"] = ts

    # 每 30 帧 flush 一次到 DB (作为一组完成)
    flushed = False
    if s["frames"] % 30 == 0 and s["scores"]:
        await _flush_sim_session(key, s)
        flushed = True
    return JSONResponse({"ok": True, "frames": s["frames"], "avg_score": (sum(s["scores"])/len(s["scores"]) if s["scores"] else 0), "flushed": flushed})


async def _flush_sim_session(key, s):
    """把累计数据写 user_exercise_log + 增量 daily_summary."""
    import datetime
    conn = get_db()
    try:
        cur = conn.cursor()
        # 一帧 = 一秒, 30 帧 = ~3s ; 假设是 1 个 rep
        # 更稳: 直接估算 reps = frames / 8 (大概 0.8s/rep)
        reps = max(1, s["frames"] // 8)
        avg_score = sum(s["scores"]) / len(s["scores"])
        dur = max(1, int(s["last_ts"] - s["started_at"]))
        kcal = round(reps * 0.4, 1)  # 粗估
        cur.execute(
            """INSERT INTO user_exercise_log
               (user_id, session_id, exercise_type, reps, sets, duration_seconds, avg_form_score, calories_kcal, performed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["user_id"], f"sim_{int(s['started_at'])}", s["exercise"], reps, 1, dur, round(avg_score, 1), kcal, int(s["last_ts"]))
        )
        # daily_summary upsert
        today = datetime.date.fromtimestamp(s["last_ts"]).isoformat()
        existing = cur.execute("SELECT id, total_reps, total_sets, total_duration_sec, avg_form_score, total_calories, exercises_done, sessions_count FROM daily_summary WHERE user_id=? AND date=?", (s["user_id"], today)).fetchone()
        if existing:
            new_reps = existing[1] + reps
            new_sets = existing[2] + 1
            new_dur = existing[3] + dur
            new_avg = round((existing[4] * existing[1] + avg_score * reps) / max(1, new_reps), 1)
            new_kcal = round((existing[5] or 0) + kcal, 1)
            try:
                ex_done = json.loads(existing[6] or "{}")
            except Exception:
                ex_done = {}
            ex_done[s["exercise"]] = ex_done.get(s["exercise"], 0) + reps
            cur.execute("UPDATE daily_summary SET total_reps=?, total_sets=?, total_duration_sec=?, avg_form_score=?, total_calories=?, exercises_done=?, sessions_count=?, updated_at=? WHERE id=?",
                        (new_reps, new_sets, new_dur, new_avg, new_kcal, json.dumps(ex_done), existing[7] + 1, int(time.time()), existing[0]))
        else:
            ex_done = {s["exercise"]: reps}
            cur.execute("INSERT INTO daily_summary (user_id, date, total_reps, total_sets, total_duration_sec, avg_form_score, total_calories, exercises_done, sessions_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (s["user_id"], today, reps, 1, dur, round(avg_score, 1), kcal, json.dumps(ex_done), 1, int(time.time())))
        conn.commit()
        # 重置 sub-session
        s["frames"] = 0
        s["scores"] = []
        s["started_at"] = s["last_ts"]
    finally:
        conn.close()


@app.get("/api/sim/state")
def sim_state():
    return JSONResponse({"sessions": [{"key": k, "frames": v["frames"], "avg_score": (sum(v["scores"])/len(v["scores"]) if v["scores"] else 0)} for k, v in _sim_sessions.items()]})


@app.post("/api/ai/meal_suggestion")
async def meal_suggestion(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    conn = get_db()
    try:
        return JSONResponse(ai_planner.meal_suggestion(conn, uid))
    finally:
        conn.close()
