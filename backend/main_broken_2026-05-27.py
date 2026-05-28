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


@asynccontextmanager
async def lifespan(app):
    init_db()
    auth.init_auth_db()
    start_mqtt()
    log.info("Server started - MQTT handler active")
    yield
    mqtt_handler.disconnect()
    log.info("Server shutdown complete")


app = FastAPI(
    title="Smart Fitness Guidance System API",
    description="AI-powered fitness coaching backend with IoT sensor integration",
    version="1.0.0",
    lifespan=lifespan,
)

# F-06 Prometheus 鐩戞帶 (鍙€氳繃 FITNESS_METRICS_ENABLED=0 鍏抽棴)
if os.environ.get("FITNESS_METRICS_ENABLED", "1") == "1":
    try:
        from metrics import MetricsMiddleware, prometheus_text, metrics as _fitness_metrics
        from fastapi.responses import Response as _MetricResponse
        app.add_middleware(MetricsMiddleware)

        @app.get("/metrics")
        async def _prometheus_endpoint():
            """Prometheus exposition (text format v0.0.4)."""
            # 鍔ㄦ€?gauges
            try:
                _conn = get_db()
                for tbl in ("pose_data", "user_exercise_log", "user_body_metrics",
                             "daily_summary", "device_user_binding", "users"):
                    try:
                        cnt = _conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                        _fitness_metrics.set_gauge("fitness_table_rows", cnt,
                                                    labels={"table": tbl},
                                                    help_text="鍚勬牳蹇冭〃褰撳墠琛屾暟")
                    except Exception:
                        pass
            except Exception:
                pass
            return _MetricResponse(content=prometheus_text(),
                                   media_type="text/plain; version=0.0.4; charset=utf-8")

        log.info("F-06 Prometheus metrics enabled at /metrics")
    except Exception as _me:
        log.warning(f"F-06 metrics init failed: {_me}")

# CORS: allow origins from env, default to * for development
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Database Setup ----------

DB_PATH = os.path.join(os.path.dirname(__file__), "fitness.db")

# Thread-safe connection pool: each thread gets its own reusable connection
_db_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Get a thread-local database connection (reused per thread)."""
    if not hasattr(_db_local, 'conn') or _db_local.conn is None:
        _db_local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_local.conn.row_factory = sqlite3.Row
        _db_local.conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads
        _db_local.conn.execute("PRAGMA synchronous=NORMAL")  # safe + faster than FULL
    return _db_local.conn


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
    pass  # auto-fixed
        CREATE TABLE IF NOT EXISTS user_body_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            weight_kg REAL,
            height_cm REAL,
            body_fat_pct REAL,
            resting_hr INTEGER,
            notes TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    pass  # auto-fixed
        CREATE TABLE IF NOT EXISTS user_exercise_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT,
            exercise_type TEXT NOT NULL,
            reps INTEGER DEFAULT 0,
            sets INTEGER DEFAULT 1,
            duration_seconds REAL DEFAULT 0,
            avg_form_score REAL,
            calories_kcal REAL,
            performed_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    pass  # auto-fixed
        CREATE TABLE IF NOT EXISTS device_user_binding (
            device_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            token TEXT,
            bound_at REAL NOT NULL DEFAULT (julianday('now')),
            last_used_at REAL,
            active INTEGER DEFAULT 1,
            PRIMARY KEY (device_id, user_id),
            FOREIGN KEY (device_id) REFERENCES devices(device_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    pass  # auto-fixed
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_reps INTEGER DEFAULT 0,
            total_sets INTEGER DEFAULT 0,
            total_duration_sec REAL DEFAULT 0,
            avg_form_score REAL,
            total_calories REAL DEFAULT 0,
            exercises_done INTEGER DEFAULT 0,
            sessions_count INTEGER DEFAULT 0,
            updated_at REAL NOT NULL,
            UNIQUE(user_id, date),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_daily_summary_user_date ON daily_summary(user_id, date DESC)")

    conn.commit()
    conn.close()


# ---------- MQTT Client ----------

mqtt_handler = MQTTClientHandler()
mqtt_thread = None


def start_mqtt():
    global mqtt_thread
    mqtt_handler.connect()
    mqtt_thread = threading.Thread(target=mqtt_handler.loop_forever, daemon=True)
    mqtt_thread.start()


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
        self.user_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: Optional[str] = None, user_id: Optional[str] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if session_id:
            if session_id not in self.session_connections:
                self.session_connections[session_id] = []
            self.session_connections[session_id].append(websocket)
        if user_id:
            uid = str(user_id)
            if uid not in self.user_connections:
                self.user_connections[uid] = []
            self.user_connections[uid].append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        for sid in list(self.session_connections.keys()):
            if websocket in self.session_connections[sid]:
                self.session_connections[sid].remove(websocket)
        for uid in list(self.user_connections.keys()):
            if websocket in self.user_connections[uid]:
                self.user_connections[uid].remove(websocket)

    async def broadcast_to_session(self, session_id: str, message: dict):
        if session_id in self.session_connections:
            for ws in self.session_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def broadcast_to_user(self, user_id, message: dict):
        uid = str(user_id)
        if uid in self.user_connections:
            for ws in self.user_connections[uid]:
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


# ========== Active Training Control (璁惧寮€濮?鍋滄鎺у埗) ==========
# APP 涓婄偣"寮€濮嬭缁?鍚庡湪杩欓噷鍐欎竴鏉★紱ESP32 涓嬩竴娆?infer 鍝嶅簲閲屼細鐪嬪埌 paused=false + exercise_hint
# 鍋滄鍚庯細ESP32 鐪嬪埌 paused=true + next_interval_ms=5000锛岃繘浣庨寰呮満
_active_training: Dict[str, Dict[str, Any]] = {}
_active_training_lock = threading.Lock()


def _get_active_for_device(device_id: str) -> Optional[Dict[str, Any]]:
    with _active_training_lock:
        rec = _active_training.get(device_id)
        if not rec:
            return None
        # 瓒呰繃 30 鍒嗛挓鑷姩杩囨湡锛岄槻姝?APP 宕╀簡璁惧涓€鐩存媿
        if time.time() - rec.get("started_at", 0) > 1800:
            _active_training.pop(device_id, None)
            return None
        return dict(rec)


def _set_active_for_device(device_id: str, user_id: int, exercise: str, session_id: str) -> Dict[str, Any]:
    with _active_training_lock:
        rec = {
            "user_id": user_id,
            "exercise": exercise,
            "session_id": session_id,
            "started_at": time.time(),
        }
        _active_training[device_id] = rec
        return dict(rec)


def _clear_active_for_device(device_id: str) -> bool:
    with _active_training_lock:
        return _active_training.pop(device_id, None) is not None


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


@app.get("/app")
async def redirect_to_app():
    return RedirectResponse(url="/static/index.html")


def get_active_device_count() -> int:
    c = get_db().cursor()
    c.execute("SELECT COUNT(*) FROM devices WHERE status='online' AND last_seen > ?",
              (time.time() - 120,))
    return c.fetchone()[0]


# --- Device Management ---

@app.post("/api/devices/register")
async def register_device(device: DeviceRegister):
    c = get_db().cursor()
    c.execute(
        "INSERT OR REPLACE INTO devices (device_id, name, firmware_version, last_seen, status) "
        "VALUES (?, ?, ?, ?, 'online')",
        (device.device_id, device.name or device.device_id,
         device.firmware_version or "unknown", time.time())
    )
    get_db().commit()
    return {"status": "registered", "device_id": device.device_id}


@app.get("/api/devices")
async def list_devices():
    c = get_db().cursor()
    c.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    rows = c.fetchall()
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
    c = get_db().cursor()
    c.execute("SELECT * FROM devices WHERE device_id=?", (device_id,))
    row = c.fetchone()
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
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (session_id, device_id, user_id, exercise_type, start_time, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (session_id, session.device_id, session.user_id or "anonymous",
         session.exercise_type, time.time())
    )
    conn.commit()
    return {"session_id": session_id, "status": "started"}


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE sessions SET end_time=?, status='completed' WHERE session_id=?",
        (time.time(), session_id)
    )
    if c.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    conn.commit()
    return {"status": "ended", "session_id": session_id}


@app.get("/api/sessions")
async def list_sessions(limit: int = Query(20, ge=1, le=100)):
    c = get_db().cursor()
    c.execute("SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?", (limit,))
    rows = c.fetchall()
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


@app.get("/api/sessions/detail/{session_id}")
async def get_session_detail(session_id: str):
    c = get_db().cursor()
    c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
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
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO pose_data (session_id, timestamp, exercise_type, rep_count, form_score, angles_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, frame.timestamp, frame.exercise_type,
         frame.rep_count, frame.form_score, json.dumps(frame.angles))
    )
    c.execute(
        "UPDATE sessions SET total_reps=?, avg_form_score=("
        "SELECT avg(form_score) FROM pose_data WHERE session_id=? AND form_score > 0"
        ") WHERE session_id=?",
        (frame.rep_count, session_id, session_id)
    )
    conn.commit()

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
    conn = get_db()
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
    return {"status": "stored"}


@app.post("/api/sessions/{session_id}/feedback")
async def submit_feedback(session_id: str, feedback: FeedbackMessage):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO feedback_log (session_id, timestamp, severity, message) "
        "VALUES (?, ?, ?, ?)",
        (session_id, time.time(), feedback.severity, feedback.message)
    )
    conn.commit()

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
    c = get_db().cursor()

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


# ---------- Camera WebSocket (pose estimation) ----------

_shared_pose_engine: Any = None
_camera_engines: Dict[str, Any] = {}  # session_id -> PoseEngine (optimistic)

# B-02: 鎸?session/device 缂撳瓨 ExerciseDetector锛堢姸鎬佹満璺ㄥ抚锛? FormAnalyzer
_exercise_detectors: Dict[str, Any] = {}  # key=session_id|device_id -> ExerciseDetector
_form_analyzer_shared: Any = None  # 鏃犵姸鎬侊紝鍏ㄥ眬鍏变韩


def _get_exercise_detector(key: str):
    pass  # auto-fixed
    if detector is None or exercise_type is None:
        return 0
    fn_map = {
        ExerciseType.SQUAT: detector.count_squat,
        ExerciseType.PUSH_UP: detector.count_push_up,
        ExerciseType.JUMPING_JACK: detector.count_jumping_jack,
        ExerciseType.LUNGE: detector.count_lunge,
        ExerciseType.BICEP_CURL: detector.count_bicep_curl,
        ExerciseType.SHOULDER_PRESS: detector.count_shoulder_press,
    }
    fn = fn_map.get(exercise_type)
    if fn is None:
        return 0
    try:
        return int(fn(angles))
    except Exception as _e:
        log.debug(f"rep count failed for {exercise_type}: {_e}")
        return 0


def _get_camera_engine(session_id: str):
    global _shared_pose_engine
    if _shared_pose_engine is None:
        _shared_pose_engine = _PoseEngine()
    # Use a shared engine for all sessions instead of creating one per session
    return _shared_pose_engine


def _score_from_landmarks(engine, landmarks: list) -> float:
    """Derive a 0-100 form score from landmark visibility and body symmetry."""
    if not landmarks:
        return 0.0
    visible = sum(1 for lm in landmarks if lm.get("visibility", 0) > 0.5)
    visibility_pct = (visible / len(landmarks)) * 100
    symmetry = engine.calculate_symmetry(landmarks)
    penalty = min(symmetry.get("overall_symmetry", 0), 30)
    return round(max(0.0, visibility_pct - penalty), 1)


@app.websocket("/ws/camera/{session_id}")
async def websocket_camera(websocket: WebSocket, session_id: str):
    await websocket.accept()

    if not _POSE_ENGINE_AVAILABLE:
        await websocket.send_json({"error": "PoseEngine not available on this server"})
        await websocket.close()
        return

    engine = _get_camera_engine(session_id)
    loop = asyncio.get_event_loop()

    # Initialise per-session workout tracking
    _session_workouts[session_id] = {
        'start_time': time.time(),
        'exercise_type': 'unknown',
        'last_rep': 0,
        'rep_scores': [],
    }

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            # Skip non-frame messages (session_start, etc.)
            frame_b64 = msg.get("data", "") or msg.get("frame", "")
            if not frame_b64:
                continue

            # Decode base64 JPEG 鈫?numpy BGR frame
            jpg_bytes = base64.b64decode(frame_b64)
            np_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                continue

            # Run MediaPipe in a thread executor so the event loop stays responsive
            _, landmarks = await loop.run_in_executor(None, engine.process_frame, frame)

            if landmarks:
                angles = engine.calculate_body_angles(landmarks)
                # Combined score: FormAnalyzer for exercise-specific, visibility as fallback
                if angles and _exercise_detector:
                    ex_enum = _exercise_detector.classify_exercise(angles)
                    ex_name = ex_enum.value if hasattr(ex_enum, 'value') else str(ex_enum)
                    if ex_name not in ('unknown', 'idle'):
                        fa_score, fa_feedback = _form_analyzer.analyze(angles, ex_enum, 0)
                        score = fa_score
                    else:
                        score = _score_from_landmarks(engine, landmarks)
                else:
                    score = _score_from_landmarks(engine, landmarks)

                exercise_type = 'unknown'
                reps = 0
                if _exercise_detector is not None:
                    with _exercise_lock:
                        exercise_type = _exercise_detector.classify_exercise(angles)
                        # Dispatch rep counting against the persistent exercise
                        # (target if set, else last actively-detected exercise).
                        # classify_exercise returns IDLE between rep extremes
                        # (e.g. standing upright during squats), so dispatching
                        # off it directly would leave the state machine stranded
                        # in DOWN and reps would never increment.
                        target = _exercise_detector.get_target_exercise()
                        if target is not None:
                            count_ex = target
                        elif _exercise_detector.current_exercise.value != 'idle':
                            count_ex = _exercise_detector.current_exercise
                        else:
                            count_ex = exercise_type
                        ex_val = count_ex.value if hasattr(count_ex, 'value') else count_ex
                        count_method = getattr(_exercise_detector, f'count_{ex_val}', None)
                        if count_method:
                            count_method(angles)
                        reps = _exercise_detector.rep_count
                        # Record detection event
                        _exercise_detector.add_detection(time.time())
            else:
                angles = {}
                score = 0.0
                exercise_type = 'unknown'
                reps = 0

            # Per-rep score tracking (init on first frame)
            if session_id not in _session_workouts:
                _session_workouts[session_id] = {
                    'last_rep': 0, 'rep_scores': [],
                    'start_time': time.time(), 'exercise_type': '',
                    'last_voiced_rep': 0,
                }
            wd = _session_workouts[session_id]
            ex_str = exercise_type.value if hasattr(exercise_type, 'value') else exercise_type
            if ex_str != 'unknown':
                wd['exercise_type'] = ex_str
            new_rep_detected = reps > wd['last_rep']
            if new_rep_detected:
                for rep_num in range(wd['last_rep'] + 1, reps + 1):
                    wd['rep_scores'].append({'rep': rep_num, 'score': score, 'time': time.time()})
                wd['last_rep'] = reps
            rep_scores = wd.get('rep_scores', [])
            if new_rep_detected and reps > wd.get('last_voiced_rep', 0):
                wd['last_voiced_rep'] = reps
                ex_for_phrase = wd.get('exercise_type') or ex_str
                vf_category = "praise" if score >= 70 else "correct"
                vf_phrase = voice_coach.pick_phrase(ex_for_phrase, vf_category)
                await websocket.send_json({
                    "type": "voice_feedback",
                    "session_id": session_id,
                    "rep": reps,
                    "phrase": vf_phrase,
                    "timestamp": time.time(),
                })
            await websocket.send_json({
                "type": "pose",
                "session_id": session_id,
                "keypoints": landmarks,
                "angles": angles,
                "score": score,
                "exercise_type": exercise_type.value if hasattr(exercise_type, 'value') else exercise_type,
                "reps": reps,
                "rep_scores": rep_scores,
                "pose_detected": bool(landmarks),
                "timestamp": time.time(),
            })

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        log.warning(f"WebSocket error [{session_id}]: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        # Auto-save workout data
        wdata = _session_workouts.pop(session_id, None)
        if wdata and len(wdata.get('rep_scores', [])) > 0:
            try:
                duration = time.time() - wdata['start_time']
                rep_scores = wdata['rep_scores']
                total_reps = len(rep_scores)
                avg_score = round(sum(r['score'] for r in rep_scores) / total_reps, 1) if total_reps > 0 else 0.0
                conn = get_db()
                conn.execute(
                    "INSERT OR REPLACE INTO sessions (session_id, exercise_type, start_time, end_time, total_reps, avg_form_score, status) VALUES (?,?,?,?,?,?,?)",
                    (session_id, wdata['exercise_type'], wdata['start_time'], time.time(), total_reps, avg_score, 'disconnected')
                )
                conn.commit()
                log.info(f"Workout auto-saved on disconnect: {session_id} ({total_reps} reps, avg={avg_score})")
            except Exception as e:
                log.warning(f"Failed to auto-save workout: {e}")
        # Shared engine is kept alive globally; no per-session cleanup needed


# ---------- Exercise Target ----------

# Global exercise detector singleton
_exercise_detector = None
_form_analyzer = None
_exercise_lock = threading.Lock()
# Per-session rep tracking: session_id -> {last_rep_count, rep_scores: [{rep, score, time}], start_time}
_session_workouts: Dict[str, dict] = {}

if _POSE_ENGINE_AVAILABLE:
    try:
        from exercise_detector import ExerciseDetector
        _exercise_detector = ExerciseDetector()
        _form_analyzer = _LazyFormAnalyzer.get()
        log.info("ExerciseDetector + FormAnalyzer initialized")
        # Pre-warm PoseEngine so first WS connection is instant
        _shared_pose_engine = _PoseEngine()
        log.info("Pre-warm: PoseEngine instance ready (shared engine)")
    except Exception as e:
        log.warning(f"Pre-warm init error: {e}")


class ExerciseTarget(BaseModel):
    exercise_type: str


if _exercise_detector is not None:

    @app.post("/api/exercise/target")
    async def set_target_exercise(data: ExerciseTarget):
        """Set the target exercise type for detection."""
        if not data.exercise_type or not data.exercise_type.strip():
            raise HTTPException(status_code=422, detail="exercise_type is required")
        try:
            with _exercise_lock:
                _exercise_detector.set_target_exercise(data.exercise_type)
        except (ValueError, AttributeError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid exercise_type: {e}")
        log.info(f"Target exercise set to: {data.exercise_type}")
        return {"status": "ok", "exercise_type": data.exercise_type}

    @app.get("/api/exercise/target")
    async def get_target_exercise():
        """Get the current target exercise type."""
        with _exercise_lock:
            current = _exercise_detector.get_target_exercise()
        return {"exercise_type": current} if current else {"exercise_type": None}

    @app.delete("/api/exercise/target")
    async def clear_target_exercise():
        """Clear the target exercise restriction."""
        with _exercise_lock:
            _exercise_detector.clear_target_exercise()
        log.info("Target exercise cleared")
        return {"status": "ok"}


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


# ---------- Workout End / Save ----------

@app.post("/api/workout/end/{session_id}")
async def end_workout(session_id: str):
    """End a workout session, save summary to database."""
    wdata = _session_workouts.pop(session_id, None)
    if not wdata:
        raise HTTPException(status_code=404, detail="No active workout for this session")
    duration = time.time() - wdata['start_time']
    rep_scores = wdata['rep_scores']
    total_reps = len(rep_scores)
    avg_score = round(sum(r['score'] for r in rep_scores) / total_reps, 1) if total_reps > 0 else 0.0
    exercise_type = wdata['exercise_type']

    # Save to DB
    conn = get_db()
    conn.execute(
        "UPDATE sessions SET end_time=?, total_reps=?, avg_form_score=?, status=? WHERE session_id=?",
        (time.time(), total_reps, avg_score, 'completed', session_id)
    )
    if conn.total_changes == 0:
        # No existing session row, insert one
        conn.execute(
            "INSERT INTO sessions (session_id, exercise_type, start_time, end_time, total_reps, avg_form_score, status) VALUES (?,?,?,?,?,?,?)",
            (session_id, exercise_type, wdata['start_time'], time.time(), total_reps, avg_score, 'completed')
        )
    conn.commit()
    log.info(f"Workout saved: {session_id} ({exercise_type}, {total_reps} reps, avg={avg_score})")

    voice_summary = voice_coach.generate_workout_summary(
        exercise_type, total_reps, avg_score, rep_scores
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "exercise_type": exercise_type,
        "total_reps": total_reps,
        "avg_score": avg_score,
        "duration_seconds": round(duration, 1),
        "rep_scores": rep_scores,
        "voice_summary": voice_summary,
    }

# ---------- Auth API ----------


class AuthRegister(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None


class AuthLogin(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def auth_register(data: AuthRegister):
    result = auth.register(data.username, data.password, data.display_name or "")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "娉ㄥ唽澶辫触"))
    return result


@app.post("/api/auth/login")
async def auth_login(data: AuthLogin):
    result = auth.login(data.username, data.password)
    if not result.get("ok"):
        raise HTTPException(status_code=401, detail=result.get("message", "鐧诲綍澶辫触"))
    return result


@app.get("/api/auth/profile")
async def auth_profile(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缂哄皯鎺堟潈浠ょ墝")
    token = authorization[len("Bearer "):].strip()
    info = auth.verify_token(token)
    if not info:
        raise HTTPException(status_code=401, detail="浠ょ墝鏃犳晥鎴栧凡杩囨湡")
    profile = auth.get_user_profile(info["user_id"])
    if not profile:
        raise HTTPException(status_code=404, detail="error")
    return profile


# ---------- Static Files ----------

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
else:
    log.warning(f"Static directory not found, /static will not be served: {_static_dir}")


# ========== V2 API ROUTES ==========
# 澶氱敤鎴枫€丣WT璁よ瘉銆佽缁冭鍒掔鐞?

# ========== V2 API ROUTES (all under /api/v2/) ==========
# 澶氱敤鎴枫€丣WT璁よ瘉銆佽缁冭鍒掔鐞?

@app.post("/api/v2/auth/register")
async def v2_register(req: Request):
    try:
        body = await req.json()
        r = auth.register(body.get("username",""), body.get("password",""), body.get("display_name",""))
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.post("/api/v2/auth/login")
async def v2_login(req: Request):
    try:
        body = await req.json()
        r = auth.login(body.get("username",""), body.get("password",""))
        if not r["ok"]:
            return JSONResponse(r, status_code=401)
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.get("/api/v2/auth/profile")
async def v2_profile(req: Request):
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "message": "error"}
    pass  # auto-fixed
        profile = auth.get_user_profile(user["user_id"])
        if not profile:
            return JSONResponse({"ok": False, "message": "error"}
        return JSONResponse({"ok": True, "user": {
            "id": profile["id"], "username": profile["username"],
            "display_name": profile["display_name"],
            "avatar": profile.get("avatar", ""),
            "created_at": profile["created_at"],
            "last_login": profile.get("last_login")
        }})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@app.post("/api/v2/devices/register")
async def v2_register_device(req: Request):
    try:
        body = await req.json()
        auth_header = req.headers.get("Authorization")
        user = auth.require_auth(auth_header) if auth_header else None
        r = auth.register_device(body.get("device_id", ""), body.get("device_type", "phone"),
                                 body.get("name", ""), user["user_id"] if user else None)
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.get("/api/v2/devices")
async def v2_list_devices(req: Request):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM devices ORDER BY last_seen DESC LIMIT 50"
    ).fetchall()
    return JSONResponse({"devices": [dict(r) for r in rows]})


# ========== D-03 鐢ㄦ埛韬綋鎸囨爣 ==========

@app.post("/api/v2/metrics/body")
async def v2_add_body_metric(req: Request):
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "message": "error"}
    try:
        body = await req.json()
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_body_metrics (user_id, timestamp, weight_kg, height_cm, body_fat_pct, resting_hr, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["user_id"], time.time(),
             body.get("weight_kg"), body.get("height_cm"),
             body.get("body_fat_pct"), body.get("resting_hr"),
             body.get("notes", ""))
        )
        conn.commit()
        return JSONResponse({"ok": True, "id": cur.lastrowid})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.get("/api/v2/metrics/body")
async def v2_list_body_metrics(req: Request, limit: int = 30):
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "message": "error"}
    conn = get_db()
    rows = conn.execute(
        "SELECT id, timestamp, weight_kg, height_cm, body_fat_pct, resting_hr, notes "
        "FROM user_body_metrics WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user["user_id"], min(limit, 365))
    ).fetchall()
    return JSONResponse({"ok": True, "metrics": [dict(r) for r in rows]})


@app.get("/api/v2/metrics/latest")
async def v2_latest_body_metric(req: Request):
    """."""summary."""."""."""
    import datetime as _dt
    try:
        d = _dt.datetime.strptime(date_str, "%Y-%m-%d")
    except Exception as e:
        return {"error": f"invalid date: {e}"}
    start_ts = d.timestamp()
    end_ts = start_ts + 86400
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(reps),0) AS reps, COALESCE(SUM(sets),0) AS sets, "
        "COALESCE(SUM(duration_seconds),0) AS dur, AVG(avg_form_score) AS form, "
        "COALESCE(SUM(calories_kcal),0) AS cal, COUNT(DISTINCT exercise_type) AS ex_cnt, "
        "COUNT(DISTINCT session_id) AS sess_cnt "
        "FROM user_exercise_log WHERE user_id=? AND performed_at>=? AND performed_at<?",
        (user_id, start_ts, end_ts)
    ).fetchone()
    payload = {
        "user_id": user_id,
        "date": date_str,
        "total_reps": int(row["reps"] or 0) if row else 0,
        "total_sets": int(row["sets"] or 0) if row else 0,
        "total_duration_sec": float(row["dur"] or 0) if row else 0.0,
        "avg_form_score": round(float(row["form"]), 1) if row and row["form"] is not None else None,
        "total_calories": float(row["cal"] or 0) if row else 0.0,
        "exercises_done": int(row["ex_cnt"] or 0) if row else 0,
        "sessions_count": int(row["sess_cnt"] or 0) if row else 0,
    }
    conn.execute(
        "INSERT INTO daily_summary (user_id, date, total_reps, total_sets, total_duration_sec, "
        "avg_form_score, total_calories, exercises_done, sessions_count, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id, date) DO UPDATE SET "
        "total_reps=excluded.total_reps, total_sets=excluded.total_sets, "
        "total_duration_sec=excluded.total_duration_sec, avg_form_score=excluded.avg_form_score, "
        "total_calories=excluded.total_calories, exercises_done=excluded.exercises_done, "
        "sessions_count=excluded.sessions_count, updated_at=excluded.updated_at",
        (user_id, date_str, payload["total_reps"], payload["total_sets"],
         payload["total_duration_sec"], payload["avg_form_score"],
         payload["total_calories"], payload["exercises_done"],
         payload["sessions_count"], time.time())
    )
    conn.commit()
    return payload


@app.post("/api/v2/summary/recompute")
async def v2_recompute_summary(req: Request):
    """."""."""."""."""."""."""灏嗘煇涓?device_id 缁戝埌褰撳墠鐧诲綍鐢ㄦ埛锛岃繑鍥炴柊鐢熸垚鐨?device token銆?
    ESP32 鍚庣画 POST 鐢?X-Device-Token 鍙嶆煡 user_id銆?""
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "error": "error"}, status_code=400)
    try:
        body = await req.json()
        device_id = (body.get("device_id") or "").strip()
        if not device_id:
            return JSONResponse({"ok": False, "message": "device_id required"}, status_code=400)
        token = _secrets.token_hex(16)
        conn = get_db()
        cur = conn.cursor()
        # 浠ラ槻涓囦竴 device 鏈敞鍐岋紝鍏堟彃鍏?
        cur.execute(
            "INSERT OR IGNORE INTO devices (device_id, name, last_seen, status, device_type, user_id) "
            "VALUES (?, ?, ?, 'offline', 'esp32', ?)",
            (device_id, body.get("name", device_id), time.time(), user["user_id"])
        )
        cur.execute(
            "INSERT INTO device_user_binding (device_id, user_id, token, bound_at, active) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(device_id, user_id) DO UPDATE SET token=excluded.token, active=1, bound_at=excluded.bound_at",
            (device_id, user["user_id"], token, time.time())
        )
        conn.commit()
        return JSONResponse({"ok": True, "device_id": device_id, "token": token,
                             "hint": "ESP32 璇峰湪 X-Device-Token header 鎼哄甫杩欎釜 token"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.get("/api/v2/devices/bindings")
async def v2_list_bindings(req: Request):
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "error": "error"}, status_code=400)
    conn = get_db()
    rows = conn.execute(
        "SELECT device_id, bound_at, last_used_at, active FROM device_user_binding "
        "WHERE user_id=? AND active=1 ORDER BY bound_at DESC",
        (user["user_id"],)
    ).fetchall()
    return JSONResponse({"ok": True, "bindings": [dict(r) for r in rows]})


@app.delete("/api/v2/devices/bind/{device_id}")
async def v2_unbind_device(req: Request, device_id: str):
    auth_header = req.headers.get("Authorization")
    user = auth.require_auth(auth_header)
    if not user:
        return JSONResponse({"ok": False, "error": "error"}, status_code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE device_user_binding SET active=0 WHERE device_id=? AND user_id=?",
        (device_id, user["user_id"])
    )
    conn.commit()
    return JSONResponse({"ok": True, "unbound": device_id, "rows_affected": cur.rowcount})


# ========== D-07 鏁版嵁瀵煎嚭 (鐢ㄦ埛涓绘潈) ==========

from fastapi.responses import PlainTextResponse

@app.get("/api/v2/export/csv")
async def v2_export_csv(req: Request, days: int = 90):
    pass  # auto-fixed
    if not client_ip:
        return True
    now = time.time()
    try:
        conn = get_db()
        cur = conn.cursor()
        row = cur.execute(
            "SELECT count, window_start FROM rate_limits WHERE ip=? AND endpoint=?",
            (client_ip, endpoint)
        ).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO rate_limits (ip, endpoint, count, window_start) VALUES (?, ?, 1, ?)",
                (client_ip, endpoint, now)
            )
            conn.commit()
            return True
        if now - row["window_start"] > _RATE_WINDOW:
            cur.execute(
                "UPDATE rate_limits SET count=1, window_start=? WHERE ip=? AND endpoint=?",
                (now, client_ip, endpoint)
            )
            conn.commit()
            return True
        if row["count"] >= _RATE_MAX:
            return False
        cur.execute(
            "UPDATE rate_limits SET count=count+1 WHERE ip=? AND endpoint=?",
            (client_ip, endpoint)
        )
        conn.commit()
        return True
    except Exception as _e:
        log.warning(f"rate limit check failed: {_e}")
        return True  # fail-open


def _resolve_device_token(token: str):
    pass  # auto-fixed
    if not user_id or not exercise_type:
        return None
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT plan_id, name, exercises FROM workout_plans WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        for r in rows:
            try:
                ex_list = json.loads(r["exercises"] or "[]")
            except Exception:
                continue
            for item in ex_list:
                if (item.get("type") or "").lower() == exercise_type.lower():
                    target_sets = int(item.get("sets", 1))
                    target_reps = int(item.get("reps", 0))
                    total_target = target_sets * target_reps if target_reps else 0
                    progress_pct = round(100 * current_reps / total_target, 1) if total_target else None
                    return {
                        "in_plan": True,
                        "plan_id": r["plan_id"],
                        "plan_name": r["name"],
                        "target_sets": target_sets,
                        "target_reps": target_reps,
                        "target_total": total_target,
                        "current_reps": current_reps,
                        "progress_pct": progress_pct,
                    }
        return {"in_plan": False}
    except Exception as _e:
        log.warning(f"B-04 plan match failed: {_e}")
        return None


def _get_body_context(user_id: int):
    pass  # auto-fixed
    if key not in _active_ws_connections:
        return
    msg = json.dumps(message, ensure_ascii=False)
    dead = []
    for ws in _active_ws_connections[key]:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister_ws(key, ws)


@app.websocket("/api/v2/ws/session/{session_id}")
async def v2_ws_session(websocket: WebSocket, session_id: str):
    pass  # auto-fixed
    try:
        body = await req.json()
        target = body.get("target", "")
        message = body.get("message", {})
        await ws_broadcast(target, message)
        return JSONResponse({"ok": True, "pushed": target})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@app.post("/api/v2/vision/infer")
async def v2_vision_infer(req: Request):
    pass  # auto-fixed
    pass  # auto-fixed
      - status: ok | needs_correction | no_pose | unauthorized | error
      - level: info | warn | bad
      pass  # auto-fixed
      pass  # auto-fixed
    pass  # auto-fixed
    pass  # auto-fixed
    try:
        from auth import verify_token  # 宸插瓨鍦?
    except Exception:
        return None
    h = req.headers.get("Authorization", "") or req.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        return None
    tok = h.split(" ", 1)[1].strip()
    try:
        payload = verify_token(tok)
        if not payload:
            return None
        uid = payload.get("user_id") or payload.get("sub")
        return int(uid) if uid is not None else None
    except Exception:
        return None


@app.post("/api/v2/training/start")
async def v2_training_start(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    device_id = (body.get("device_id") or "").strip()
    exercise  = (body.get("exercise")  or "").strip().lower()
    session_id = (body.get("session_id") or f"sess_{uid}_{int(time.time())}")
    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id required"}, status_code=400)
    rec = _set_active_for_device(device_id, uid, exercise, session_id)
    log.info(f"[TRAINING] start: user={uid} device={device_id} exercise={exercise} session={session_id}")
    return {"ok": True, "active": rec}


@app.post("/api/v2/training/stop")
async def v2_training_stop(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    try:
        body = await req.json()
    except Exception:
        body = {}
    device_id = (body.get("device_id") or "").strip()
    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id required"}, status_code=400)
    removed = _clear_active_for_device(device_id)
    log.info(f"[TRAINING] stop: user={uid} device={device_id} removed={removed}")
    return {"ok": True, "removed": removed}


@app.get("/api/v2/training/active")
async def v2_training_active(req: Request):
    uid = _require_user_id(req)
    with _active_training_lock:
        items = []
        for dev, rec in _active_training.items():
            if uid is not None and rec.get("user_id") != uid:
                continue
            items.append({"device_id": dev, **rec})
    return {"ok": True, "items": items}


# ========== B-08 鎸?user_id 璁㈤槄鐨?WebSocket /ws/coach/{user_id} ==========
@app.websocket("/ws/coach/{user_id}")
async def ws_coach_user(websocket: WebSocket, user_id: str):
    pass  # auto-fixed
    await ws_manager.connect(websocket, user_id=user_id)
    try:
        await websocket.send_json({
            "type": "connected",
            "channel": "coach",
            "user_id": user_id,
            "server_time": time.time(),
        })
        while True:
            # 淇濇寔杩炴帴锛屾敮鎸?ping/pong
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(data) if data else {}
            except Exception:
                msg = {}
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "server_time": time.time()})
            else:
                # echo 浣滀负 keep-alive
                await websocket.send_json({"type": "ack"})
    except WebSocketDisconnect:
        pass
    except Exception as _e:
        log.warning(f"/ws/coach/{user_id} error: {_e}")
    finally:
        ws_manager.disconnect(websocket)


try:
    from routes_v2_backtest import router as backtest_router
    app.include_router(backtest_router)
    log.info("V2 Backtest routes registered")
except Exception as e:
    log.warning(f"V2 Backtest routes not available: {e}")


# ---------- Main Entry ----------



# ========== AI Personal Planner Routes (2026-05-27) ==========

@app.post("/api/v2/ai/daily_summary")
async def v2_ai_daily_summary(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not _AI_PLANNER_AVAILABLE:
        return JSONResponse({"ok": False, "error": "AI planner not installed"}, status_code=501)
    if not _ai_planner.is_available():
        return JSONResponse({"ok": False, "error": "DEEPSEEK_API_KEY not set"}, status_code=503)
    conn = get_db()
    result = _ai_planner.daily_summary(conn, uid)
    return JSONResponse(result)


@app.post("/api/v2/ai/weekly_report")
async def v2_ai_weekly_report(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not _AI_PLANNER_AVAILABLE:
        return JSONResponse({"ok": False, "error": "AI planner not installed"}, status_code=501)
    conn = get_db()
    result = _ai_planner.weekly_report(conn, uid)
    return JSONResponse(result)


@app.post("/api/v2/ai/plan_generate")
async def v2_ai_plan_generate(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not _AI_PLANNER_AVAILABLE:
        return JSONResponse({"ok": False, "error": "AI planner not installed"}, status_code=501)
    try:
        body = await req.json()
    except Exception:
        body = {}
    goal  = (body.get("goal") or "").strip()
    weeks = int(body.get("weeks") or 4)
    if not goal:
        return JSONResponse({"ok": False, "error": "goal required (e.g. '增肌 5kg', '减脂腰围 -3cm')"}, status_code=400)
    weeks = max(1, min(weeks, 12))
    conn = get_db()
    result = _ai_planner.generate_plan(conn, uid, goal, weeks)
    return JSONResponse(result)


@app.post("/api/v2/ai/chat")
async def v2_ai_chat(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not _AI_PLANNER_AVAILABLE:
        return JSONResponse({"ok": False, "error": "AI planner not installed"}, status_code=501)
    try:
        body = await req.json()
    except Exception:
        body = {}
    msg = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not msg:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
    conn = get_db()
    result = _ai_planner.chat(conn, uid, msg, history=history)
    return JSONResponse(result)


@app.post("/api/v2/ai/meal_suggestion")
async def v2_ai_meal_suggestion(req: Request):
    uid = _require_user_id(req)
    if uid is None:
        return JSONResponse({"ok": False, "error": "login required"}, status_code=401)
    if not _AI_PLANNER_AVAILABLE:
        return JSONResponse({"ok": False, "error": "AI planner not installed"}, status_code=501)
    conn = get_db()
    result = _ai_planner.meal_suggestion(conn, uid)
    return JSONResponse(result)


if __name__ == "__main__":
    log.info("Starting Smart Fitness API server...")
    log.info("API docs at http://localhost:8000/docs")
    log.info("MQTT broker expected at localhost:1883")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info"
    )

