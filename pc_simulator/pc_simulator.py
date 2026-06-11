"""
pc_simulator.py — PC Camera Fitness Simulator
==============================================
Streams pose data from a webcam to an MQTT broker, mimicking an ESP32 device.

Usage:
    python pc_simulator.py [--camera 0] [--broker localhost:1883]
                           [--device-id pc-sim-001] [--server-url http://host:8080]
                           [--fps 10] [--show-preview]
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

# Allow importing PoseEngine from the sibling ai_vision package
sys.path.insert(0, str(Path(__file__).parent.parent / "ai_vision"))
from pose_engine import PoseEngine, SKELETON_CONNECTIONS, _validate_pose_for_exercise  # noqa: E402

try:
    import paho.mqtt.client as mqtt
    _MQTT_OK = True
except ImportError:
    _MQTT_OK = False

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    import urllib.request as _urllib_req
    _REQUESTS_OK = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pc_simulator")

# ─── Exercise angle thresholds ─────────────────────────────────────────────────

SQUAT_DOWN_ANGLE  = 120   # avg knee < this → "down"
SQUAT_UP_ANGLE    = 160   # avg knee > this → "up"
PUSHUP_DOWN_ANGLE = 100   # avg elbow < this → "down"
PUSHUP_UP_ANGLE   = 150   # avg elbow > this → "up"

# Ideal joint angles for form-score calculation (bottom of each movement)
_SQUAT_IDEAL  = {"left_knee": 90, "right_knee": 90, "left_hip": 80, "right_hip": 80}
_PUSHUP_IDEAL = {"left_elbow": 90, "right_elbow": 90,
                 "left_shoulder": 45, "right_shoulder": 45}


# ─── Exercise tracker ──────────────────────────────────────────────────────────

class ExerciseTracker:
    """Rep-counting state machine with rolling form score.

    Improvements over v1:
      - stance_validation: checks required body landmarks are visible before
        triggering detect or counting a rep.
      - amplitude_threshold: rep is only counted if the angle actually changed
        by more than AMPLITUDE_MIN_DELTA degrees (prevents noise counting).
      - cooldown_frames: after a rep, ignores short dips to prevent double-count.
      - min_visibility: configurable per-exercise landmark visibility floor.
    """

    # Minimum angle change required between up→down→up for a valid rep
    AMPLITUDE_MIN_DELTA = 30.0

    def __init__(self) -> None:
        self.exercise: Optional[str] = None
        self.state = "up"
        self.reps = 0
        self.form_score = 100.0
        self._score_buf: List[float] = []

        # Visible landmarks reference (set by set_landmarks before each update)
        self._landmarks: Optional[List[Dict]] = None

        # Amplitude tracking: store the angle when state last changed
        self._state_change_angle: Optional[float] = None

        # Cooldown to prevent double-count
        self._cooldown_frames = 0
        self._cooldown_max = 10

    def set_landmarks(self, landmarks: Optional[List[Dict]]) -> None:
        """Pass the raw landmark list so the tracker can do visibility checks."""
        self._landmarks = landmarks

    def _validate_stance(self) -> bool:
        """Check that the user's body is positioned properly for the exercise."""
        if self._landmarks is None:
            return True  # can't validate = pass through (backward compat)
        return _validate_pose_for_exercise(
            self._landmarks, exercise=self.exercise, min_visibility=0.5
        )

    def update(self, angles: Dict[str, Optional[float]]) -> None:
        self._detect(angles)
        self._tick_reps(angles)
        score = self._score_form(angles)
        if score is not None:
            self._score_buf.append(score)
            if len(self._score_buf) > 30:
                self._score_buf.pop(0)
            self.form_score = float(np.mean(self._score_buf))

        # Cooldown decay
        if self._cooldown_frames > 0:
            self._cooldown_frames -= 1

    def _detect(self, angles: Dict[str, Optional[float]]) -> None:
        if not self._validate_stance():
            return  # don't even try to detect if body isn't visible
        lk = angles.get("left_knee")
        rk = angles.get("right_knee")
        le = angles.get("left_elbow")
        re = angles.get("right_elbow")
        if lk is not None and rk is not None and (lk + rk) / 2 < SQUAT_DOWN_ANGLE:
            self.exercise = "squat"
        elif le is not None and re is not None and (le + re) / 2 < PUSHUP_DOWN_ANGLE:
            self.exercise = "pushup"

    def _tick_reps(self, angles: Dict[str, Optional[float]]) -> None:
        if self._cooldown_frames > 0:
            return
        if not self._validate_stance():
            return  # body not fully visible = skip rep counting

        if self.exercise == "squat":
            lk = angles.get("left_knee")
            rk = angles.get("right_knee")
            if lk is None or rk is None:
                return
            avg = (lk + rk) / 2
            if self.state == "up" and avg < SQUAT_DOWN_ANGLE:
                # Check amplitude if we have a baseline
                if self._state_change_angle is not None:
                    delta = self._state_change_angle - avg
                    if delta < self.AMPLITUDE_MIN_DELTA:
                        return  # not enough movement
                self._state_change_angle = avg
                self.state = "down"
            elif self.state == "down" and avg > SQUAT_UP_ANGLE:
                if self._state_change_angle is not None:
                    delta = avg - self._state_change_angle
                    if delta < self.AMPLITUDE_MIN_DELTA:
                        return
                self._state_change_angle = avg
                self.state = "up"
                self.reps += 1
                self._cooldown_frames = self._cooldown_max
                log.info(f"Squat rep #{self.reps}")

        elif self.exercise == "pushup":
            le = angles.get("left_elbow")
            re = angles.get("right_elbow")
            if le is None or re is None:
                return
            avg = (le + re) / 2
            if self.state == "up" and avg < PUSHUP_DOWN_ANGLE:
                if self._state_change_angle is not None:
                    delta = self._state_change_angle - avg
                    if delta < self.AMPLITUDE_MIN_DELTA:
                        return
                self._state_change_angle = avg
                self.state = "down"
            elif self.state == "down" and avg > PUSHUP_UP_ANGLE:
                if self._state_change_angle is not None:
                    delta = avg - self._state_change_angle
                    if delta < self.AMPLITUDE_MIN_DELTA:
                        return
                self._state_change_angle = avg
                self.state = "up"
                self.reps += 1
                self._cooldown_frames = self._cooldown_max
                log.info(f"Pushup rep #{self.reps}")

    def _score_form(self, angles: Dict[str, Optional[float]]) -> Optional[float]:
        ideal = _SQUAT_IDEAL if self.exercise == "squat" else \
                _PUSHUP_IDEAL if self.exercise == "pushup" else None
        if ideal is None:
            return None
        devs = [abs(angles[k] - v) for k, v in ideal.items() if angles.get(k) is not None]
        if not devs:
            return None
        return max(0.0, 100.0 - float(np.mean(devs)) / 90.0 * 100.0)


# ─── MQTT publisher ────────────────────────────────────────────────────────────

class MQTTPublisher:
    """paho-mqtt wrapper with LWT and graceful disconnect."""

    def __init__(self, host: str, port: int, device_id: str) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.connected = False
        self._client = None

        if not _MQTT_OK:
            log.warning("paho-mqtt not installed — MQTT publishing disabled")
            return

        self._client = mqtt.Client(client_id=device_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.will_set(
            f"fitness/{device_id}/status",
            json.dumps({"device_id": device_id, "status": "offline"}),
            qos=1,
            retain=True,
        )

    def connect(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.connect(self.host, self.port, keepalive=60)
            self._client.loop_start()
            time.sleep(0.3)
            return True
        except Exception as exc:
            log.error(f"MQTT connect failed: {exc}")
            return False

    def _on_connect(self, client, userdata, flags, rc) -> None:
        self.connected = rc == 0
        if self.connected:
            log.info(f"MQTT connected → {self.host}:{self.port}")
        else:
            log.warning(f"MQTT connect refused rc={rc}")

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.connected = False
        if rc != 0:
            log.warning(f"MQTT unexpected disconnect rc={rc}")

    def publish(self, topic: str, payload: dict,
                qos: int = 0, retain: bool = False) -> bool:
        if not self._client:
            return False
        try:
            self._client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
            return True
        except Exception as exc:
            log.error(f"MQTT publish error: {exc}")
            return False

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass


# ─── HTTP helper ───────────────────────────────────────────────────────────────

def _http_post(url: str, payload: dict, timeout: int = 5) -> Optional[dict]:
    """POST JSON; returns parsed response dict or None."""
    try:
        if _REQUESTS_OK:
            r = _requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        else:
            data = json.dumps(payload).encode()
            req = _urllib_req.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with _urllib_req.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
    except Exception as exc:
        log.warning(f"HTTP POST {url} → {exc}")
        return None


# ─── Drawing helpers ───────────────────────────────────────────────────────────

def draw_skeleton(frame: np.ndarray, landmarks: list) -> None:
    """Draw SKELETON_CONNECTIONS lines and joint dots from landmark dicts."""
    lm_map = {lm["id"]: lm for lm in landmarks}
    for a, b in SKELETON_CONNECTIONS:
        p1 = lm_map.get(a)
        p2 = lm_map.get(b)
        if p1 and p2 and p1["visibility"] > 0.5 and p2["visibility"] > 0.5:
            cv2.line(frame,
                     (p1["pixel_x"], p1["pixel_y"]),
                     (p2["pixel_x"], p2["pixel_y"]),
                     (0, 255, 128), 2)
    for lm in landmarks:
        if lm["visibility"] > 0.5:
            cv2.circle(frame, (lm["pixel_x"], lm["pixel_y"]), 4, (255, 128, 0), -1)


def draw_hud(frame: np.ndarray, fps: float, tracker: ExerciseTracker) -> None:
    """Semi-transparent HUD: FPS, exercise type, state, reps, form score."""
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (275, 135), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
    lines = [
        f"FPS:      {fps:.1f}",
        f"Exercise: {tracker.exercise or 'detecting...'}",
        f"State:    {tracker.state}",
        f"Reps:     {tracker.reps}",
        f"Form:     {tracker.form_score:.0f}/100",
    ]
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (8, 22 + i * 23),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PC Camera Fitness Simulator")
    p.add_argument("--camera",       type=int,   default=0,
                   help="Camera index (default: 0)")
    p.add_argument("--broker",       default="localhost:1883",
                   help="MQTT broker host:port (default: localhost:1883)")
    p.add_argument("--device-id",    default="pc-sim-001",
                   help="Device identifier published in MQTT topics")
    p.add_argument("--server-url",   default="",
                   help="Backend REST URL for session start/end (optional)")
    p.add_argument("--fps",          type=float, default=30.0,
                   help="Target capture/publish rate in Hz (default: 30, 0=unlimited)")
    p.add_argument("--show-preview", action="store_true",
                   help="Open a window showing skeleton overlay and HUD")
    return p.parse_args()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    parts = args.broker.rsplit(":", 1)
    broker_host = parts[0]
    broker_port = int(parts[1]) if len(parts) == 2 else 1883

    device_id  = args.device_id
    session_id = str(uuid.uuid4())

    log.info("=== PC Fitness Simulator ===")
    log.info(f"  device_id  : {device_id}")
    log.info(f"  session_id : {session_id}")
    log.info(f"  broker     : {broker_host}:{broker_port}")
    log.info(f"  server_url : {args.server_url or '(none)'}")
    log.info(f"  fps        : {args.fps}")
    log.info(f"  camera     : {args.camera}")
    log.info(f"  preview    : {args.show_preview}")

    # PoseEngine
    try:
        engine = PoseEngine(model_complexity=0, smooth_landmarks=True)
        log.info("PoseEngine initialized")
    except Exception as exc:
        log.error(f"PoseEngine init failed: {exc}")
        sys.exit(1)

    # Camera
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        log.error(f"Cannot open camera {args.camera}")
        sys.exit(1)
    log.info(f"Camera {args.camera} opened")

    # MQTT
    mqtt_pub = MQTTPublisher(broker_host, broker_port, device_id)
    mqtt_pub.connect()

    # REST session start
    server = args.server_url.rstrip("/") if args.server_url else ""
    if server:
        result = _http_post(
            f"{server}/api/sessions/start",
            {"device_id": device_id, "session_id": session_id},
        )
        if result:
            session_id = result.get("session_id", session_id)
            log.info(f"Server assigned session_id: {session_id}")

    tracker      = ExerciseTracker()
    frame_gap    = 1.0 / max(args.fps, 1.0) if args.fps > 0 else 0.0
    start_time   = time.time()
    last_hb_time = time.time()
    running      = True

    # Graceful shutdown on SIGINT / SIGTERM
    def _stop(signum=None, frame=None) -> None:
        nonlocal running
        log.info("Shutdown requested")
        running = False

    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    # Heartbeat thread — publishes to fitness/<id>/status every 30 s
    _hb_stop = threading.Event()

    def _heartbeat() -> None:
        nonlocal last_hb_time
        while not _hb_stop.wait(timeout=1.0):
            if time.time() - last_hb_time >= 30:
                mqtt_pub.publish(
                    f"fitness/{device_id}/status",
                    {
                        "device_id":  device_id,
                        "session_id": session_id,
                        "status":     "online",
                        "timestamp":  time.time(),
                        "uptime":     round(time.time() - start_time, 1),
                        "reps":       tracker.reps,
                        "exercise":   tracker.exercise,
                        "form_score": round(tracker.form_score, 1),
                    },
                    qos=1,
                    retain=True,
                )
                last_hb_time = time.time()

    hb_thread = threading.Thread(target=_heartbeat, daemon=True, name="heartbeat")
    hb_thread.start()

    log.info("Main loop running — press Ctrl+C to stop")

    try:
        while running:
            t0 = time.time()

            ret, frame = cap.read()
            if not ret:
                log.warning("Camera read failed, retrying...")
                time.sleep(0.1)
                continue

            try:
                # process_frame returns (annotated_frame, landmarks | None)
                annotated, landmarks = engine.process_frame(frame)

                if landmarks:
                    angles = engine.calculate_body_angles(landmarks)
                    tracker.set_landmarks(landmarks)
                    tracker.update(angles)

                    mqtt_pub.publish(
                        f"fitness/{device_id}/pose",
                        {
                            "device_id":  device_id,
                            "session_id": session_id,
                            "timestamp":  time.time(),
                            "exercise":   tracker.exercise,
                            "state":      tracker.state,
                            "reps":       tracker.reps,
                            "form_score": round(tracker.form_score, 1),
                            "angles": {
                                k: round(v, 1) if v is not None else None
                                for k, v in angles.items()
                            },
                            "landmarks": [
                                {
                                    "id":         lm["id"],
                                    "x":          round(lm["x"], 4),
                                    "y":          round(lm["y"], 4),
                                    "z":          round(lm["z"], 4),
                                    "visibility": round(lm["visibility"], 3),
                                }
                                for lm in landmarks
                                if lm["visibility"] > 0.3
                            ],
                        },
                    )

                if args.show_preview:
                    display = annotated.copy()
                    if landmarks:
                        draw_skeleton(display, landmarks)
                    draw_hud(display, engine.current_fps, tracker)
                    cv2.imshow("PC Fitness Simulator", display)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):   # q or ESC
                        running = False

            except Exception as exc:
                log.exception(f"Frame processing error: {exc}")
                # single-frame errors do not stop the loop

            # FPS throttle
            elapsed = time.time() - t0
            if elapsed < frame_gap:
                time.sleep(frame_gap - elapsed)

    finally:
        log.info("Shutting down...")
        _hb_stop.set()
        hb_thread.join(timeout=2)

        # Publish offline status
        mqtt_pub.publish(
            f"fitness/{device_id}/status",
            {
                "device_id":       device_id,
                "session_id":      session_id,
                "status":          "offline",
                "timestamp":       time.time(),
                "total_reps":      tracker.reps,
                "final_form_score": round(tracker.form_score, 1),
            },
            qos=1,
            retain=True,
        )

        # REST session end
        if server:
            _http_post(
                f"{server}/api/sessions/{session_id}/end",
                {
                    "session_id":     session_id,
                    "total_reps":     tracker.reps,
                    "avg_form_score": round(tracker.form_score, 1),
                },
            )

        cap.release()
        if args.show_preview:
            cv2.destroyAllWindows()
        mqtt_pub.disconnect()
        engine.close()

        log.info(
            f"Session done — reps: {tracker.reps}, "
            f"form: {tracker.form_score:.0f}/100, "
            f"uptime: {time.time() - start_time:.0f}s"
        )


if __name__ == "__main__":
    main()
