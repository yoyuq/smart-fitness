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

# ============================================================
# Rep-counting state machines per device_id
# ============================================================
try:
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai_vision"))
    from exercise_detector import ExerciseDetector, ExerciseType
    _detectors: Dict[str, ExerciseDetector] = {}
    log.info("exercise_detector available for rep counting")
except Exception as _e:
    log.warning(f"exercise_detector not available: {_e}")
    ExerciseDetector = None  # type: ignore
    _detectors = {}


def _get_detector(device_id: str, target_exercise: Optional[str] = None) -> Optional[Any]:
    """Return (and optionally configure) the ExerciseDetector for a device.
    Only resets rep_count when the target exercise *changes*, not on every call."""
    if ExerciseDetector is None:
        return None
    det = _detectors.get(device_id)
    if det is None:
        det = ExerciseDetector()
        _detectors[device_id] = det
    if target_exercise:
        try:
            current_target = det.get_target_exercise()
            new_target = ExerciseType(target_exercise)
            if current_target != new_target:
                # Target changed — reset is correct here (new exercise)
                det.set_target_exercise(new_target)
                log.info(f"detector {device_id} target changed {current_target} -> {target_exercise}, reps reset")
        except ValueError:
            pass  # unknown exercise string, ignore
    return det


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
    # Reset per-device rep counter when a workout starts.
    try:
        if device_id in _detectors:
            _detectors[device_id].reset()
        det = _get_detector(device_id, exercise)
        if det is not None:
            det.set_target_exercise(exercise)
    except Exception as e:
        log.warning(f"detector reset failed: {e}")
    log.info(f"training start device={device_id} user={user_id} exercise={exercise} sid={session_id}")
    return JSONResponse({"ok": True, "session_id": session_id, "exercise": exercise})


@app.post("/api/v2/training/stop")
async def v2_train_stop(req: Request):
    body = await req.json()
    device_id = (body.get("device_id") or "").strip()
    sess = active_trainings.pop(device_id, None)
    log.info(f"training stop device={device_id} had={bool(sess)}")
    # 训练结束落库 sessions, 否则 /api/v2/sessions/history 永远为空
    if sess:
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT MAX(rep_count) AS reps, AVG(form_score) AS form FROM pose_data WHERE session_id=?",
                (sess["session_id"],)).fetchone()
            total_reps = int(row["reps"]) if row and row["reps"] is not None else 0
            avg_form = round(row["form"], 1) if row and row["form"] is not None else None
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, device_id, user_id, exercise_type, "
                "start_time, end_time, total_reps, avg_form_score, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (sess["session_id"], device_id, str(sess["user_id"]), sess["exercise"],
                 sess["started_at"], time.time(), total_reps, avg_form, "completed"))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"session persist failed: {e}")
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
    source = (body.get("source") or body.get("device_type") or "esp32cam").strip()
    image_b64 = body.get("image_base64") or body.get("image") or ""

    # 训练态控制
    sess = active_trainings.get(device_id)
    # 自动复活训练态: 如果推理帧带有 user_id + exercise, 但训练态丢了,
    # 就在收到这条请求时自动恢复. 这样 ESP32 就算被意外 stop/掉态,
    # 下一条 APP 二次推理帧进来就回到 500ms 帧率, 不用重新点开始.
    if sess is None:
        body_user_id = body.get("user_id") or None
        body_exercise = (body.get("exercise") or body.get("exercise_type") or "").strip() or None
        if device_id and body_user_id and body_exercise:
            new_sid = f"sess_{body_user_id}_{int(time.time())}"
            active_trainings[device_id] = {
                "user_id": int(body_user_id),
                "exercise": body_exercise,
                "session_id": new_sid,
                "started_at": time.time(),
            }
            sess = active_trainings[device_id]
            log.warning(f"auto-revived training device={device_id} user={body_user_id} ex={body_exercise} sid={new_sid}")
        # else: ESP32 预览模式(无 user_id/exercise) — 不复活, 保持降频
    paused = sess is None
    # APP preview also sends the selected exercise. Prefer active training state,
    # but fall back to request body so reps can be counted before/without WS lag.
    requested_exercise = (body.get("exercise") or body.get("exercise_type") or "").strip() or None
    next_interval_ms = 5000 if paused else 500
    exercise_hint = sess["exercise"] if sess else requested_exercise
    user_id = sess["user_id"] if sess else (body.get("user_id") or None)
    session_id = sess["session_id"] if sess else body.get("session_id")

    # 推理
    detected = False
    exercise_pred = None
    confidence = 0.0
    form_score = None
    feedback = ""
    rep_count = 0
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
                    raw_pred = res.get("exercise")
                    exercise_pred = exercise_hint or raw_pred
                    confidence = res.get("confidence", 0)
                    angles = res.get("angles", {})
                    # Score the selected target exercise when available, not whatever a single frame predicts.
                    form_score = res.get("form_score")
                    feedback = res.get("feedback", "")
                    pose_valid = res.get("pose_valid", True)
                    try:
                        import pose_engine as _pe
                        # 有效性按"目标动作"重新校验 (引擎里是按预测动作校验的)
                        vis33 = [l.get("v", 0.0) for l in landmarks_out] if landmarks_out else []
                        if vis33:
                            pose_valid, vis_quality = _pe.check_pose_validity(vis33, exercise_pred or raw_pred)
                        else:
                            vis_quality = 0.0
                        score_rule = _pe.FORM_RULES.get(exercise_pred or raw_pred)
                        if score_rule and angles:
                            _score, _fb = score_rule(angles)
                            form_score, feedback = _pe.apply_score_gate(int(_score), _fb, pose_valid, vis_quality)
                    except Exception:
                        pass
                    # Rep counting: target exercise is fixed by the user's Spinner selection.
                    # 无效帧 (人体不完整) 不参与计数, 防止幻觉关节虚计次数
                    det = _get_detector(device_id or "default", exercise_pred)
                    if det is not None and angles and pose_valid:
                        det_angles = {
                            "left_knee": angles.get("knee_L"), "right_knee": angles.get("knee_R"),
                            "left_hip": angles.get("hip_L"), "right_hip": angles.get("hip_R"),
                            "left_elbow": angles.get("elbow_L"), "right_elbow": angles.get("elbow_R"),
                            "left_shoulder": angles.get("shoulder_L"), "right_shoulder": angles.get("shoulder_R"),
                            "torso_tilt": angles.get("torso_tilt"),
                        }
                        method = getattr(det, f"count_{exercise_pred}", None)
                        if callable(method):
                            rep_count = int(method(det_angles))
                        else:
                            rep_count = int(getattr(det, "rep_count", 0))
                        log_angles = {k: round(v, 1) for k, v in det_angles.items() if v is not None}
                        log.info(f"[REP] device={device_id} target={exercise_pred} angles={log_angles} reps={rep_count} stage={det.stage}")
                    elif det is not None:
                        # 无效帧: 维持已有计数, 不推进状态机
                        rep_count = int(getattr(det, "rep_count", 0))
                    # 写库
                    if session_id and user_id:
                        try:
                            conn = get_db()
                            conn.execute(
                                "INSERT INTO pose_data (session_id, timestamp, exercise_type, rep_count, "
                                "form_score, angles_json, landmarks_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (session_id, time.time(), exercise_pred, rep_count, form_score,
                                 json.dumps({k: round(v, 2) for k, v in angles.items() if v is not None}),
                                 json.dumps(landmarks_out))
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
                    "form_score": form_score,
                    "rep_count": rep_count,
                    "feedback": feedback,
                    "detected": detected,
                    "landmarks": landmarks_out,
                    "paused": paused,
                    "source": source,
                    "device_type": source,
                    "device_id": device_id,
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
        "rep_count": rep_count,
        "feedback": feedback,
        "angles": angles,
        "paused": paused,
        "exercise_hint": exercise_hint,
        "next_interval_ms": next_interval_ms,
        "source": source,
        "device_type": source,
        "device_id": device_id,
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


@app.post("/api/v2/ai/coach_review")
async def v2_ai_coach_review(req: Request):
    """私人教练管家: 整合历史数据+当前计划的系统复盘 (趋势/平衡/弱点/执行率/下周建议)."""
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        res = ai_planner.coach_review(conn, user["user_id"])
    finally:
        conn.close()
    return JSONResponse(res)


@app.get("/api/v2/ai/memory")
async def v2_ai_memory_list(req: Request):
    """教练长期记忆列表."""
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        notes = ai_planner.get_coach_memories(conn, user["user_id"], limit=30)
    finally:
        conn.close()
    return JSONResponse({"ok": True, "memories": notes})


@app.post("/api/v2/ai/memory")
async def v2_ai_memory_add(req: Request):
    """手动添加教练记忆 (如 '膝盖有旧伤' / '目标6月前减5kg')."""
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    note = (body.get("note") or "").strip()
    if not note:
        return JSONResponse({"ok": False, "error": "note required"}, status_code=400)
    category = (body.get("category") or "general").strip()
    conn = get_db()
    try:
        ai_planner.add_coach_memory(conn, user["user_id"], note, category)
    finally:
        conn.close()
    return JSONResponse({"ok": True})


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

