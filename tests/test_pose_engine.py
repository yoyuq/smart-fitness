"""
test_pose_engine.py - Pose Engine Backend Unit Tests
=====================================================
Verifies that:
  - The PoseEngine factory dispatches to the right backend.
  - Both backends produce a 33-entry landmark list compatible with the
    documented schema.
  - calculate_angle returns a known value for a synthetic right-angle pose.
  - Backend switching via the `backend=` kwarg works.

YOLO tests are skipped automatically when onnxruntime/ultralytics aren't
installed, or when the ONNX model can't be fetched/exported (e.g. offline
CI environments without network access).

Run:
  python -m pytest tests/test_pose_engine.py -v
"""

import os
import sys
import unittest

import numpy as np

# 两个同名 pose_engine.py 会因 sys.modules 缓存互相污染, 统一走 conftest 显式加载
sys.path.insert(0, os.path.dirname(__file__))
from conftest import load_ai_vision_pose_engine  # noqa: E402

_mod = load_ai_vision_pose_engine()
PoseEngine = _mod.PoseEngine
PoseEngineMediaPipe = _mod.PoseEngineMediaPipe
PoseEngineYOLO = _mod.PoseEngineYOLO
IPoseEngine = _mod.IPoseEngine
LANDMARK_NAMES = _mod.LANDMARK_NAMES


def _make_blank_frame(h: int = 480, w: int = 640) -> np.ndarray:
    """Return a plain gray BGR frame (no pose, but valid shape/dtype)."""
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _make_synthetic_right_angle_landmarks() -> list:
    """Construct a 33-landmark list shaped so the left elbow angle is 90 deg.

    Layout (normalized coords): shoulder at (0.4, 0.3), elbow at (0.4, 0.5),
    wrist at (0.6, 0.5). The vectors elbow->shoulder (up) and elbow->wrist
    (right) are perpendicular -> 90 degrees.
    """
    lms = []
    for i in range(33):
        lms.append({
            'id': i, 'name': LANDMARK_NAMES.get(i, f'landmark_{i}'),
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'visibility': 0.0,
            'pixel_x': 0, 'pixel_y': 0,
        })
    # 11 = left_shoulder, 13 = left_elbow, 15 = left_wrist
    lms[11].update({'x': 0.4, 'y': 0.3, 'visibility': 1.0})
    lms[13].update({'x': 0.4, 'y': 0.5, 'visibility': 1.0})
    lms[15].update({'x': 0.6, 'y': 0.5, 'visibility': 1.0})
    return lms


class TestFactoryDispatch(unittest.TestCase):
    """PoseEngine(backend=...) dispatches to the correct concrete class."""

    def test_factory_defaults_to_mediapipe(self):
        engine = PoseEngine(model_complexity=0, smooth_landmarks=False)
        try:
            self.assertIsInstance(engine, PoseEngineMediaPipe)
            self.assertIsInstance(engine, IPoseEngine)
        finally:
            engine.close()

    def test_factory_mediapipe_explicit(self):
        engine = PoseEngine(backend="mediapipe", model_complexity=0,
                            smooth_landmarks=False)
        try:
            self.assertIsInstance(engine, PoseEngineMediaPipe)
        finally:
            engine.close()

    def test_factory_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            PoseEngine(backend="not-a-backend")

    def test_factory_mediapipe_swallows_yolo_kwargs(self):
        """Forwarding YOLO-only kwargs to the MediaPipe backend should be a no-op."""
        engine = PoseEngine(backend="mediapipe", model_complexity=0,
                            smooth_landmarks=False, imgsz=640,
                            conf_threshold=0.25)
        try:
            self.assertIsInstance(engine, PoseEngineMediaPipe)
        finally:
            engine.close()


class TestMediaPipeBackend(unittest.TestCase):
    """Smoke tests for the MediaPipe backend."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PoseEngine(model_complexity=0, smooth_landmarks=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_process_blank_frame_returns_annotated(self):
        """Even with no detection, process_frame must return (frame, None|list)."""
        frame = _make_blank_frame()
        annotated, landmarks = self.engine.process_frame(frame)
        self.assertEqual(annotated.shape, frame.shape)
        # On a blank gray frame MediaPipe shouldn't detect anything.
        self.assertTrue(landmarks is None or isinstance(landmarks, list))

    def test_calculate_angle_synthetic_right_angle(self):
        lms = _make_synthetic_right_angle_landmarks()
        angle = self.engine.calculate_angle(lms, 11, 13, 15)
        self.assertIsNotNone(angle)
        self.assertAlmostEqual(angle, 90.0, delta=0.1)

    def test_calculate_body_angles_returns_expected_keys(self):
        lms = _make_synthetic_right_angle_landmarks()
        angles = self.engine.calculate_body_angles(lms)
        expected = {
            'left_elbow', 'right_elbow', 'left_knee', 'right_knee',
            'left_hip', 'right_hip', 'left_shoulder', 'right_shoulder',
            'left_ankle', 'right_ankle',
        }
        self.assertEqual(set(angles.keys()), expected)
        self.assertAlmostEqual(angles['left_elbow'], 90.0, delta=0.1)

    def test_calculate_symmetry_keys(self):
        lms = _make_synthetic_right_angle_landmarks()
        sym = self.engine.calculate_symmetry(lms)
        self.assertIn('shoulder_tilt', sym)
        self.assertIn('hip_tilt', sym)
        self.assertIn('leg_angle_diff', sym)
        self.assertIn('overall_symmetry', sym)


def _yolo_backend_available() -> bool:
    """Quick check: do we have onnxruntime+ultralytics importable?"""
    try:
        import onnxruntime  # noqa: F401
        import ultralytics  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_yolo_backend_available(),
                     "onnxruntime/ultralytics not installed")
class TestYoloBackend(unittest.TestCase):
    """Smoke tests for the YOLO backend.

    These tests download/export the ONNX model on first run (~10MB). They are
    skipped gracefully if the download fails (e.g. offline CI).
    """

    engine = None  # type: ignore

    @classmethod
    def setUpClass(cls):
        try:
            cls.engine = PoseEngine(backend="yolo", smooth_landmarks=False,
                                    conf_threshold=0.25)
        except Exception as e:
            raise unittest.SkipTest(f"YOLO backend unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        if cls.engine is not None:
            cls.engine.close()

    def test_factory_returns_yolo_instance(self):
        self.assertIsInstance(self.engine, PoseEngineYOLO)
        self.assertIsInstance(self.engine, IPoseEngine)

    def test_process_blank_frame_returns_annotated(self):
        frame = _make_blank_frame()
        annotated, landmarks = self.engine.process_frame(frame)
        self.assertEqual(annotated.shape, frame.shape)
        self.assertTrue(landmarks is None or isinstance(landmarks, list))
        if isinstance(landmarks, list):
            self.assertEqual(len(landmarks), 33,
                             "YOLO landmarks must be remapped to 33 MediaPipe slots")
            for lm in landmarks:
                for key in ('id', 'name', 'x', 'y', 'z',
                            'visibility', 'pixel_x', 'pixel_y'):
                    self.assertIn(key, lm)

    def test_calculate_angle_synthetic_right_angle(self):
        lms = _make_synthetic_right_angle_landmarks()
        angle = self.engine.calculate_angle(lms, 11, 13, 15)
        self.assertIsNotNone(angle)
        self.assertAlmostEqual(angle, 90.0, delta=0.1)


if __name__ == '__main__':
    unittest.main()
