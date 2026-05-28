"""main_v2_routes.py - Sprint 1/2 Routes.

把所有 v2 路由放在独立模块, 不污染干净的 main.py.
在 main.py 末尾加: `import main_v2_routes` 即可挂载.

提供:
  /api/auth/register|login|profile (+ v2 别名)
  /api/v2/devices/register|list|by_token
  /api/v2/metrics/body POST|GET
  /api/v2/training/start|stop|active
  /api/v2/vision/infer/full (含 paused/exercise_hint/next_interval_ms)
  /ws/coach/{user_id} (按 user 订阅广播)
  /api/v2/ai/{daily_summary,weekly_report,plan_generate,chat,meal_suggestion}
"""
import os, json, time, base64, io, asyncio, logging
from typing import Optional, Dict, Any
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import auth
import ai_planner
from main import app  # 复用主 FastAPI 实例

log = logging.getLogger("v2_routes")

# 可选: pose engine (ML 推理), 没装也不阻断启动
try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml_pose"))
    from pose_engine import PoseEngine
    _pose_engine: Optional[PoseEngine] = None
    def get_pose_engine() -> Optional[PoseEngine]:
        global _pose_engine
        if _pose_engine is None:
            try:
                _pose_engine = PoseEngine()
                log.info("pose_engine ready")
            except Exception as e:
                log.warning(f"pose_engine init failed: {e}")
                _pose_engine = None
        return _pose_engine
except Exception as e:
    log.warning(f"pose_engine not available: {e}")
    def get_pose_engine():
        return None

# DB 路径
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "fitness.db")


def get_db():
    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _require_user(req: Request) -> Optional[Dict]:
    auth_h = req.headers.get("Authorization") or req.headers.get("authorization")
    return auth.require_auth(auth_h)


# ============================================================
# A-02 Auth: register / login / profile (v1 + v2)
# ============================================================
@app.post("/api/auth/register")
async def auth_register(req: Request):
    body = await req.json()
    return JSONResponse(auth.register(
        username=body.get("username", "").strip(),
        password=body.get("password", ""),
        display_name=body.get("display_name", "")
    ))


@app.post("/api/auth/login")
async def auth_login(req: Request):
    body = await req.json()
    return JSONResponse(auth.login(
        username=body.get("username", "").strip(),
        password=body.get("password", "")
    ))


@app.get("/api/auth/profile")
async def auth_profile(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    prof = auth.get_user_profile(user["user_id"])
    return JSONResponse({"ok": True, "user": prof})


# v2 别名
app.add_api_route("/api/v2/auth/register", auth_register, methods=["POST"])
app.add_api_route("/api/v2/auth/login",    auth_login,    methods=["POST"])
app.add_api_route("/api/v2/auth/profile",  auth_profile,  methods=["GET"])


# ============================================================
# A-03 v2 Devices: register / list / by_token
# ============================================================
@app.post("/api/v2/devices/register")
async def v2_dev_register(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    return JSONResponse(auth.register_device(
        device_id=body.get("device_id", "").strip(),
        device_type=body.get("device_type", "phone"),
        name=body.get("name", ""),
        user_id=user["user_id"],
    ))


@app.get("/api/v2/devices")
async def v2_dev_list(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT device_id, device_type, name, token, registered_at FROM devices WHERE user_id = ? ORDER BY registered_at DESC",
            (user["user_id"],)
        ).fetchall()
        return JSONResponse({"ok": True, "devices": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.get("/api/v2/devices/by_token/{token}")
def v2_dev_by_token(token: str):
    """ESP32 用 token 反查 user_id + device_id."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT device_id, user_id, name FROM devices WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "token not found"}, status_code=404)
        return JSONResponse({"ok": True, **dict(row)})
    finally:
        conn.close()


# ============================================================
# A-?? Body metrics
# ============================================================
@app.post("/api/v2/metrics/body")
async def v2_body_post(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO user_body_metrics (user_id, weight_kg, height_cm, body_fat_pct, notes, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (user["user_id"], body.get("weight_kg"), body.get("height_cm"),
             body.get("body_fat_pct"), body.get("notes", ""), int(time.time()))
        )
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()


@app.get("/api/v2/metrics/body")
async def v2_body_list(req: Request, limit: int = 30):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT weight_kg, height_cm, body_fat_pct, notes, timestamp FROM user_body_metrics WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user["user_id"], limit)
        ).fetchall()
        return JSONResponse({"ok": True, "metrics": [dict(r) for r in rows]})
    finally:
        conn.close()


# ============================================================
# B-?? Training control: start / stop / active
# ============================================================
# device_id -> {user_id, exercise, session_id, started_at}
active_trainings: Dict[str, Dict[str, Any]] = {}


@app.post("/api/v2/training/start")
async def v2_train_start(req: Request):
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    # user_id 优先从 JWT 取, fallback 到 body
    user_id = body.get("user_id")
    if not user_id:
        try:
            import auth
            u = auth.require_auth(req.headers.get("Authorization") or req.headers.get("authorization"))
            if u: user_id = u.get("user_id")
        except Exception:
            pass
    # 兼容 APP 字段名: exercise / exercise_type
    exercise = (body.get("exercise") or body.get("exercise_type") or "squat").strip() or "squat"
    if not device_id or not user_id:
        return JSONResponse({"ok": False, "error": "device_id and user_id required"}, status_code=400)
    session_id = f"sess_{user_id}_{int(time.time())}"
    active_trainings[device_id] = {
        "user_id": int(user_id),
        "exercise": exercise,
        "session_id": session_id,
        "started_at": time.time(),
    }
    log.info(f"training start device={device_id} user={user_id} exercise={exercise} sid={session_id}")
    return JSONResponse({"ok": True, "session_id": session_id, "exercise": exercise})


@app.post("/api/v2/training/stop")
async def v2_train_stop(req: Request):
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    sess = active_trainings.pop(device_id, None)
    log.info(f"training stop device={device_id} had={bool(sess)}")
    return JSONResponse({"ok": True, "stopped": bool(sess), "session_id": sess["session_id"] if sess else None})


@app.get("/api/v2/training/active")
async def v2_train_active(req: Request, device_id: Optional[str] = None):
    if device_id:
        return JSONResponse({"ok": True, "active": active_trainings.get(device_id)})
    return JSONResponse({"ok": True, "all": active_trainings})


# ============================================================
# B-?? Vision inference (full) - 含 paused/exercise_hint/next_interval_ms
# ============================================================
@app.post("/api/v2/vision/infer/full")
async def v2_vision_infer_full(req: Request):
    """ESP32 POST 一张 JPEG (base64), 返回 pose + 训练控制字段."""
    t0 = time.time()
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    image_b64 = body.get("image_base64") or body.get("image") or ""

    # 训练态控制
    sess = active_trainings.get(device_id)
    paused = sess is None
    next_interval_ms = 5000 if paused else 2500
    exercise_hint = sess["exercise"] if sess else None
    user_id = sess["user_id"] if sess else None
    session_id = sess["session_id"] if sess else None

    # 推理
    detected = False
    exercise_pred = None
    confidence = 0.0
    form_score = None
    feedback = ""
    angles = {}
    landmarks_out = []
    img_for_broadcast = None
    try:
        if image_b64:
            import numpy as np, cv2
            raw = base64.b64decode(image_b64.split(",")[-1])
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            img_for_broadcast = img
            eng = get_pose_engine()
            if eng is not None and img is not None:
                res = eng.infer_from_image(img)
                detected = res.get("detected", False)
                landmarks_out = res.get("landmarks") or []
                if detected:
                    exercise_pred = res.get("exercise")
                    confidence = res.get("confidence", 0)
                    form_score = res.get("form_score")
                    feedback = res.get("feedback", "")
                    angles = res.get("angles", {})
                    # 写库
                    if session_id and user_id:
                        try:
                            conn = get_db()
                            conn.execute(
                                "INSERT INTO pose_data (session_id, timestamp, keypoints, body_angle, status) VALUES (?, ?, ?, ?, ?)",
                                (session_id, int(time.time()), json.dumps(landmarks_out),
                                 angles.get("torso_tilt", 0), exercise_pred)
                            )
                            conn.commit()
                            conn.close()
                        except Exception as e:
                            log.warning(f"db write failed: {e}")
        # WS 广播 (始终推骨架预览; 训练态额外推评分/计数)
        if user_id or device_id:
            # 没训练时也要拿到 user_id — 从 device 注册表反查
            broadcast_uid = user_id
            if not broadcast_uid:
                try:
                    conn2 = get_db()
                    row2 = conn2.execute("SELECT user_id FROM devices WHERE device_id=? ORDER BY registered_at DESC LIMIT 1", (device_id,)).fetchone()
                    conn2.close()
                    if row2: broadcast_uid = row2["user_id"]
                except Exception:
                    pass
            if broadcast_uid:
                payload = {
                    "type": "coach_update",
                    "session_id": session_id,
                    "exercise": exercise_pred if not paused else None,
                    "confidence": round(confidence, 2) if not paused else None,
                    "form_score": form_score if not paused else None,
                    "feedback": feedback if not paused else "",
                    "detected": detected,
                    "landmarks": landmarks_out,
                    "paused": paused,
                    "ts": time.time(),
                }
                asyncio.create_task(_ws_broadcast_user(str(broadcast_uid), payload))
    except Exception as e:
        log.warning(f"infer error: {e}")

    return JSONResponse({
        "ok": True,
        "detected": detected,
        "exercise": exercise_pred,
        "confidence": round(confidence, 3),
        "form_score": form_score,
        "feedback": feedback,
        "angles": angles,
        "paused": paused,
        "exercise_hint": exercise_hint,
        "next_interval_ms": next_interval_ms,
        "infer_ms": int((time.time() - t0) * 1000),
        "inference_ms": int((time.time() - t0) * 1000),
    })


# ============================================================
# B-08 WS /ws/coach/{user_id} (按用户订阅广播)
# ============================================================
# user_id -> set[WebSocket]
_coach_listeners: Dict[str, set] = {}


async def _ws_broadcast_user(user_id: str, payload: Dict):
    listeners = list(_coach_listeners.get(str(user_id), set()))
    if not listeners:
        return
    text = json.dumps(payload, ensure_ascii=False)
    dead = []
    for ws in listeners:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for d in dead:
        _coach_listeners.get(str(user_id), set()).discard(d)
    log.info(f"broadcast_to_user uid={user_id} listeners={len(listeners)}")


@app.websocket("/ws/coach/{user_id}")
async def ws_coach(websocket: WebSocket, user_id: str):
    await websocket.accept()
    _coach_listeners.setdefault(str(user_id), set()).add(websocket)
    log.info(f"coach WS connect uid={user_id} total={len(_coach_listeners[str(user_id)])}")
    try:
        while True:
            # 客户端可发心跳, 服务端忽略
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"ws_coach error: {e}")
    finally:
        _coach_listeners.get(str(user_id), set()).discard(websocket)
        log.info(f"coach WS disconnect uid={user_id}")


# ============================================================
# AI Planner endpoints (主端口 8000 直接也提供, 不用切 8081)
# ============================================================
@app.post("/api/v2/ai/daily_summary")
async def v2_ai_daily(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        res = ai_planner.daily_summary(conn, user["user_id"])
    finally:
        conn.close()
    if isinstance(res, dict):
        res["ok"] = True
        return JSONResponse(res)
    return JSONResponse({"ok": True, "summary": res or ""})


@app.post("/api/v2/ai/weekly_report")
async def v2_ai_weekly(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        res = ai_planner.weekly_report(conn, user["user_id"])
    finally:
        conn.close()
    if isinstance(res, dict):
        res["ok"] = True
        return JSONResponse(res)
    return JSONResponse({"ok": True, "report": res or ""})


@app.post("/api/v2/ai/plan_generate")
async def v2_ai_plan(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    goal = body.get("goal", "增肌")
    weeks = int(body.get("weeks", 4))
    conn = get_db()
    try:
        res = ai_planner.generate_plan(conn, user["user_id"], goal, weeks)
    finally:
        conn.close()
    if isinstance(res, dict):
        res["ok"] = True
        return JSONResponse(res)
    return JSONResponse({"ok": True, "plans": res or []})


@app.post("/api/v2/ai/chat")
async def v2_ai_chat(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    msg = body.get("message") or ""
    history = body.get("history") or []
    conn = get_db()
    try:
        res = ai_planner.chat(conn, user["user_id"], msg, history)
    finally:
        conn.close()
    if isinstance(res, dict):
        res["ok"] = True
        return JSONResponse(res)
    return JSONResponse({"ok": True, "reply": res or ""})


@app.post("/api/v2/ai/meal_suggestion")
async def v2_ai_meal(req: Request):
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        res = ai_planner.meal_suggestion(conn, user["user_id"])
    finally:
        conn.close()
    if isinstance(res, dict):
        res["ok"] = True
        return JSONResponse(res)
    return JSONResponse({"ok": True, "suggestion": res or ""})


# 启动时初始化 auth db
try:
    auth.init_auth_db()
except Exception as e:
    log.warning(f"init_auth_db: {e}")

log.info("main_v2_routes loaded, total routes attached.")


# ====== 2026-05-28: 补齐 12 个 APP 必需的 v2 路由 (plans/stats/sessions/metrics/exercise/bind/vision) ======
try:
    import main_v2_extra  # noqa: F401
except Exception as e:
    log.warning(f"main_v2_extra load failed: {e}")

