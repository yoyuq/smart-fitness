"""Unit tests for the two-stage vision pipeline.

Stage 1 (extract) and Stage 2 (reason) each hit an HTTP endpoint; we mock
``requests.post`` so tests are fully offline and deterministic.
"""
import base64
import json
import os
import sqlite3
import sys
import types
from typing import Any, Dict

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fitness_agent import vision_pipeline as vp


# ---------------- helpers ----------------


class FakeResp:
    def __init__(self, status_code: int, body: Dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _stage1_body(features: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "choices": [
            {"message": {"content": json.dumps(features, ensure_ascii=False)}}
        ]
    }


def _stage2_body(analysis: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "choices": [
            {"message": {"content": json.dumps(analysis, ensure_ascii=False)}}
        ]
    }


SAMPLE_FEATURES = {
    "exercise_visible": "squat",
    "movement_phase": "bottom",
    "visible_body_parts": ["head", "torso", "left_leg", "right_leg"],
    "observed_angles_deg": {"knee_left": 28, "knee_right": 30, "hip": 40, "trunk_forward_lean": 42},
    "alignment_cues": ["pelvis_tucked_back (butt_wink)", "knee_over_toe"],
    "symmetry": {"left_right_balance": "balanced", "notes": ""},
    "camera_angle": "side",
    "image_quality": {"blur": "none", "occlusion": [], "lighting": "good"},
    "confidence": 0.85,
}


SAMPLE_ANALYSIS = {
    "exercise_confirmed": "squat",
    "posture_assessment": {
        "strengths": ["双腿对称发力均衡"],
        "issues": [
            {"issue": "蹲太深触发骨盆后翻", "severity": "medium", "evidence": "knee_left=28°<60°", "citation_key": "squat_knee_deep"}
        ]
    },
    "completion_score": {
        "depth": 60, "control": 90, "symmetry": 88, "overall": 78,
        "notes": "视觉与规则一致, 深度分低"
    },
    "guidance": {
        "immediate_next_rep": ["下蹲深度控制在膝角60-70°"],
        "next_session": ["加强踝背屈灵活性"],
        "progression_or_regression": "保持当前重量",
        "cautions": []
    },
    "data_gaps": [],
    "confidence": 0.82,
}


# ---------------- stage 1 tests ----------------


def test_extract_pose_features_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("VOLC_ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("HUNYUAN_API_KEY", raising=False)
    r = vp.extract_pose_features(image_url="https://example.com/x.jpg", exercise="squat")
    assert r["ok"] is False
    assert "no vision provider configured" in r["error"]


def test_extract_pose_features_success_via_url(monkeypatch):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return FakeResp(200, _stage1_body(SAMPLE_FEATURES))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    r = vp.extract_pose_features(image_url="https://cdn.example.com/rep.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["stage"] == "extract"
    assert r["features"]["exercise_visible"] == "squat"
    # ensure the extraction prompt was actually sent
    msg = captured["payload"]["messages"][0]["content"]
    text_parts = [c for c in msg if c.get("type") == "text"]
    assert any("姿态观察员" in p["text"] for p in text_parts)


def test_extract_pose_features_http_error(monkeypatch):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    # ensure no other provider takes priority
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("AI_AGENT_VISION_PROVIDERS", "volcengine")
    monkeypatch.setattr(vp.requests, "post", lambda *a, **kw: FakeResp(503, {"err": "busy"}))
    r = vp.extract_pose_features(image_url="https://cdn.example.com/x.jpg")
    assert r["ok"] is False
    # single provider forced, so all-fail == that one HTTP error
    assert r["error_type"] == "vision_all_providers_failed"
    assert r["recoverable"] is True
    # detail should show the HTTP 503 in the tried list
    assert any(t.get("http") == 503 for t in r["tried"])


def test_extract_pose_features_reads_local_whitelisted_image(monkeypatch, tmp_path):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    # Point the whitelist at our tmp dir
    monkeypatch.setenv("AI_AGENT_VISION_FRAME_ROOTS", str(tmp_path))
    img = tmp_path / "rep.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0FAKEJPG")  # tiny fake bytes; size <4MB
    monkeypatch.setattr(vp.requests, "post", lambda *a, **kw: FakeResp(200, _stage1_body(SAMPLE_FEATURES)))
    r = vp.extract_pose_features(image_path=str(img), exercise="squat")
    assert r["ok"] is True
    assert r["features"]["exercise_visible"] == "squat"


# ---------------- stage 2 tests ----------------


def test_reason_requires_features():
    r = vp.reason_about_pose(features={}, exercise="squat")
    assert r["ok"] is False


def test_reason_success_carries_context(monkeypatch):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp(200, _stage2_body(SAMPLE_ANALYSIS))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    r = vp.reason_about_pose(
        features=SAMPLE_FEATURES,
        exercise="squat",
        rule_summary={"total": 84.0, "depth": 73.9, "feedback": "蹲太深"},
        user_context={"user_id": 31, "body": {"weight_kg": 56.0}, "memory": [{"kind": "injury", "note": "膝盖旧伤"}]},
        evidence_citations=[{"key": "squat_knee_deep", "range": "60-70°", "source": "Escamilla 2001"}],
    )
    assert r["ok"] is True
    assert r["analysis"]["exercise_confirmed"] == "squat"
    payload_str = json.dumps(captured["payload"], ensure_ascii=False)
    # Rule + context + evidence should all be present in the user prompt
    assert "73.9" in payload_str  # rule depth score threaded through
    assert "膝盖旧伤" in payload_str  # injury context threaded through
    assert "squat_knee_deep" in payload_str  # citation key threaded through


def test_reason_llm_http_error(monkeypatch):
    # After migrating stage2 to a provider chain (deepseek -> qwen -> volc), any
    # single HTTP error is retried against the next provider. When *all* providers
    # return an HTTP error, the aggregate error_type flips to llm_all_providers_failed
    # with the tried[] list preserved for the caller.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(vp.requests, "post", lambda *a, **kw: FakeResp(429, {"err": "rate limited"}))
    r = vp.reason_about_pose(features=SAMPLE_FEATURES, exercise="squat")
    assert r["ok"] is False
    assert r["error_type"] == "llm_all_providers_failed"
    tried = r.get("tried") or []
    assert tried and all(t.get("http") == 429 for t in tried)


# ---------------- data-loss mitigation tests ----------------


def test_stage1_parse_failure_still_returns_raw_reply(monkeypatch):
    """Loss 3/4: malformed JSON must not silently zero out the observation."""
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    broken_content = 'The image shows a squat with knee cave-in but I forgot to close JSON: {"exercise_visible":"squat"'
    monkeypatch.setattr(
        vp.requests, "post",
        lambda *a, **kw: FakeResp(200, {"choices": [{"message": {"content": broken_content}, "finish_reason": "length"}]}),
    )
    r = vp.extract_pose_features(image_url="https://x/y.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["parse_status"] == "parse_failed"
    assert "knee cave-in" in r["raw_reply"]


def test_two_stage_falls_back_to_raw_text_when_parse_fails(monkeypatch):
    """Loss 3: two_stage should keep going with raw text rather than abort."""
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")

    def fake_post(url, headers=None, json=None, timeout=None):
        if len(json.get("messages", [])) == 1:
            return FakeResp(200, {"choices": [{"message": {"content": "Freeform: knees are caving in, back rounded"}}]})
        return FakeResp(200, _stage2_body(SAMPLE_ANALYSIS))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    r = vp.two_stage_pose_analysis(image_url="https://x/y.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["stage_extract"]["parse_status"] == "parse_failed"
    # stage-2 was still called with a synthetic features dict carrying raw_vision_text
    assert r["stage_reason"]["ok"] is True


def test_reason_prompt_carries_time_series_and_history(monkeypatch):
    """Loss 5/6: time_series + history must be threaded to stage 2."""
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp(200, _stage2_body(SAMPLE_ANALYSIS))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    r = vp.reason_about_pose(
        features=SAMPLE_FEATURES,
        exercise="squat",
        rule_summary={"total": 82.0},
        time_series={"knee_left": [90, 60, 30, 25, 30, 60, 90], "stats": {"min": 25, "max": 90, "mean": 55}},
        history=[
            {"rep_index": 1, "total": 84.0, "peak_angle": 28.0},
            {"rep_index": 2, "total": 82.9, "peak_angle": 26.0},
        ],
    )
    assert r["ok"] is True
    payload_text = json.dumps(captured["payload"], ensure_ascii=False)
    # time series should be in there
    assert "knee_left" in payload_text
    assert "55" in payload_text  # mean
    # history should be in there
    assert "rep_index" in payload_text
    assert "82.9" in payload_text


def test_extraction_prompt_now_allows_other_observations():
    """Loss 1: schema must have an escape hatch for observations outside the enum."""
    p = vp._extraction_prompt("squat")
    assert "other_observations" in p
    # Prompt should tell model this is a free-text field for unusual findings
    assert "自由描述" in p or "free" in p.lower()


def test_max_tokens_is_configurable(monkeypatch):
    """Loss 4: max_tokens must be bumped (>=1000) and env-configurable."""
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    monkeypatch.setenv("AI_AGENT_VISION_EXTRACT_MAX_TOKENS", "1800")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp(200, _stage1_body(SAMPLE_FEATURES))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    vp.extract_pose_features(image_url="https://x/y.jpg", exercise="squat")
    assert captured["payload"]["max_tokens"] == 1800


# ---------------- two-stage chain tests ----------------


def test_two_stage_chains_success(monkeypatch):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        # The stage-1 call uses a "姿态观察员" prompt, the stage-2 call uses "姿态与运动完成度分析师"
        prompt_text = ""
        for part in (json.get("messages", [{}])[0].get("content", []) or []):
            if isinstance(part, dict) and part.get("type") == "text":
                prompt_text = part.get("text", "")
                break
            elif isinstance(part, str):
                prompt_text = part
                break
        # stage 2 has a system + user message (2 messages); stage 1 has 1
        if len(json.get("messages", [])) == 1:
            return FakeResp(200, _stage1_body(SAMPLE_FEATURES))
        else:
            return FakeResp(200, _stage2_body(SAMPLE_ANALYSIS))

    monkeypatch.setattr(vp.requests, "post", fake_post)
    r = vp.two_stage_pose_analysis(
        image_url="https://cdn.example.com/rep.jpg",
        exercise="squat",
        rule_summary={"total": 84.0},
        user_context={"user_id": 31},
        evidence_citations=[{"key": "squat_knee_deep"}],
    )
    assert r["ok"] is True
    assert r["stage_extract"]["ok"] is True
    assert r["stage_reason"]["ok"] is True
    assert r["stage_reason"]["analysis"]["exercise_confirmed"] == "squat"
    assert len(calls) == 2  # exactly two API calls


def test_two_stage_fails_gracefully_when_stage1_empty(monkeypatch):
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")
    # Stage 1 returns totally empty content (no features, no raw text)
    monkeypatch.setattr(vp.requests, "post", lambda *a, **kw: FakeResp(200, {"choices": [{"message": {"content": ""}}]}))
    r = vp.two_stage_pose_analysis(image_url="https://cdn.example.com/rep.jpg", exercise="squat")
    assert r["ok"] is False
    assert r["stage_failed"] == "extract"


# ---------------- toolkit wrapper test ----------------


def test_analyze_rep_two_stage_tool_registered():
    from fitness_agent.toolkit import TOOL_SPECS
    names = {t["name"] for t in TOOL_SPECS}
    assert "analyze_rep_two_stage" in names
