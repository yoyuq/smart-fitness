"""Tests for backend/rep_quality_rules.py — evidence-based rep quality classifier."""
import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rep_quality_rules import (
    EVIDENCE_SOURCES,
    evidence_refs,
    score_rep_quality,
    score_rep_quality_rules,
)


def _series(bottom, top=170.0, n=32, torso_max=None, lr_diff=None,
            shoulder_L=None, shoulder_R=None, hip_L=None, hip_R=None,
            ankle_dx=None):
    """Build a synthetic V-shaped angle_series that dips to `bottom` at the midpoint."""
    mid = n // 2
    primary = []
    for i in range(n):
        # linear down then up
        t = 1 - abs(i - mid) / mid
        primary.append(top - (top - bottom) * t)
    series = {"primary": primary}
    if torso_max is not None:
        # simple triangle for torso tilt
        series["torso_tilt"] = [torso_max * (1 - abs(i - mid) / mid) for i in range(n)]
    if lr_diff is not None:
        series["lr_diff"] = [lr_diff] * n
    if shoulder_L is not None:
        series["shoulder_L"] = [shoulder_L] * n
    if shoulder_R is not None:
        series["shoulder_R"] = [shoulder_R] * n
    if hip_L is not None:
        series["hip_L"] = [hip_L] * n
    if hip_R is not None:
        series["hip_R"] = [hip_R] * n
    if ankle_dx is not None:
        series["ankle_dx"] = [ankle_dx] * n
    return series


def test_evidence_sources_have_citations():
    for key, ev in EVIDENCE_SOURCES.items():
        assert ev.get("citation"), f"{key} missing citation"
        assert ev.get("pmid") or ev.get("arxiv"), f"{key} missing pmid/arxiv"


def test_evidence_refs_maps_pmids():
    refs = evidence_refs(["escamilla_2001", "oneill_2024", "jaiswal_2023", "does_not_exist"])
    assert "PMID:11194098" in refs
    assert "PMID:33660588" in refs
    assert "arxiv:2310.07221" in refs
    assert len(refs) == 3  # unknown key silently dropped


def test_squat_parallel_good():
    """Parallel squat (knee interior ~85°): should be labelled good."""
    s = _series(bottom=85, torso_max=30, lr_diff=5,
                shoulder_L=None, shoulder_R=None,
                hip_L=None, hip_R=None)
    r = score_rep_quality_rules("squat", s, rep_row={"duration_s": 3.0})
    assert r is not None
    assert r["label"] == "good", r
    assert r["score"] == 100.0
    assert r["issues"] == []


def test_squat_half_squat_flagged_shallow():
    """125° knee (half squat per O'Neill 2024) → shallow."""
    s = _series(bottom=125, torso_max=30, lr_diff=5)
    r = score_rep_quality_rules("squat", s, rep_row={"duration_s": 3.0})
    assert r["label"] == "shallow"
    # evidence must include Escamilla 2001 and O'Neill 2024
    ev = [i["evidence"] for i in r["issues"] if i["key"] == "shallow"][0]
    assert "PMID:11194098" in ev
    assert "PMID:33660588" in ev


def test_squat_torso_lean_flagged():
    """Torso lean 65° > 55° threshold → torso_lean penalty w/ Schmid/Hebling refs."""
    s = _series(bottom=85, torso_max=65, lr_diff=5)
    r = score_rep_quality_rules("squat", s, rep_row={"duration_s": 3.0})
    keys = {i["key"] for i in r["issues"]}
    assert "torso_lean" in keys
    ev = next(i["evidence"] for i in r["issues"] if i["key"] == "torso_lean")
    assert any("PMID:35449120" == p or "PMID:27015103" == p for p in ev)


def test_squat_kasr_valgus():
    s = _series(bottom=85, torso_max=30, lr_diff=5)
    r = score_rep_quality_rules("squat", s, rep_row={"duration_s": 3.0}, kasr_bottom=0.4)
    keys = {i["key"] for i in r["issues"]}
    assert "knee_valgus" in keys
    ev = next(i["evidence"] for i in r["issues"] if i["key"] == "knee_valgus")
    assert "PMID:27313480" in ev  # Ortiz 2016 KASR


def test_pushup_body_sag_and_flare():
    # hip interior 140° (< 160°) = sag; shoulder at bottom 80° (> 65°) = flare
    n = 32
    mid = n // 2
    top = 170.0
    bottom = 85.0
    primary = [top - (top - bottom) * (1 - abs(i - mid) / mid) for i in range(n)]
    shoulder = [80.0] * n
    hip = [140.0] * n
    series = {"primary": primary,
              "shoulder_L": shoulder, "shoulder_R": shoulder,
              "hip_L": hip, "hip_R": hip}
    r = score_rep_quality_rules("push_up", series, rep_row={"duration_s": 2.5})
    keys = {i["key"] for i in r["issues"]}
    assert "elbow_flare" in keys
    assert "body_sag" in keys


def test_bicep_curl_shoulder_swing():
    n = 32
    mid = n // 2
    top = 170.0
    bottom = 40.0  # curl top interior
    primary = [top - (top - bottom) * (1 - abs(i - mid) / mid) for i in range(n)]
    shoulder = [50.0] * n  # > 30° stable ceiling
    series = {"primary": primary,
              "shoulder_L": shoulder, "shoulder_R": shoulder}
    r = score_rep_quality_rules("bicep_curl", series, rep_row={"duration_s": 2.0})
    keys = {i["key"] for i in r["issues"]}
    assert "shoulder_swing" in keys


def test_jumping_jack_feet_narrow():
    n = 32
    mid = n // 2
    top = 20.0
    bottom = 150.0  # max abduction (direction=max)
    primary = [top + (bottom - top) * (1 - abs(i - mid) / mid) for i in range(n)]
    ankle = [0.6] * n  # < 0.9 min
    series = {"primary": primary, "ankle_dx": ankle}
    r = score_rep_quality_rules("jumping_jack", series, rep_row={"duration_s": 1.0})
    keys = {i["key"] for i in r["issues"]}
    assert "feet_narrow" in keys


def test_score_shim_returns_number():
    s = _series(bottom=85, torso_max=30, lr_diff=5)
    v = score_rep_quality("squat", s, rule_total=88.0,
                          rep_row={"duration_s": 3.0})
    assert isinstance(v, float)
    assert 0 <= v <= 100


def test_score_shim_falls_back_on_unknown():
    v = score_rep_quality("burpee", {}, rule_total=42.0)
    assert v == 42.0
