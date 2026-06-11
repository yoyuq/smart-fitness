"""
main.py - FastAPI Backend Server
=================================
Framework: FastAPI
  Source: https://github.com/tiangolo/fastapi (MIT License)
Framework: Uvicorn
  Source: https://github.com/encode/uvicorn (BSD 3-Clause)

Provides:
  - RESTful API for exercise session management
  - Real-time MQTT bridge for ESP32 sensor data
  - Pose data processing and storage
  - WebSocket for live client updates
"""

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import sqlite3
import os
import threading

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv 未安装时退回纯环境变量

from mqtt_client import MQTTClientHandler

# ---------- App Initialization ----------

app = FastAPI(
    title="Smart Fitness Guidance System API",
    description="AI-powered fitness coaching backend with IoT sensor integration",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- PWA (PC 浏览器瘦客户端) ----------
# static/index.html 是 PC 端网页训练界面; v2 重写 main 时挂载被丢掉, 这里恢复
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/app")
    async def pwa_redirect():
        return RedirectResponse("/static/index.html")

# ---------- Database Setup ----------

DB_PATH = os.path.join(os.path.dirname(__file__), "fitness.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            name TEXT,
            firmware_version TEXT,
            last_seen REAL,
            wifi_rssi REAL,
            status TEXT DEFAULT 'offline'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            device_id TEXT,
            user_id TEXT,
            exercise_type TEXT,
            start_time REAL,
            end_time REAL,
            total_reps INTEGER DEFAULT 0,
            avg_form_score REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (device_id) REFERENCES devices(device_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pose_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL,
            exercise_type TEXT,
            rep_count INTEGER,
            form_score REAL,
            angles_json TEXT,
            landmarks_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL,
            heart_rate REAL,
            hr_confidence REAL,
            movement_intensity REAL,
            body_angle REAL,
            accel_x REAL,
            accel_y REAL,
            accel_z REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL,
            severity TEXT,
            message TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ---------- MQTT Client ----------

mqtt_handler = MQTTClientHandler()
mqtt_thread = None


def start_mqtt():
    global mqtt_thread
    mqtt_handler.connect()
    mqtt_thread = threading.Thread(target=mqtt_handler.loop_forever, daemon=True)
    mqtt_thread.start()


@app.on_event("startup")
async def startup():
    start_mqtt()
    print("[Backend] Server started - MQTT handler active")
    # Pose engine warmup (避免第一次推理冷启动 2s 卡顿)
    try:
        import asyncio
        async def _warmup():
            try:
                import sys, os
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml_pose"))
                from pose_engine import PoseEngine
                import numpy as np
                eng = PoseEngine()
                # 触发一次推理预热
                dummy = np.zeros((240, 320, 3), dtype=np.uint8)
                eng.infer_from_image(dummy)
                # 把预热好的引擎实例注入 v2 routes 全局
                import main_v2_routes
                main_v2_routes._pose_engine = eng
                print("[Backend] pose_engine warmup OK, classifier=" + ("yes" if eng.clf else "no"))
            except Exception as e:
                print(f"[Backend] pose warmup failed: {e}")
        asyncio.create_task(_warmup())
    except Exception as e:
        print(f"[Backend] warmup schedule failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    mqtt_handler.disconnect()


# ---------- Data Models ----------

class DeviceRegister(BaseModel):
    device_id: str
    name: Optional[str] = None
    firmware_version: Optional[str] = None


class SessionStart(BaseModel):
    device_id: str
    user_id: Optional[str] = None
    exercise_type: str = "squat"


class PoseFrame(BaseModel):
    timestamp: float
    exercise_type: str
    rep_count: int
    form_score: float
    angles: Dict[str, Optional[float]]
    landmarks: Optional[List[Dict[str, Any]]] = None


class SensorReading(BaseModel):
    timestamp: float
    heart_rate: Optional[float] = None
    hr_confidence: Optional[float] = None
    movement_intensity: Optional[float] = None
    body_angle: Optional[float] = None
    accel_x: Optional[float] = None
    accel_y: Optional[float] = None
    accel_z: Optional[float] = None


class FeedbackMessage(BaseModel):
    severity: str
    message: str


# ---------- WebSocket Manager ----------

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.session_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: Optional[str] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if session_id:
            if session_id not in self.session_connections:
                self.session_connections[session_id] = []
            self.session_connections[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        for sid in self.session_connections:
            if websocket in self.session_connections[sid]:
                self.session_connections[sid].remove(websocket)

    async def broadcast_to_session(self, session_id: str, message: dict):
        if session_id in self.session_connections:
            for ws in self.session_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def broadcast_all(self, message: dict):
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                pass


ws_manager = ConnectionManager()


# ---------- REST API Routes ----------

@app.get("/")
async def root():
    return {
        "service": "Smart Fitness Guidance System",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "devices": "/api/devices",
            "sessions": "/api/sessions",
            "pose": "/api/pose",
            "docs": "/docs",
        }
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "devices_active": get_active_device_count(),
        "mqtt_connected": mqtt_handler.is_connected(),
    }


def get_active_device_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM devices WHERE status='online' AND last_seen > ?",
              (time.time() - 120,))
    count = c.fetchone()[0]
    conn.close()
    return count


# --- Device Management ---

@app.post("/api/devices/register")
async def register_device(device: DeviceRegister):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO devices (device_id, name, firmware_version, last_seen, status) "
        "VALUES (?, ?, ?, ?, 'online')",
        (device.device_id, device.name or device.device_id,
         device.firmware_version or "unknown", time.time())
    )
    conn.commit()
    conn.close()
    return {"status": "registered", "device_id": device.device_id}


@app.get("/api/devices")
async def list_devices():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    rows = c.fetchall()
    conn.close()
    return [
        {
            "device_id": row[0],
            "name": row[1],
            "firmware_version": row[2],
            "last_seen": row[3],
            "wifi_rssi": row[4],
            "status": row[5],
        }
        for row in rows
    ]


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM devices WHERE device_id=?", (device_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "device_id": row[0],
        "name": row[1],
        "firmware_version": row[2],
        "last_seen": row[3],
        "wifi_rssi": row[4],
        "status": row[5],
    }


# --- Session Management ---

@app.post("/api/sessions/start")
async def start_session(session: SessionStart):
    session_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (session_id, device_id, user_id, exercise_type, start_time, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (session_id, session.device_id, session.user_id or "anonymous",
         session.exercise_type, time.time())
    )
    conn.commit()
    conn.close()
    return {"session_id": session_id, "status": "started"}


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE sessions SET end_time=?, status='completed' WHERE session_id=?",
        (time.time(), session_id)
    )
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    conn.commit()
    conn.close()
    return {"status": "ended", "session_id": session_id}


@app.get("/api/sessions")
async def list_sessions(limit: int = Query(20, ge=1, le=100)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {
            "session_id": row[0],
            "device_id": row[1],
            "user_id": row[2],
            "exercise_type": row[3],
            "start_time": row[4],
            "end_time": row[5],
            "total_reps": row[6],
            "avg_form_score": row[7],
            "status": row[8],
        }
        for row in rows
    ]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": row[0],
        "device_id": row[1],
        "user_id": row[2],
        "exercise_type": row[3],
        "start_time": row[4],
        "end_time": row[5],
        "total_reps": row[6],
        "avg_form_score": row[7],
        "status": row[8],
    }


# --- Data Ingestion ---

@app.post("/api/sessions/{session_id}/pose")
async def submit_pose_data(session_id: str, frame: PoseFrame):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO pose_data (session_id, timestamp, exercise_type, rep_count, form_score, angles_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, frame.timestamp, frame.exercise_type,
         frame.rep_count, frame.form_score, json.dumps(frame.angles))
    )
    conn.commit()
    conn.close()

    # Broadcast via WebSocket
    await ws_manager.broadcast_to_session(session_id, {
        "type": "pose_frame",
        "session_id": session_id,
        "timestamp": frame.timestamp,
        "exercise_type": frame.exercise_type,
        "rep_count": frame.rep_count,
        "form_score": frame.form_score,
    })

    return {"status": "stored"}


@app.post("/api/sessions/{session_id}/sensor")
async def submit_sensor_data(session_id: str, reading: SensorReading):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sensor_data (session_id, timestamp, heart_rate, hr_confidence, "
        "movement_intensity, body_angle, accel_x, accel_y, accel_z) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, reading.timestamp, reading.heart_rate, reading.hr_confidence,
         reading.movement_intensity, reading.body_angle,
         reading.accel_x, reading.accel_y, reading.accel_z)
    )
    conn.commit()
    conn.close()
    return {"status": "stored"}


@app.post("/api/sessions/{session_id}/feedback")
async def submit_feedback(session_id: str, feedback: FeedbackMessage):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO feedback_log (session_id, timestamp, severity, message) "
        "VALUES (?, ?, ?, ?)",
        (session_id, time.time(), feedback.severity, feedback.message)
    )
    conn.commit()
    conn.close()

    await ws_manager.broadcast_to_session(session_id, {
        "type": "feedback",
        "severity": feedback.severity,
        "message": feedback.message,
        "timestamp": time.time(),
    })

    return {"status": "logged"}


# --- Analytics ---

@app.get("/api/sessions/{session_id}/analytics")
async def get_session_analytics(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT avg(form_score), count(*) FROM pose_data WHERE session_id=?",
              (session_id,))
    avg_score, total_frames = c.fetchone() or (0, 0)

    c.execute("SELECT avg(heart_rate), max(heart_rate), min(heart_rate) "
              "FROM sensor_data WHERE session_id=?", (session_id,))
    hr_avg, hr_max, hr_min = c.fetchone() or (0, 0, 0)

    c.execute("SELECT avg(form_score) FROM pose_data WHERE session_id=? "
              "AND form_score > 0", (session_id,))
    valid_avg_score = c.fetchone()[0] or 0

    c.execute("SELECT count(DISTINCT rep_count) FROM pose_data WHERE session_id=?",
              (session_id,))
    total_reps = c.fetchone()[0] or 0

    conn.close()

    return {
        "session_id": session_id,
        "total_frames": total_frames,
        "total_reps": total_reps,
        "avg_form_score": round(valid_avg_score, 1),
        "heart_rate": {
            "avg": round(hr_avg, 1) if hr_avg else 0,
            "max": round(hr_max, 1) if hr_max else 0,
            "min": round(hr_min, 1) if hr_min else 0,
        }
    }


# --- WebSocket ---

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await ws_manager.connect(websocket, session_id)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
            "timestamp": time.time()
        })

        while True:
            data = await websocket.receive_text()
            # Echo received commands
            await websocket.send_json({
                "type": "ack",
                "data": data,
                "timestamp": time.time()
            })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ---------- MQTT Data Bridge ----------

@app.post("/api/mqtt/sensor")
async def mqtt_sensor_bridge(data: dict):
    """Bridge for ESP32 sensor data received via MQTT."""
    session_id = data.get("session_id", "unknown")
    reading = SensorReading(
        timestamp=data.get("timestamp", time.time()),
        heart_rate=data.get("hr_bpm"),
        hr_confidence=data.get("hr_conf"),
        movement_intensity=data.get("movement"),
        body_angle=data.get("body_angle"),
    )
    return await submit_sensor_data(session_id, reading)


# ---------- Main Entry ----------

# Mount Sprint 1/2 routes (auth/devices/training/vision/ws/ai)
try:
    import main_v2_routes  # noqa: F401
    print("[Backend] main_v2_routes loaded (auth/v2/training/vision/ws/ai)")
except Exception as e:
    print(f"[Backend] main_v2_routes load failed: {e}")

if __name__ == "__main__":
    print("[Backend] Starting Smart Fitness API server...")
    print("[Backend] API docs available at http://localhost:8000/docs")
    print("[Backend] MQTT broker expected at localhost:1883")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
