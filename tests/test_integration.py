"""
test_integration.py - Integration Tests
========================================
Tests the Smart Fitness Guidance System end-to-end:
  - API endpoints
  - Session lifecycle
  - Pose data ingestion
  - Sensor data handling

Framework: pytest
  Source: https://github.com/pytest-dev/pytest (MIT License)

Run: python -m pytest tests/test_integration.py -v
"""

import sys
import os
import json
import time
import unittest
import tempfile

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
# Add ai_vision to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_vision'))

from fastapi.testclient import TestClient
from main import app

sys.path.insert(0, os.path.dirname(__file__))
from conftest import load_ai_vision_pose_engine
PoseEngine = load_ai_vision_pose_engine().PoseEngine
from exercise_detector import ExerciseDetector, ExerciseType
from form_analyzer import FormAnalyzer


client = TestClient(app)


class TestAPIIntegration(unittest.TestCase):
    """Test the backend REST API end-to-end."""

    def setUp(self):
        self.device_id = "test-esp32-001"
        self.session_id = None

    def test_01_health_check(self):
        """Test server health endpoint."""
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("timestamp", data)

    def test_02_register_device(self):
        """Test device registration."""
        resp = client.post("/api/devices/register", json={
            "device_id": self.device_id,
            "name": "Test ESP32",
            "firmware_version": "1.0.0"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "registered")

    def test_03_list_devices(self):
        """Test device listing."""
        resp = client.get("/api/devices")
        self.assertEqual(resp.status_code, 200)
        devices = resp.json()
        device_ids = [d["device_id"] for d in devices]
        self.assertIn(self.device_id, device_ids)

    def test_04_get_device(self):
        """Test get single device."""
        resp = client.get(f"/api/devices/{self.device_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["device_id"], self.device_id)

    def test_05_start_session(self):
        """Test starting an exercise session."""
        resp = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "user_id": "test-user-01",
            "exercise_type": "squat"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("session_id", data)
        self.assertEqual(data["status"], "started")
        self.session_id = data["session_id"]

    def test_06_submit_pose_data(self):
        """Test pose data submission."""
        # First start a session
        sess = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "exercise_type": "squat"
        }).json()
        session_id = sess["session_id"]

        # Submit multiple pose frames
        for i in range(5):
            resp = client.post(f"/api/sessions/{session_id}/pose", json={
                "timestamp": time.time(),
                "exercise_type": "squat",
                "rep_count": i,
                "form_score": 85.0 + i * 2,
                "angles": {
                    "left_knee": 90.0,
                    "right_knee": 95.0,
                    "left_hip": 80.0,
                    "right_hip": 82.0,
                    "left_elbow": 170.0,
                    "right_elbow": 168.0,
                }
            })
            self.assertEqual(resp.status_code, 200)

    def test_07_submit_sensor_data(self):
        """Test sensor data ingestion."""
        sess = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "exercise_type": "squat"
        }).json()
        session_id = sess["session_id"]

        resp = client.post(f"/api/sessions/{session_id}/sensor", json={
            "timestamp": time.time(),
            "heart_rate": 72.5,
            "hr_confidence": 0.85,
            "movement_intensity": 0.45,
            "body_angle": 5.2,
            "accel_x": 0.1,
            "accel_y": 0.2,
            "accel_z": 9.8,
        })
        self.assertEqual(resp.status_code, 200)

    def test_08_submit_feedback(self):
        """Test feedback submission."""
        sess = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "exercise_type": "squat"
        }).json()
        session_id = sess["session_id"]

        resp = client.post(f"/api/sessions/{session_id}/feedback", json={
            "severity": "info",
            "message": "Not enough depth, go lower"
        })
        self.assertEqual(resp.status_code, 200)

    def test_09_session_analytics(self):
        """Test session analytics."""
        # Create session with data
        sess = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "exercise_type": "squat"
        }).json()
        session_id = sess["session_id"]

        # Add pose data
        for i in range(3):
            client.post(f"/api/sessions/{session_id}/pose", json={
                "timestamp": time.time(),
                "exercise_type": "squat",
                "rep_count": i,
                "form_score": 90.0,
                "angles": {"left_knee": 95.0, "right_knee": 90.0}
            })

        # Get analytics
        resp = client.get(f"/api/sessions/{session_id}/analytics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_frames", data)
        self.assertIn("avg_form_score", data)

    def test_10_list_sessions(self):
        """Test session listing."""
        resp = client.get("/api/sessions?limit=10")
        self.assertEqual(resp.status_code, 200)
        sessions = resp.json()
        self.assertGreater(len(sessions), 0)

    def test_11_device_not_found(self):
        """Test 404 for missing device."""
        resp = client.get("/api/devices/nonexistent-device")
        self.assertEqual(resp.status_code, 404)

    def test_12_end_session(self):
        """Test ending a session."""
        sess = client.post("/api/sessions/start", json={
            "device_id": self.device_id,
            "exercise_type": "squat"
        }).json()
        session_id = sess["session_id"]

        resp = client.post(f"/api/sessions/{session_id}/end")
        self.assertEqual(resp.status_code, 200)

        # Verify session status changed
        resp = client.get(f"/api/sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "completed")


class TestPoseIntegration(unittest.TestCase):
    """Test pose estimation engine integration."""

    def setUp(self):
        self.engine = PoseEngine(model_complexity=0)
        self.detector = ExerciseDetector()
        self.analyzer = FormAnalyzer()

    def test_angle_calculation(self):
        """Test angle calculation for key joints."""
        landmarks = [
            {'id': 11, 'x': 0.3, 'y': 0.2, 'z': 0.0, 'visibility': 1.0, 'name': 'left_shoulder'},
            {'id': 13, 'x': 0.3, 'y': 0.4, 'z': 0.0, 'visibility': 1.0, 'name': 'left_elbow'},
            {'id': 15, 'x': 0.3, 'y': 0.6, 'z': 0.0, 'visibility': 1.0, 'name': 'left_wrist'},
        ]
        angle = self.engine.calculate_angle(landmarks, 11, 13, 15)
        self.assertIsNotNone(angle)
        self.assertAlmostEqual(angle, 180.0, delta=1.0)

    def test_body_angles_complete(self):
        """Test that all body angles are calculated."""
        landmarks = []
        for i in range(33):
            landmarks.append({
                'id': i, 'x': 0.5, 'y': 0.1 + i * 0.025,
                'z': 0.0, 'visibility': 0.9,
                'name': f'lm_{i}'
            })
        angles = self.engine.calculate_body_angles(landmarks)
        expected = ['left_knee', 'right_knee', 'left_elbow', 'right_elbow',
                    'left_hip', 'right_hip']
        for key in expected:
            self.assertIn(key, angles)
            self.assertIsNotNone(angles[key])

    def test_squat_detection_cycle(self):
        """Test full squat detection cycle."""
        down = {
            'left_knee': 90.0, 'right_knee': 95.0,
            'left_hip': 75.0, 'right_hip': 80.0,
            'left_elbow': 170.0, 'right_elbow': 168.0,
            'left_shoulder': 85.0, 'right_shoulder': 83.0,
            'left_ankle': 85.0, 'right_ankle': 87.0,
        }
        up = {
            'left_knee': 170.0, 'right_knee': 168.0,
            'left_hip': 95.0, 'right_hip': 93.0,
            'left_elbow': 172.0, 'right_elbow': 170.0,
            'left_shoulder': 88.0, 'right_shoulder': 86.0,
            'left_ankle': 88.0, 'right_ankle': 90.0,
        }

        # Down
        for _ in range(5):
            self.detector.count_squat(down)
        reps = self.detector.count_squat(down)
        self.assertEqual(self.detector.stage.name, "DOWN")

        # Up
        for _ in range(5):
            self.detector.count_squat(up)
        reps = self.detector.count_squat(up)
        self.assertEqual(self.detector.rep_count, 1)

    def test_form_analysis(self):
        """Test form quality analysis."""
        good_form = {
            'left_knee': 90.0, 'right_knee': 95.0,
            'left_hip': 80.0, 'right_hip': 82.0,
            'left_ankle': 85.0, 'right_ankle': 87.0,
        }
        score, feedback = self.analyzer.analyze(good_form, ExerciseType.SQUAT)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


class TestTrainingStateAPI(unittest.TestCase):
    """v2 训练态状态机: /api/v2/training/start|stop|active.

    (替代原 TestExerciseTargetAPI — /api/exercise/target 是 v1 死接口,
    v2 中目标动作随 training/start 与每帧请求携带.)
    """

    DEVICE = "test-train-dev-001"

    def setUp(self):
        self.client = TestClient(app)
        # 清掉可能残留的训练态
        self.client.post("/api/v2/training/stop", json={"device_id": self.DEVICE})

    def tearDown(self):
        self.client.post("/api/v2/training/stop", json={"device_id": self.DEVICE})

    def test_start_training(self):
        r = self.client.post("/api/v2/training/start",
                             json={"device_id": self.DEVICE, "user_id": 1, "exercise": "push_up"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["exercise"], "push_up")
        self.assertTrue(data["session_id"].startswith("sess_1_"))

    def test_active_reflects_state(self):
        self.client.post("/api/v2/training/start",
                         json={"device_id": self.DEVICE, "user_id": 1, "exercise": "squat"})
        r = self.client.get(f"/api/v2/training/active?device_id={self.DEVICE}")
        self.assertEqual(r.status_code, 200)
        active = r.json()["active"]
        self.assertIsNotNone(active)
        self.assertEqual(active["exercise"], "squat")
        self.assertEqual(active["user_id"], 1)

    def test_stop_clears_state(self):
        self.client.post("/api/v2/training/start",
                         json={"device_id": self.DEVICE, "user_id": 1, "exercise": "lunge"})
        r = self.client.post("/api/v2/training/stop", json={"device_id": self.DEVICE})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["stopped"])
        r = self.client.get(f"/api/v2/training/active?device_id={self.DEVICE}")
        self.assertIsNone(r.json()["active"])

    def test_start_requires_device_and_user(self):
        r = self.client.post("/api/v2/training/start", json={"exercise": "squat"})
        self.assertEqual(r.status_code, 400)

    def test_restart_replaces_session(self):
        r1 = self.client.post("/api/v2/training/start",
                              json={"device_id": self.DEVICE, "user_id": 1, "exercise": "squat"})
        time.sleep(1.1)  # session_id 以秒级时间戳区分
        r2 = self.client.post("/api/v2/training/start",
                              json={"device_id": self.DEVICE, "user_id": 2, "exercise": "plank"})
        self.assertNotEqual(r1.json()["session_id"], r2.json()["session_id"])
        active = self.client.get(f"/api/v2/training/active?device_id={self.DEVICE}").json()["active"]
        self.assertEqual(active["user_id"], 2)
        self.assertEqual(active["exercise"], "plank")


class TestPWAEndpoint(unittest.TestCase):
    """Test PWA static file serving."""

    def setUp(self):
        self.client = TestClient(app)

    def test_static_index_served(self):
        """Test that the PWA HTML is served correctly."""
        r = self.client.get("/static/index.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        # Check for key Chinese content
        body = r.text
        self.assertIn("zh-CN", body)
        self.assertIn("\u5f00\u59cb\u8bad\u7ec3", body)  # 开始训练
        self.assertIn("\u7ed3\u675f\u8bad\u7ec3", body)  # 结束训练
        self.assertIn("\u9009\u62e9\u76ee\u6807\u52a8\u4f5c", body)  # 选择目标动作

    def test_app_redirect(self):
        """Test /app redirects to /static/index.html."""
        r = self.client.get("/app", follow_redirects=False)
        self.assertEqual(r.status_code, 307)
        self.assertIn("/static/index.html", r.headers.get("location", ""))

    def test_health_endpoint(self):
        """Test health check."""
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
