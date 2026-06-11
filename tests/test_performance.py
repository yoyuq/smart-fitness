"""
test_performance.py - Performance Benchmarks
=============================================
Framework: pytest-benchmark
  Source: https://github.com/ionelmc/pytest-benchmark (MIT License)

Measures:
  - Pose estimation throughput
  - Exercise detection latency
  - Form analysis speed
  - API response times

Run: python -m pytest tests/test_performance.py -v --benchmark-only
Run: python tests/test_performance.py                    (standalone)
"""

import sys
import os
import time
import json
import unittest

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_vision'))

from conftest import load_ai_vision_pose_engine
PoseEngine = load_ai_vision_pose_engine().PoseEngine
from exercise_detector import ExerciseDetector, ExerciseType
from form_analyzer import FormAnalyzer

# Simulated landmarks for benchmarking
SAMPLE_LANDMARKS = []
for i in range(33):
    SAMPLE_LANDMARKS.append({
        'id': i,
        'x': 0.3 + (i * 0.01),
        'y': 0.2 + (i * 0.02),
        'z': 0.0,
        'visibility': 0.9,
        'name': f'lm_{i}'
    })

SAMPLE_ANGLES = {
    'left_knee': 90.0, 'right_knee': 95.0,
    'left_hip': 75.0, 'right_hip': 80.0,
    'left_elbow': 170.0, 'right_elbow': 168.0,
    'left_shoulder': 85.0, 'right_shoulder': 83.0,
    'left_ankle': 85.0, 'right_ankle': 87.0,
}


class TestPerformance(unittest.TestCase):
    """Performance benchmarks for core modules."""

    def setUp(self):
        self.engine = PoseEngine(model_complexity=0)
        self.detector = ExerciseDetector()
        self.analyzer = FormAnalyzer()

    def test_01_angle_calculation_throughput(self):
        """Benchmark angle calculation speed."""
        count = 1000
        start = time.perf_counter()

        for _ in range(count):
            angle = self.engine.calculate_angle(SAMPLE_LANDMARKS, 23, 25, 27)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Angle calculation: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 10000, "Too slow!")

    def test_02_body_angles_throughput(self):
        """Benchmark full body angle calculation."""
        count = 1000
        start = time.perf_counter()

        for _ in range(count):
            angles = self.engine.calculate_body_angles(SAMPLE_LANDMARKS)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Body angles: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 5000)

    def test_03_symmetry_throughput(self):
        """Benchmark body symmetry calculation."""
        count = 1000
        start = time.perf_counter()

        for _ in range(count):
            sym = self.engine.calculate_symmetry(SAMPLE_LANDMARKS)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Symmetry: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 5000)

    def test_04_exercise_classification_speed(self):
        """Benchmark exercise classification."""
        count = 1000
        start = time.perf_counter()

        for _ in range(count):
            result = self.detector.classify_exercise(SAMPLE_ANGLES)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Classification: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 20000)

    def test_05_rep_counting_speed(self):
        """Benchmark rep counting with state machine."""
        count = 500
        start = time.perf_counter()

        for i in range(count):
            if i % 10 < 5:
                self.detector.count_squat(SAMPLE_ANGLES)  # Down
            else:
                up_angles = SAMPLE_ANGLES.copy()
                up_angles['left_knee'] = 170.0
                up_angles['right_knee'] = 168.0
                self.detector.count_squat(up_angles)  # Up

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Rep counting: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 20000)

    def test_06_form_analysis_speed(self):
        """Benchmark form quality analysis."""
        count = 1000
        start = time.perf_counter()

        for _ in range(count):
            score, feedback = self.analyzer.analyze(SAMPLE_ANGLES, ExerciseType.SQUAT)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Form analysis: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.3f}ms each)")
        self.assertGreater(ops_per_sec, 10000)

    def test_07_get_landmark_speed(self):
        """Benchmark landmark lookup."""
        count = 10000
        start = time.perf_counter()

        for _ in range(count):
            lm = self.engine.get_landmark(SAMPLE_LANDMARKS, 11)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Landmark lookup: {ops_per_sec:.0f} ops/sec ({elapsed*1000/count:.4f}ms each)")
        self.assertGreater(ops_per_sec, 100000)

    def test_08_end_to_end_latency(self):
        """Simulate full pipeline latency: angles -> classify -> count -> analyze."""
        count = 200
        total_start = time.perf_counter()

        for _ in range(count):
            angles = self.engine.calculate_body_angles(SAMPLE_LANDMARKS)
            exercise = self.detector.classify_exercise(angles)
            self.detector._update_exercise(exercise)
            self.detector.rep_count = self.detector.rep_count + 1  # Simulate count
            score, feedback = self.analyzer.analyze(angles, exercise)

        elapsed = time.perf_counter() - total_start
        per_call = elapsed / count
        print(f"\n[PERF] Full pipeline: {per_call*1000:.2f}ms per frame")
        print(f"[PERF] Estimated FPS: {1/per_call:.1f}")
        self.assertLess(per_call, 0.01)  # Should be under 10ms

    def test_09_large_batch_symmetry(self):
        """Test symmetry with many repeats for stability."""
        count = 5000
        start = time.perf_counter()

        for _ in range(count):
            sym = self.engine.calculate_symmetry(SAMPLE_LANDMARKS)

        elapsed = time.perf_counter() - start
        ops_per_sec = count / elapsed

        print(f"\n[PERF] Large batch symmetry: {ops_per_sec:.0f} ops/sec")
        self.assertGreater(ops_per_sec, 2000)


def run_all():
    """Run all performance tests and print summary."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPerformance)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    run_all()
