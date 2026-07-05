"""Multi-vendor vision provider registry + fallback tests."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fitness_agent import vision as vmod
from fitness_agent import vision_pipeline as vp
from fitness_agent import vision_providers as vprov


# ---------- provider registry ----------


def _clear_all_keys(mp):
    for k in [
        "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "VOLC_ARK_API_KEY", "ARK_API_KEY",
        "ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "BIGMODEL_API_KEY", "GLM_API_KEY",
        "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "HUNYUAN_API_KEY", "TENCENT_HUNYUAN_API_KEY",
        "AI_AGENT_VISION_PROVIDERS", "AI_AGENT_VISION_MODEL",
    ]:
        mp.delenv(k, raising=False)


def test_available_providers_empty_when_no_keys(monkeypatch):
    _clear_all_keys(monkeypatch)
    assert vprov.available_providers() == []


def test_qwen_takes_priority_over_volcengine(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "volc-key")
    provs = vprov.available_providers()
    # qwen key drives all 3 qwen tiers ahead of volcengine
    assert [p["provider"] for p in provs] == [
        "qwen", "qwen-fast", "qwen-cheap", "volcengine", "volcengine-legacy",
    ]
    assert provs[0]["model"] == "qwen-vl-max"
    assert provs[1]["model"] == "qwen3-vl-plus-2025-12-19"
    assert provs[2]["model"] == "qwen-vl-plus"


def test_priority_override_env(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "volc-key")
    monkeypatch.setenv("AI_AGENT_VISION_PROVIDERS", "volcengine,qwen")
    provs = vprov.available_providers()
    assert [p["provider"] for p in provs] == ["volcengine", "qwen"]


def test_model_pin_applies_to_first_provider(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.setenv("AI_AGENT_VISION_MODEL", "qwen-vl-plus")
    provs = vprov.available_providers()
    assert provs[0]["model"] == "qwen-vl-plus"


def test_summarize_config_no_key_leak(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "some-secret-value")
    snap = vprov.summarize_config()
    serialized = json.dumps(snap, ensure_ascii=False)
    assert "some-secret-value" not in serialized
    qwen_entry = next(p for p in snap["providers"] if p["provider"] == "qwen")
    assert qwen_entry["configured"] is True
    assert qwen_entry["key_env"] == "DASHSCOPE_API_KEY"


# ---------- analyze_pose_image (1-stage) fallback ----------


class _FakeResp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body
        self.text = json.dumps(body)
    def json(self):
        return self._body


def _completion_body(content):
    return {"choices": [{"message": {"content": content}}]}


def test_analyze_pose_image_uses_first_provider_and_stops(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "volc")  # would be secondary

    calls = []
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return _FakeResp(200, _completion_body('{"exercise_visible":"squat","confidence":0.9}'))
    monkeypatch.setattr(vmod.requests, "post", fake_post)

    r = vmod.analyze_pose_image(image_url="https://cdn/x.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["provider"] == "qwen"
    assert r["model"] == "qwen-vl-max"
    assert len(calls) == 1  # stopped after first success
    assert "dashscope" in calls[0]


def test_analyze_pose_image_falls_over_on_http_error(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "volc")

    seen = []
    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(url)
        if "dashscope" in url:
            return _FakeResp(429, {"error": {"code": "1113", "message": "quota"}})
        return _FakeResp(200, _completion_body('{"exercise_visible":"squat"}'))
    monkeypatch.setattr(vmod.requests, "post", fake_post)

    r = vmod.analyze_pose_image(image_url="https://cdn/x.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["provider"] == "volcengine"
    assert len(seen) >= 2
    # tried_before must record qwen 429
    assert any(t["provider"] == "qwen" and t.get("http") == 429 for t in r["tried_before"])


def test_analyze_pose_image_all_fail(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k1")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "k2")
    monkeypatch.setattr(vmod.requests, "post", lambda *a, **kw: _FakeResp(500, {"err": "boom"}))
    r = vmod.analyze_pose_image(image_url="https://cdn/x.jpg", exercise="squat")
    assert r["ok"] is False
    assert r["error_type"] == "vision_all_providers_failed"
    assert len(r["tried"]) >= 2


def test_analyze_pose_image_no_provider_returns_clean_error(monkeypatch):
    _clear_all_keys(monkeypatch)
    r = vmod.analyze_pose_image(image_url="https://cdn/x.jpg", exercise="squat")
    assert r["ok"] is False
    assert "no vision provider configured" in r["error"]


# ---------- extract_pose_features (stage 1) fallback ----------


def test_extract_pose_features_falls_over(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen")
    monkeypatch.setenv("VOLC_ARK_API_KEY", "volc")

    def fake_post(url, headers=None, json=None, timeout=None):
        if "dashscope" in url:
            raise vp.requests.RequestException("network dead")
        return _FakeResp(200, _completion_body('{"exercise_visible":"squat","observed_angles_deg":{"knee_left":90}}'))
    monkeypatch.setattr(vp.requests, "post", fake_post)

    r = vp.extract_pose_features(image_url="https://cdn/x.jpg", exercise="squat")
    assert r["ok"] is True
    assert r["provider"] == "volcengine"
    # 3 qwen tiers all failed with network error before volcengine succeeded
    assert len(r["tried_before"]) == 3
    assert all(t["provider"].startswith("qwen") for t in r["tried_before"])


# ---------- toolkit registration ----------


def test_vision_providers_status_tool_registered():
    from fitness_agent.toolkit import TOOL_SPECS
    names = {t["name"] for t in TOOL_SPECS}
    assert "vision_providers_status" in names


def test_vision_providers_status_tool_call(monkeypatch):
    from fitness_agent.tools import execute_tool
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("VOLC_ARK_API_KEY", "abc")
    r = execute_tool(None, 42, "vision_providers_status", {})
    assert r["ok"] is True
    volc = next(p for p in r["providers"] if p["provider"] == "volcengine")
    assert volc["configured"] is True
    # ensure key value not returned
    assert "abc" not in json.dumps(r, ensure_ascii=False)
