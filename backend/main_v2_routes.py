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
import os, sys, json, time, base64, io, asyncio, logging, subprocess, threading, shutil
from typing import Optional, Dict, Any
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, RedirectResponse

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


# ============================================================
# 评分V2 第三阶段: 帧环形缓冲 (rep 完成时回捞起始/最深/结束三关键帧给 AI 评审团)
# ============================================================
from collections import deque as _deque
_frame_buffers: Dict[str, Any] = {}
REP_FRAME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "rep_frames")


def _frame_buffer_key(device_id: str, session_id: Optional[str] = None) -> str:
    # Keep preview/live-frame lookup by device, but persist rep clips from a
    # per-session buffer so two users on the same ESP32 cannot share old frames.
    return f"{device_id or 'default'}::{session_id}" if session_id else (device_id or "default")


def _buffer_frame(device_id: str, ts: float, jpg_bytes: bytes, maxlen: int = 900, session_id: Optional[str] = None):
    keys = [device_id or "default"]
    if session_id:
        keys.append(_frame_buffer_key(device_id or "default", session_id))
    for key in keys:
        buf = _frame_buffers.get(key)
        if buf is None:
            buf = _deque(maxlen=maxlen)
            _frame_buffers[key] = buf
        buf.append((ts, jpg_bytes))


def _clear_session_frame_buffer(device_id: str, session_id: Optional[str]):
    if session_id:
        _frame_buffers.pop(_frame_buffer_key(device_id or "default", session_id), None)


def _clear_device_frame_buffer(device_id: str):
    buf = _frame_buffers.get(device_id or "default")
    if buf is None:
        return
    try:
        buf.clear()
    except Exception:
        _frame_buffers.pop(device_id or "default", None)


# ============================================================
# 监控面板 (PC dashboard): 实时帧 + 实时识别 + 历史数据
# ============================================================
LAST_INFER: Dict[str, Any] = {}   # device_id -> 最新推理 payload(内存)

# 历史关键帧/完整片段静态服务 (监控台回放用)
try:
    from fastapi.staticfiles import StaticFiles as _SF
    _DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if os.path.isdir(_DATA_DIR):
        app.mount("/repdata", _SF(directory=_DATA_DIR), name="repdata")
except Exception as _e:
    log.warning(f"repdata mount failed: {_e}")


def _latest_device() -> Optional[str]:
    best, bts = None, -1.0
    for dev, buf in _frame_buffers.items():
        # Session-scoped buffers are for rep persistence only. Live monitor/state
        # should resolve to the physical device key stored in LAST_INFER.
        if "::" in str(dev):
            continue
        if buf and buf[-1][0] > bts:
            bts = buf[-1][0]; best = dev
    return best


@app.get("/monitor")
async def _monitor_redirect():
    return RedirectResponse("/static/monitor.html")


@app.get("/api/v2/monitor/frame")
def monitor_frame(device_id: Optional[str] = None):
    """ESP32 传到后端的最新一帧 (后端真正用来推理的那帧)."""
    dev = device_id or _latest_device()
    buf = _frame_buffers.get(dev) if dev else None
    if not buf:
        return Response(status_code=204)
    return Response(content=buf[-1][1], media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/v2/monitor/state")
def monitor_state(device_id: Optional[str] = None):
    """最新一次推理结果 + 最近完成 rep 的规则分/模型分."""
    dev = device_id or _latest_device()
    st = dict(LAST_INFER.get(dev or "", {}))
    try:
        conn = get_db()
        r = conn.execute("SELECT id,exercise,total,depth,control,symmetry,peak_angle,duration_s,angle_series,ts,clip_dir "
            "FROM rep_scores ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if r:
            lr = {k: r[k] for k in r.keys() if k != "angle_series"}
            try:
                import rep_quality_tcn
                lr["model_quality"] = rep_quality_tcn.score_rep_quality(
                    json.loads(r["angle_series"] or "{}"), r["total"])
            except Exception:
                lr["model_quality"] = None
            st["last_rep"] = lr
    except Exception:
        pass
    st["device_id"] = dev
    st["server_ts"] = time.time()
    return JSONResponse(st)


@app.get("/api/v2/monitor/history")
def monitor_history(limit: int = 40, exercise: Optional[str] = None):
    conn = get_db()
    try:
        _ensure_label_cols(conn)
        q = ("SELECT id,exercise,total,depth,control,symmetry,peak_angle,duration_s,ts,true_label,error_type "
             "FROM rep_scores WHERE angle_series IS NOT NULL ")
        args: list = []
        if exercise:
            q += "AND exercise=? "; args.append(exercise)
        q += "ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        rows = conn.execute(q, args).fetchall()
        return JSONResponse({"reps": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.get("/api/v2/monitor/clip/{rep_id}")
def monitor_clip(rep_id: int):
    """一次 rep 的画面: 3 关键帧(历史都有) + 完整片段帧(新录的才有)."""
    conn = get_db()
    try:
        r = conn.execute("SELECT session_id, rep_index, start_frame, peak_frame, end_frame, clip_dir "
                         "FROM rep_scores WHERE id=?", (rep_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return JSONResponse({"ok": False}, status_code=404)

    def _url(relpath):
        if not relpath:
            return None
        p = relpath.replace("\\", "/")
        i = p.find("data/")
        return ("/repdata/" + p[i + 5:]) if i >= 0 else None

    keyframes = {k.replace("_frame", ""): _url(r[k]) for k in ("start_frame", "peak_frame", "end_frame")}
    frames = []
    if r["clip_dir"]:
        p = r["clip_dir"].replace("\\", "/")
        i = p.find("data/")
        if i >= 0:
            sub = p[i + 5:]
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), p[i:])
        else:
            sub = p
            d = os.path.join(os.path.dirname(REP_FRAME_DIR), p)
    else:
        sess = r["session_id"] or "nosession"
        sub = f"rep_clips/{sess}/rep{r['rep_index']:03d}"
        d = os.path.join(os.path.dirname(REP_FRAME_DIR), "rep_clips", sess, f"rep{r['rep_index']:03d}")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".jpg"):
                frames.append(f"/repdata/{sub}/{fn}")
    return JSONResponse({"ok": True, "keyframes": keyframes, "frames": frames, "has_clip": len(frames) > 0})


@app.get("/api/v2/monitor/eval")
def monitor_eval():
    """TCN 合格判定的诚实评估结果 (交叉验证)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "models", "tcn_eval.json")
    try:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
        return Response(json.dumps(data, ensure_ascii=False), media_type="application/json; charset=utf-8")
    except Exception:
        return JSONResponse({"available": False})


# ---- 全身校准: 判断头/肩/髋/膝/踝是否都在画面内 ----
_CALIB_GROUPS = {"头部": [0], "肩部": [11, 12], "髋部": [23, 24], "膝盖": [25, 26], "脚踝": [27, 28]}


def _calibration(lms):
    if not lms or len(lms) < 29:
        return {"ok": False, "missing": ["全身"], "parts": {},
                "guidance": "未检测到完整人体,请站到摄像头前"}

    def vis_in(i):
        l = lms[i]
        x = l.get("x") if isinstance(l, dict) else l[0]
        y = l.get("y") if isinstance(l, dict) else l[1]
        v = l.get("v") if isinstance(l, dict) else (l[3] if len(l) > 3 else 1.0)
        return (v is not None and v > 0.5 and x is not None and y is not None
                and 0.03 < x < 0.97 and 0.03 < y < 0.97)
    parts, missing = {}, []
    for name, idxs in _CALIB_GROUPS.items():
        ok = any(vis_in(i) for i in idxs)
        parts[name] = ok
        if not ok:
            missing.append(name)
    if not missing:
        g = "全身已进入画面,可以开始 ✓"
    elif "脚踝" in missing or "膝盖" in missing:
        g = "腿/脚没进画面 — 往后退一点 或 把摄像头朝下调"
    elif "头部" in missing:
        g = "头没进画面 — 往后退 或 把摄像头朝上调"
    elif "髋部" in missing:
        g = "腰胯没对齐 — 调整站位/距离"
    else:
        g = "请站到画面正中、保证全身入镜"
    return {"ok": len(missing) == 0, "missing": missing, "parts": parts, "guidance": g}


@app.get("/api/v2/monitor/calibration")
def monitor_calibration(device_id: Optional[str] = None):
    dev = device_id or _latest_device()
    st = LAST_INFER.get(dev or "", {})
    return JSONResponse(_calibration(st.get("landmarks")))


# ---- 拉流管理: 后端独占拉 ESP32 MJPEG 流做高帧率识别 ----
_PULLER: Dict[str, Any] = {"proc": None}

# ---- 监控台会话控制: 开始/完成运动(是否计数+存rep) + 当前动作(切换) ----
_MON: Dict[str, Any] = {"recording": False, "exercise": "squat", "device": "esp32cam-001", "user_id": 31}


def _puller_stop():
    p = _PULLER.get("proc")
    if p is not None and p.poll() is None:
        try:
            p.terminate()
        except Exception:
            pass
    _PULLER["proc"] = None


@app.post("/api/v2/monitor/puller/start")
async def puller_start(req: Request):
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "url required"}, status_code=400)
    user_id = str(body.get("user_id") or "31")
    exercise = (body.get("exercise") or "squat").strip()
    device = (body.get("device_id") or "esp32cam-001").strip()
    fps = str(body.get("fps") or 8)
    _puller_stop()
    feeder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mjpeg_feeder.py")
    try:
        _PULLER["proc"] = subprocess.Popen([sys.executable, feeder, url, device, user_id, exercise, fps])
        log.info(f"MJPEG puller started pid={_PULLER['proc'].pid} url={url} ex={exercise}")
        return JSONResponse({"ok": True, "pid": _PULLER["proc"].pid})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/v2/monitor/puller/stop")
async def puller_stop_ep():
    _puller_stop()
    return JSONResponse({"ok": True})


@app.get("/api/v2/monitor/puller/status")
def puller_status():
    p = _PULLER.get("proc")
    return JSONResponse({"running": bool(p is not None and p.poll() is None)})


# ---- 数据采集标注: 成批把"刚做的、未标注的" rep 打上真值标签(合格/不合格 + 错误类型) ----
def _ensure_label_cols(conn):
    for col in ("true_label", "error_type", "clip_id", "clip_dir"):
        try:
            conn.execute(f"ALTER TABLE rep_scores ADD COLUMN {col} TEXT")
        except Exception:
            pass
    conn.commit()


def _label_counts(conn):
    pas = conn.execute("SELECT COUNT(*) FROM rep_scores WHERE true_label='合格'").fetchone()[0]
    fail = conn.execute("SELECT COUNT(*) FROM rep_scores WHERE true_label='不合格'").fetchone()[0]
    unlb = conn.execute("SELECT COUNT(*) FROM rep_scores WHERE true_label IS NULL").fetchone()[0]
    rows = conn.execute("SELECT error_type, COUNT(*) FROM rep_scores "
                        "WHERE true_label='不合格' AND error_type IS NOT NULL GROUP BY error_type").fetchall()
    return {"pass": pas, "fail": fail, "unlabeled": unlb, "by_type": {r[0]: r[1] for r in rows}}


@app.post("/api/v2/monitor/label_batch")
async def label_batch(req: Request):
    body = await req.json()
    label = (body.get("label") or "").strip()
    error_type = (body.get("error_type") or "").strip() or None
    if label not in ("合格", "不合格"):
        return JSONResponse({"ok": False, "error": "label 必须是 合格/不合格"}, status_code=400)
    try:
        conn = get_db()
        _ensure_label_cols(conn)
        cur = conn.execute("UPDATE rep_scores SET true_label=?, error_type=? WHERE true_label IS NULL",
                           (label, error_type))
        tagged = cur.rowcount
        conn.commit()
        stats = _label_counts(conn)
        conn.close()
        return JSONResponse({"ok": True, "tagged": tagged, **stats})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/v2/monitor/label_stats")
def label_stats():
    try:
        conn = get_db()
        _ensure_label_cols(conn)
        stats = _label_counts(conn)
        conn.close()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"pass": 0, "fail": 0, "unlabeled": 0, "by_type": {}, "error": str(e)})


@app.post("/api/v2/monitor/label_rep")
async def label_rep(req: Request):
    """逐个给单条 rep 打真值标签(回放后自己判定用)."""
    body = await req.json()
    rep_id = body.get("rep_id")
    label = (body.get("label") or "").strip()
    error_type = (body.get("error_type") or "").strip() or None
    if label not in ("合格", "不合格") or rep_id is None:
        return JSONResponse({"ok": False, "error": "需要 rep_id + label(合格/不合格)"}, status_code=400)
    try:
        conn = get_db()
        _ensure_label_cols(conn)
        cur = conn.execute("UPDATE rep_scores SET true_label=?, error_type=? WHERE id=?",
                           (label, error_type, int(rep_id)))
        conn.commit()
        stats = _label_counts(conn)
        conn.close()
        return JSONResponse({"ok": True, "updated": cur.rowcount, **stats})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ---- 监控台会话控制: 开始运动 / 完成运动 / 切换动作 ----
def _reset_counting(dev: str, session_id: Optional[str] = None):
    """开始新一组 / 切换动作时, 重置计数器 + 评分器(计数从 0)."""
    try:
        _detectors.pop(dev, None)
    except Exception:
        pass
    try:
        import rep_scorer as _rs
        _rs.reset_rep_scorer(dev, session_id)
    except Exception:
        pass


@app.get("/api/v2/monitor/session")
def monitor_session_get():
    return JSONResponse({"recording": _MON["recording"], "exercise": _MON["exercise"]})


@app.post("/api/v2/monitor/session/start")
async def monitor_session_start(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    ex = (body.get("exercise") or "").strip()
    if ex:
        _MON["exercise"] = ex
    _MON["recording"] = True
    user_id = str(body.get("user_id") or _MON.get("user_id") or "31")
    session_id = f"sess_{user_id}_{int(time.time())}"
    _MON["user_id"] = int(user_id) if str(user_id).isdigit() else user_id
    active_trainings[_MON["device"]] = {
        "user_id": int(user_id) if str(user_id).isdigit() else user_id,
        "exercise": _MON["exercise"],
        "session_id": session_id,
        "started_at": time.time(),
        "mode": "monitor",
    }
    _reset_counting(_MON["device"], session_id)
    _clear_device_frame_buffer(_MON["device"])
    _clear_session_frame_buffer(_MON["device"], session_id)
    log.info(f"monitor recording START exercise={_MON['exercise']} sid={session_id}")
    return JSONResponse({"ok": True, "recording": True, "exercise": _MON["exercise"], "session_id": session_id})


@app.post("/api/v2/monitor/session/stop")
async def monitor_session_stop():
    _MON["recording"] = False
    sess = active_trainings.pop(_MON["device"], None)
    log.info(f"monitor recording STOP sid={sess.get('session_id') if sess else None}")
    return JSONResponse({"ok": True, "recording": False, "session_id": sess.get("session_id") if sess else None})


@app.post("/api/v2/monitor/session/exercise")
async def monitor_session_exercise(req: Request):
    body = await req.json()
    ex = (body.get("exercise") or "").strip()
    if not ex:
        return JSONResponse({"ok": False, "error": "exercise required"}, status_code=400)
    _MON["exercise"] = ex
    sess = active_trainings.get(_MON["device"])
    if sess:
        sess["exercise"] = ex
    _reset_counting(_MON["device"], sess.get("session_id") if sess else None)
    return JSONResponse({"ok": True, "exercise": ex, "recording": _MON["recording"]})


@app.post("/api/v2/monitor/rep_delete")
async def rep_delete(req: Request):
    """删除一条误触发/无效的 rep(回看时发现没真做). 连带删关键帧+片段文件."""
    import shutil
    body = await req.json()
    rep_id = body.get("rep_id")
    if rep_id is None:
        return JSONResponse({"ok": False, "error": "rep_id required"}, status_code=400)
    try:
        conn = get_db()
        _ensure_label_cols(conn)
        row = conn.execute("SELECT session_id, rep_index, start_frame, peak_frame, end_frame, clip_dir "
                           "FROM rep_scores WHERE id=?", (int(rep_id),)).fetchone()
        conn.execute("DELETE FROM rep_scores WHERE id=?", (int(rep_id),))
        conn.commit()
        stats = _label_counts(conn)
        conn.close()
        if row:
            base = os.path.dirname(os.path.abspath(__file__))
            for p in (row["start_frame"], row["peak_frame"], row["end_frame"]):
                try:
                    if p:
                        os.remove(os.path.join(base, p))
                except Exception:
                    pass
            try:
                clip_rel = row["clip_dir"] if "clip_dir" in row.keys() else None
                if clip_rel:
                    clip = os.path.join(base, clip_rel)
                else:
                    sess = row["session_id"]; ri = row["rep_index"]
                    clip = os.path.join(os.path.dirname(REP_FRAME_DIR), "rep_clips", sess, f"rep{int(ri):03d}") if sess and ri is not None else None
                if clip:
                    shutil.rmtree(clip, ignore_errors=True)
            except Exception:
                pass
        return JSONResponse({"ok": True, "deleted": int(rep_id), **stats})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/v2/monitor/rep/{rep_id}")
def monitor_rep(rep_id: int):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM rep_scores WHERE id=?", (rep_id,)).fetchone()
        if not r:
            return JSONResponse({"ok": False}, status_code=404)
        d = dict(r)
        try:
            d["angle_series"] = json.loads(d.get("angle_series") or "{}")
        except Exception:
            d["angle_series"] = {}
        try:
            import rep_quality_tcn
            d["model_quality"] = rep_quality_tcn.score_rep_quality(d["angle_series"], d.get("total"))
        except Exception:
            d["model_quality"] = None
        return JSONResponse(d)
    finally:
        conn.close()


def _save_rep_keyframes(device_id: str, session_id: str, rep: Dict) -> Dict[str, Optional[str]]:
    """按 rep 的 start/peak/end 时间戳从缓冲取最近帧落盘, 返回相对路径."""
    out = {"start_frame": None, "peak_frame": None, "end_frame": None}
    buf = _frame_buffers.get(_frame_buffer_key(device_id, session_id)) or _frame_buffers.get(device_id)
    if not buf:
        return out
    frames = list(buf)
    clip_id = rep.get("clip_id") or f"rep{rep['rep_index']:03d}_{int((rep.get('ts') or time.time()) * 1000)}"
    sess_dir = os.path.join(REP_FRAME_DIR, session_id or "nosession", clip_id)
    os.makedirs(sess_dir, exist_ok=True)
    for key, ts_key in (("start_frame", "start_ts"), ("peak_frame", "peak_ts"), ("end_frame", "end_ts")):
        target = rep.get(ts_key)
        if target is None:
            continue
        nearest = min(frames, key=lambda f: abs(f[0] - target))
        if abs(nearest[0] - target) > 3.0:   # 缓冲里没有足够近的帧
            continue
        fname = f"{key.split('_')[0]}.jpg"
        fpath = os.path.join(sess_dir, fname)
        try:
            with open(fpath, "wb") as f:
                f.write(nearest[1])
            out[key] = os.path.relpath(fpath, os.path.dirname(os.path.abspath(__file__)))
        except Exception as e:
            log.warning(f"rep keyframe save failed: {e}")
    return out


def _save_rep_clip(device_id: str, session_id: str, rep: Dict, max_frames: int = 120) -> Optional[str]:
    """把一次 rep 的全部缓冲帧落盘成片段(供监控台回放完整动作)."""
    buf = _frame_buffers.get(_frame_buffer_key(device_id, session_id)) or _frame_buffers.get(device_id)
    if not buf:
        return None
    s = rep.get("start_ts"); e = rep.get("end_ts")
    frames = [(ts, jb) for (ts, jb) in list(buf)
              if (s is None or ts >= s - 0.1) and (e is None or ts <= e + 0.1)]
    if len(frames) < 2:
        return None
    if len(frames) > max_frames:
        sel = [int(i * (len(frames) - 1) / (max_frames - 1)) for i in range(max_frames)]
        frames = [frames[i] for i in sel]
    clip_root = os.path.join(os.path.dirname(REP_FRAME_DIR), "rep_clips")
    clip_id = rep.get("clip_id") or f"rep{rep.get('rep_index', 0):03d}_{int((rep.get('ts') or time.time()) * 1000)}"
    d = os.path.join(clip_root, session_id or "nosession", clip_id)
    try:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
        for i, (ts, jb) in enumerate(frames):
            with open(os.path.join(d, f"f{i:03d}.jpg"), "wb") as f:
                f.write(jb)
        return os.path.relpath(d, os.path.dirname(os.path.abspath(__file__)))
    except Exception as ex:
        log.warning(f"rep clip save failed: {ex}")
        return None


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
    from pose_engine import PoseEngine, create_engine
    _pose_engine: Optional[Any] = None
    def get_pose_engine() -> Optional[Any]:
        global _pose_engine
        if _pose_engine is None:
            try:
                # POSE_BACKEND=yolo26 (默认) | mediapipe; yolo26 失败自动回退
                _pose_engine = create_engine()
                log.info(f"pose_engine ready: {type(_pose_engine).__name__}")
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
    # 模式: guidance(指导动作-逐次实时矫正) | complete(完整运动-结束出报告). 默认 complete
    mode = (body.get("mode") or "complete").strip().lower()
    if mode not in ("guidance", "complete"):
        mode = "complete"
    if not device_id or not user_id:
        return JSONResponse({"ok": False, "error": "device_id and user_id required"}, status_code=400)
    session_id = f"sess_{user_id}_{int(time.time())}"
    active_trainings[device_id] = {
        "user_id": int(user_id),
        "exercise": exercise,
        "session_id": session_id,
        "started_at": time.time(),
        "mode": mode,
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
    log.info(f"training start device={device_id} user={user_id} exercise={exercise} sid={session_id} mode={mode}")
    return JSONResponse({"ok": True, "session_id": session_id, "exercise": exercise, "mode": mode})


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
            # 评分V2: 优先用按 rep 结算的成绩 (不被站立/组间帧稀释); 无 rep 时回退帧均分
            try:
                row_r = conn.execute(
                    "SELECT COUNT(*) AS n, AVG(total) AS t FROM rep_scores WHERE session_id=?",
                    (sess["session_id"],)).fetchone()
                if row_r and row_r["n"] and row_r["n"] > 0 and row_r["t"] is not None:
                    avg_form = round(row_r["t"], 1)
            except Exception:
                pass  # rep_scores 表还没建 (本会话无完成动作)
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
        allow_auto_revive = not (device_id == _MON.get("device") and not _MON.get("recording"))
        if allow_auto_revive and device_id and body_user_id and body_exercise:
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
    next_interval_ms = 5000 if paused else 120   # 训练态 ~8.3fps (MediaPipe 14ms 轻松跟上)
    exercise_hint = sess["exercise"] if sess else requested_exercise
    # 监控台管理的设备: 当前动作由"切换动作"按钮决定(即时生效), 覆盖 feeder/会话里的值
    if device_id == _MON["device"]:
        exercise_hint = _MON["exercise"]
    _rec_ok = (_MON["recording"] or device_id != _MON["device"])  # 监控设备只在"开始运动"后才计数/存rep
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
    rep_score_out = None  # 评分V2: 最近一次完成动作的分项分
    exercise_sig = None   # 多参数动作识别签名 (供监控台展示)
    try:
        if image_b64:
            import numpy as np, cv2
            raw = base64.b64decode(image_b64.split(",")[-1])
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            img_for_broadcast = img
            # 帧时间戳: 默认服务器时间; 回放/模拟可传 frame_ts (视频真实时间), 保证节奏评分准确
            try:
                frame_ts = float(body.get("frame_ts") or 0) or time.time()
            except (TypeError, ValueError):
                frame_ts = time.time()
            if not paused:
                _buffer_frame(device_id or "default", frame_ts, raw, session_id=session_id)
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
                    # 体态门禁: 人实际在做的动作(趴/站)要和目标动作一致, 否则不计数
                    # (例如选俯卧撑却在做深蹲, 不应给俯卧撑加 reps)
                    posture_ok = True
                    try:
                        import pose_engine as _pe2
                        exercise_sig = _pe2.match_exercise_signature(exercise_pred or raw_pred, angles)
                        posture_ok = exercise_sig["ok"]
                    except Exception:
                        pass
                    if not posture_ok:
                        bad = [x["name"] for x in (exercise_sig or {}).get("params", []) if x.get("hard") and not x.get("ok")]
                        feedback = (f"姿势与所选动作不符({'、'.join(bad)}), 请按{exercise_pred}的姿态做"
                                    if bad else f"姿势与所选动作不符, 请按{exercise_pred}的姿态做")
                    # Rep counting: target exercise is fixed by the user's Spinner selection.
                    # 无效帧(人体不完整)或体态不符 不参与计数, 防止虚计次数
                    det = _get_detector(device_id or "default", exercise_pred)
                    if det is not None and angles and pose_valid and posture_ok and _rec_ok:
                        det_angles = {
                            "left_knee": angles.get("knee_L"), "right_knee": angles.get("knee_R"),
                            "left_hip": angles.get("hip_L"), "right_hip": angles.get("hip_R"),
                            "left_elbow": angles.get("elbow_L"), "right_elbow": angles.get("elbow_R"),
                            "left_shoulder": angles.get("shoulder_L"), "right_shoulder": angles.get("shoulder_R"),
                            "torso_tilt": angles.get("torso_tilt"),
                            # 多关节生物力学量 (评分V2 第四阶段)
                            "ankle_dx": angles.get("ankle_dx"), "wrist_above": angles.get("wrist_above"),
                            "head_drop": angles.get("head_drop"), "head_fwd": angles.get("head_fwd"),
                        }
                        method = getattr(det, f"count_{exercise_pred}", None)
                        if callable(method):
                            rep_count = int(method(det_angles))
                        else:
                            rep_count = int(getattr(det, "rep_count", 0))
                        log_angles = {k: round(v, 1) for k, v in det_angles.items() if v is not None}
                        log.info(f"[REP] device={device_id} target={exercise_pred} angles={log_angles} reps={rep_count} stage={det.stage}")
                        # ===== 按 rep 评分 (评分V2): 有效帧喂给 RepScorer, 计数自增时结算 =====
                        try:
                            import rep_scorer as _rs
                            scorer = _rs.get_rep_scorer(device_id or "default", session_id)
                            completed_rep = scorer.add_frame(exercise_pred, det_angles, rep_count, ts=frame_ts)
                            if completed_rep is not None:
                                # 完成一次动作: HUD 分数/反馈切换为本次动作的结算结果
                                form_score = int(completed_rep["total"])
                                feedback = completed_rep["feedback"]
                                if session_id:
                                    try:
                                        clip_id = completed_rep.get("clip_id") or f"rep{completed_rep['rep_index']:03d}_{int(completed_rep['ts'] * 1000)}"
                                        completed_rep["clip_id"] = clip_id
                                        # 第三阶段: 留存起始/最深/结束关键帧供 AI 评审团夜间批审
                                        kf = _save_rep_keyframes(device_id or "default", session_id, completed_rep)
                                        # 完整片段落盘挪到后台线程: 目录使用 clip_id 唯一化, 防止同一 session 内不同 rep 串帧。
                                        threading.Thread(
                                            target=_save_rep_clip,
                                            args=(device_id or "default", session_id, dict(completed_rep)),
                                            daemon=True,
                                        ).start()
                                        clip_dir = os.path.join("data", "rep_clips", session_id or "nosession", clip_id)
                                        conn_r = get_db()
                                        _rs.ensure_table(conn_r)
                                        conn_r.execute(
                                            "INSERT INTO rep_scores (session_id, rep_index, exercise, depth, control, "
                                            "symmetry, total, peak_angle, duration_s, feedback, ts, "
                                            "start_frame, peak_frame, end_frame, angle_series, clip_id, clip_dir) "
                                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                            (session_id, completed_rep["rep_index"], completed_rep["exercise"],
                                             completed_rep["depth"], completed_rep["control"], completed_rep["symmetry"],
                                             completed_rep["total"], completed_rep["peak_angle"],
                                             completed_rep["duration_s"], completed_rep["feedback"], completed_rep["ts"],
                                             kf["start_frame"], kf["peak_frame"], kf["end_frame"],
                                             json.dumps(completed_rep.get("angle_series")), clip_id, clip_dir))
                                        conn_r.commit()
                                        conn_r.close()
                                    except Exception as e:
                                        log.warning(f"rep_scores write failed: {e}")
                            rep_score_out = completed_rep or scorer.last_rep
                        except Exception as e:
                            log.warning(f"rep scorer failed: {e}")
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
                    "rep_score": rep_score_out,
                    "signature": exercise_sig,
                    "ts": time.time(),
                }
                LAST_INFER[device_id or "default"] = payload
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
        "rep_score": rep_score_out,
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


@app.post("/api/v2/ai/workout_report")
async def v2_ai_workout_report(req: Request):
    """模式2: 对一次完整训练(session)出报告——本次表现 + 历史对比 + 建议, 存账号."""
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await req.json()
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    conn = get_db()
    try:
        res = ai_planner.workout_report(conn, user["user_id"], session_id)
    finally:
        conn.close()
    return JSONResponse(res)


@app.get("/api/v2/ai/workout_reports")
async def v2_ai_workout_reports(req: Request):
    """历史训练报告列表."""
    user = _require_user(req)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    conn = get_db()
    try:
        ai_planner.ensure_workout_reports_table(conn)
        rows = conn.execute(
            "SELECT session_id, report_json, created_at FROM workout_reports "
            "WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["user_id"],)).fetchall()
        out = [{"session_id": r[0], "data": json.loads(r[1]) if r[1] else None, "created_at": r[2]}
               for r in rows]
    finally:
        conn.close()
    return JSONResponse({"ok": True, "reports": out})


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

