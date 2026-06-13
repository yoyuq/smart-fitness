"""rep 计数低帧率回归测试.

2026-06-13: 真实视频在 2fps 下大量漏计, 根因是 _required_frames=2 在低帧率
(一个动作相位仅 1 帧) 下凑不齐连续帧。改为 1 帧确认后恢复。本测试锁定该行为。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_vision"))
from exercise_detector import ExerciseDetector, ExerciseType  # noqa: E402


def count_squat_seq(angles_seq):
    det = ExerciseDetector()
    det.set_target_exercise(ExerciseType.SQUAT)
    for a in angles_seq:
        det.count_squat({"left_knee": a, "right_knee": a})
    return det.rep_count


def test_low_fps_single_frame_per_phase_counts():
    """每个相位只有 1 帧 (模拟 2fps 快动作): 3 次深蹲应计满 3."""
    # 站(170) -> 蹲(80) -> 站(170) 每相位 1 帧, 重复 3 次
    seq = [170, 80, 170, 80, 170, 80, 170]
    assert count_squat_seq(seq) == 3


def test_threshold_gap_debounces_jitter():
    """角度在 up_threshold 附近抖动但从未达到 down: 不应计数."""
    seq = [170, 152, 168, 155, 172, 151, 169]  # 全程 >150, 从未 <110
    assert count_squat_seq(seq) == 0


def test_partial_rep_not_counted():
    """只蹲到 140 (未过 count_squat 的 down=130): 不计数."""
    seq = [170, 140, 170, 135, 170]
    assert count_squat_seq(seq) == 0


def test_full_cycle_required():
    """蹲下但没站起来 (停在底部): 不计数, 需走完 UP->DOWN->UP."""
    seq = [170, 80, 75, 78, 82]  # 下去就不上来
    assert count_squat_seq(seq) == 0


def test_multiple_reps_dense():
    """高帧率密集采样 5 次深蹲: 状态机循环防重复, 恰好计 5."""
    seq = []
    for _ in range(5):
        seq += [170, 160, 120, 80, 60, 80, 120, 160, 170]
    assert count_squat_seq(seq) == 5
