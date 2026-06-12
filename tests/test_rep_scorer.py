"""评分V2 按 rep 评分回归测试.

用合成角度序列驱动 ExerciseDetector + RepScorer,
验证: 标准蹲高分 / 半程蹲深度低分 / 弹簧蹲控制低分 / 站立不稀释.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_vision"))

from rep_scorer import RepScorer  # noqa: E402
from exercise_detector import ExerciseDetector, ExerciseType  # noqa: E402


def drive_squat(scorer, det, bottom_angle, rep_seconds, n_reps, fps=10, lr_diff=0.0):
    """模拟 n 个深蹲: 膝角 170 -> bottom -> 170, 每个 rep_seconds 秒."""
    completed = []
    ts = 0.0
    frames_per_phase = max(2, int(rep_seconds * fps / 2))
    for _ in range(n_reps):
        seq = []
        for i in range(frames_per_phase):  # 下蹲
            seq.append(170 + (bottom_angle - 170) * (i + 1) / frames_per_phase)
        for i in range(frames_per_phase):  # 站起
            seq.append(bottom_angle + (170 - bottom_angle) * (i + 1) / frames_per_phase)
        for knee in seq:
            ts += 1.0 / fps
            angles = {"left_knee": knee - lr_diff / 2, "right_knee": knee + lr_diff / 2,
                      "left_hip": 120.0, "right_hip": 120.0}
            count = det.count_squat(angles)
            rep = scorer.add_frame("squat", angles, count, ts=ts)
            if rep:
                completed.append(rep)
    return completed


def make_pair():
    det = ExerciseDetector()
    det.set_target_exercise(ExerciseType.SQUAT)
    scorer = RepScorer()
    return scorer, det


def test_standard_squat_scores_high():
    scorer, det = make_pair()
    reps = drive_squat(scorer, det, bottom_angle=85, rep_seconds=2.5, n_reps=5)
    assert len(reps) == 5, f"应结算5次, 实际{len(reps)}"
    avg = sum(r["total"] for r in reps) / len(reps)
    assert avg >= 85, f"标准蹲平均分应>=85, 实际{avg}"
    assert all(r["depth"] == 100 for r in reps)


def test_shallow_squat_low_depth():
    scorer, det = make_pair()
    reps = drive_squat(scorer, det, bottom_angle=125, rep_seconds=2.5, n_reps=3)
    assert len(reps) == 3
    assert all(r["depth"] <= 60 for r in reps), [r["depth"] for r in reps]
    assert any("蹲得太浅" in r["feedback"] for r in reps)


def test_bouncy_squat_low_control():
    scorer, det = make_pair()
    reps = drive_squat(scorer, det, bottom_angle=85, rep_seconds=0.5, n_reps=3, fps=30)
    assert len(reps) == 3
    assert all(r["control"] < 70 for r in reps), [r["control"] for r in reps]
    assert any("速度太快" in r["feedback"] for r in reps)


def test_asymmetric_squat_penalized():
    scorer, det = make_pair()
    reps = drive_squat(scorer, det, bottom_angle=85, rep_seconds=2.5, n_reps=3, lr_diff=30)
    assert len(reps) == 3
    assert all(r["symmetry"] < 80 for r in reps)


def test_idle_frames_do_not_dilute():
    """关键回归: 站立帧不参与成绩 — 会话分 = rep 均分."""
    scorer, det = make_pair()
    # 先站 30 秒 (膝角 175, 不会触发计数)
    ts = 0.0
    for _ in range(300):
        ts += 0.1
        angles = {"left_knee": 175.0, "right_knee": 175.0}
        count = det.count_squat(angles)
        scorer.add_frame("squat", angles, count, ts=ts)
    assert scorer.session_avg() is None, "纯站立不应产生成绩"
    # 再做 5 个标准蹲
    reps = drive_squat(scorer, det, bottom_angle=85, rep_seconds=2.5, n_reps=5)
    assert len(reps) == 5
    assert scorer.session_avg() >= 85, "站立时间不应稀释成绩"


def test_exercise_switch_resets():
    scorer, det = make_pair()
    drive_squat(scorer, det, bottom_angle=85, rep_seconds=2.5, n_reps=2)
    assert len(scorer.rep_scores) == 2
    scorer.add_frame("push_up", {"left_elbow": 170.0, "right_elbow": 170.0}, 0, ts=999.0)
    assert scorer.rep_scores == [], "切换动作应重置"
