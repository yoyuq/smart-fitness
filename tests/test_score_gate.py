"""评分可见度门禁回归测试 (修复: 只拍到脸也给满分).

不依赖后端服务, 直接测 ml_pose.pose_engine 的门禁逻辑.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml_pose"))
import pose_engine as pe  # noqa: E402


def test_validity_full_body():
    vis = np.ones(33) * 0.9
    ok, q = pe.check_pose_validity(vis, "squat")
    assert ok
    assert q > 0.8


def test_validity_face_only():
    vis = np.ones(33) * 0.1
    vis[:11] = 0.95  # 只有脸部高可见
    ok, _ = pe.check_pose_validity(vis, "squat")
    assert not ok, "只有脸的帧必须判无效"


def test_validity_per_exercise():
    vis = np.ones(33) * 0.9
    vis[23:29] = 0.2  # 髋/膝/踝不可见
    ok_squat, _ = pe.check_pose_validity(vis, "squat")
    ok_curl, _ = pe.check_pose_validity(vis, "bicep_curl")
    assert not ok_squat, "下肢缺失时深蹲应无效"
    assert ok_curl, "弯举只需上肢可见"


def test_gate_invalid_frame_no_score():
    score, fb = pe.apply_score_gate(100, "标准!", valid=False, quality=0.1)
    assert score is None
    assert "画面" in fb


def test_gate_low_quality_capped():
    score, fb = pe.apply_score_gate(100, "标准!", valid=True, quality=0.55)
    assert score == pe.LOW_QUALITY_CAP
    assert "可见度低" in fb


def test_gate_good_quality_untouched():
    score, fb = pe.apply_score_gate(95, "标准!", valid=True, quality=0.9)
    assert score == 95
    assert fb == "标准!"
