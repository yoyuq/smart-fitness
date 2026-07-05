"""Tests for stage1 conflict detection in vision_pipeline."""
import os, sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parent)
sys.path.insert(0, BACKEND)

from fitness_agent.vision_pipeline import detect_stage1_conflicts


def test_angle_gap_flagged():
    features = {"observed_angles_deg": {"knee_left": 90, "knee_right": 92}}
    rule = {"exercise": "squat", "peak_angle_deg": 20.7}
    conflicts = detect_stage1_conflicts(features, rule)
    assert any(c["type"] == "angle_gap" for c in conflicts)
    ag = next(c for c in conflicts if c["type"] == "angle_gap")
    assert ag["trust"] == "rule"
    assert ag["delta_deg"] >= 30


def test_angle_gap_not_flagged_when_close():
    features = {"observed_angles_deg": {"knee_left": 25, "knee_right": 22}}
    rule = {"exercise": "squat", "peak_angle_deg": 20.7}
    conflicts = detect_stage1_conflicts(features, rule)
    assert not any(c["type"] == "angle_gap" for c in conflicts)


def test_lumbar_hyperextension_in_squat_flagged():
    features = {
        "alignment_cues": ["lumbar_hyperextension", "knee_over_toe"],
        "observed_angles_deg": {"knee_left": 30, "knee_right": 30},
    }
    rule = {"exercise": "squat", "peak_angle_deg": 30}
    conflicts = detect_stage1_conflicts(features, rule)
    # squat @ interior 30° = mid-bottom → flexion, not hyperextension
    keys = [c.get("cue") for c in conflicts if c["type"] == "cue_vs_rule_range"]
    assert "lumbar_hyperextension" in keys


def test_lumbar_ok_when_standing():
    features = {
        "alignment_cues": ["lumbar_hyperextension"],
        "observed_angles_deg": {"knee_left": 170, "knee_right": 170},
    }
    rule = {"exercise": "squat", "peak_angle_deg": 170}
    conflicts = detect_stage1_conflicts(features, rule)
    # 170° = standing, hyperextension is possible → no conflict
    assert not any(c.get("cue") == "lumbar_hyperextension" for c in conflicts)


def test_hip_pike_impossible_in_squat():
    features = {"alignment_cues": ["hip_pike"]}
    rule = {"exercise": "squat", "peak_angle_deg": 40}
    conflicts = detect_stage1_conflicts(features, rule)
    assert any(c["type"] == "impossible_cue" and c["cue"] == "hip_pike" for c in conflicts)


def test_hip_sag_impossible_in_squat():
    features = {"alignment_cues": ["hip_sag"]}
    rule = {"exercise": "squat", "peak_angle_deg": 40}
    conflicts = detect_stage1_conflicts(features, rule)
    assert any(c["type"] == "impossible_cue" and c["cue"] == "hip_sag" for c in conflicts)


def test_hip_pike_ok_in_pushup():
    features = {"alignment_cues": ["hip_pike"]}
    rule = {"exercise": "push_up", "peak_angle_deg": 85}
    conflicts = detect_stage1_conflicts(features, rule)
    assert not any(c["type"] == "impossible_cue" and c["cue"] == "hip_pike" for c in conflicts)


def test_empty_inputs_return_empty_list():
    assert detect_stage1_conflicts({}, None) == []
    assert detect_stage1_conflicts({}, {}) == []


def test_no_rule_summary_still_detects_impossible_cue():
    features = {"alignment_cues": ["hip_pike"], "exercise_visible": "squat"}
    conflicts = detect_stage1_conflicts(features, None)
    assert any(c["type"] == "impossible_cue" and c["cue"] == "hip_pike" for c in conflicts)
