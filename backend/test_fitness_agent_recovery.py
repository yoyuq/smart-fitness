import os, sys, uuid
import time
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401


def _uid() -> int:
    return 500000 + (uuid.uuid4().int % 400000)


@pytest.fixture(autouse=True)
def _clear_provider_breakers():
    import fitness_agent.loop as loop
    loop._PROVIDER_BREAKERS.clear()
    yield
    loop._PROVIDER_BREAKERS.clear()


def test_fitness_agent_bad_json_is_repaired_or_falls_back(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return '```json\n{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]  // broken json\n```'
        if "修复" in messages[-1]["content"] or "只输出" in messages[-1]["content"]:
            return '{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]}'
        assert "工具结果" in messages[-1]["content"]
        return '{"final":"已恢复坏 JSON，并根据训练汇总给出建议。"}'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_bad_json_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"
    assert "已恢复坏 JSON" in data["reply"]
    assert any(t.get("event") == "json_repair" for t in data["agent_loop"].get("recovery", []))


def test_fitness_agent_tool_exception_is_recovered(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]}'
        assert "tool_exception" in messages[-1]["content"] or "工具结果" in messages[-1]["content"]
        return '{"final":"训练数据工具刚才失败了，我先给你保守建议，并建议稍后重试。"}'

    def boom(*args, **kwargs):
        raise RuntimeError("simulated db timeout")

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    monkeypatch.setattr("fitness_agent.loop.execute_tool", boom)
    token = auth.generate_token(_uid(), "agent_tool_exception_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"
    assert "保守建议" in data["reply"]
    item = data["agent_loop"]["trace"][0]
    assert item["result"]["ok"] is False
    assert item["result"]["error_type"] == "tool_exception"
    assert any(t.get("event") == "tool_exception" for t in data["agent_loop"].get("recovery", []))


def test_fitness_agent_uses_agent_specific_provider_timeout(monkeypatch):
    seen = []

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        seen.append((chain, timeout))
        return '{"final":"短超时调用成功。"}'

    monkeypatch.setenv("AI_AGENT_CHAT_CHAIN", "deepseek,qwen")
    monkeypatch.setattr("ai_planner.AGENT_LLM_TIMEOUT", 17)
    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_provider_timeout_test")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    assert r.json()["run_status"] == "completed"
    assert seen and seen[0] == ("deepseek", 17)


def test_fitness_agent_provider_circuit_breaker_skips_cooling_provider(monkeypatch):
    attempts = []

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        attempts.append(chain)
        if chain == "deepseek":
            raise TimeoutError("deepseek still down")
        if chain == "qwen":
            return '{"final":"已跳过冷却中的主模型，用备用模型完成。"}'
        return None

    monkeypatch.setenv("AI_AGENT_CHAT_CHAIN", "deepseek,qwen")
    monkeypatch.setattr("fitness_agent.loop.PROVIDER_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr("fitness_agent.loop.PROVIDER_COOLDOWN_SEC", 300)
    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_provider_circuit_test")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # First failure: deepseek tried, qwen recovers.
    r1 = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练1", "mode": "analysis"})
    assert r1.status_code == 200
    # Second failure opens circuit for deepseek.
    r2 = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练2", "mode": "analysis"})
    assert r2.status_code == 200
    # Third request should skip deepseek and go straight to qwen.
    before = len(attempts)
    r3 = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练3", "mode": "analysis"})
    assert r3.status_code == 200
    new_attempts = attempts[before:]
    assert new_attempts == ["qwen"]
    recovery = r3.json()["agent_loop"].get("recovery", [])
    assert any(t.get("event") == "provider_skipped" and t.get("provider") == "deepseek" for t in recovery)


def test_fitness_agent_provider_exception_switches_to_backup_llm(monkeypatch):
    attempts = []

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        attempts.append(chain)
        if chain == "deepseek":
            raise TimeoutError("deepseek timeout")
        if chain == "qwen":
            return '{"final":"已切换备用模型完成分析。"}'
        return None

    monkeypatch.setenv("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,hunyuan")
    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_provider_backup_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"
    assert "备用模型" in data["reply"]
    assert attempts[:2] == ["deepseek", "qwen"]
    recovery = data["agent_loop"].get("recovery", [])
    assert any(t.get("event") == "provider_error" and t.get("provider") == "deepseek" for t in recovery)
    assert any(t.get("event") == "provider_recovered" and t.get("provider") == "qwen" for t in recovery)
    health = client.get("/api/v2/agent/health", headers=headers)
    assert health.status_code == 200
    providers = {p.get("provider"): p for p in health.json().get("providers", [])}
    assert "deepseek" in providers
    assert "qwen" in providers


def test_fitness_agent_tool_timeout_is_recovered(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]}'
        assert "tool_timeout" in messages[-1]["content"] or "工具结果" in messages[-1]["content"]
        return '{"final":"工具响应超时，我先给你保守建议，稍后可以重试。"}'

    def slow_tool(*args, **kwargs):
        time.sleep(0.3)
        return {"ok": True, "too_late": True}

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    monkeypatch.setattr("fitness_agent.loop.execute_tool", slow_tool)
    monkeypatch.setattr("fitness_agent.loop.AGENT_TOOL_TIMEOUT_SEC", 0.05)
    token = auth.generate_token(_uid(), "agent_tool_timeout_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"
    assert "超时" in data["reply"]
    item = data["agent_loop"]["trace"][0]
    assert item["result"]["ok"] is False
    assert item["result"]["error_type"] == "tool_timeout"
    assert any(t.get("event") == "tool_timeout" for t in data["agent_loop"].get("recovery", []))


def test_fitness_agent_provider_exception_uses_safe_fallback(monkeypatch):
    def boom(*args, **kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr("ai_planner._call_llm", boom)
    token = auth.generate_token(_uid(), "agent_provider_exception_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"
    assert "连不上大模型" in data["reply"] or "稍后" in data["reply"]
    assert any(t.get("event") == "provider_error" for t in data["agent_loop"].get("recovery", []))


def test_fitness_agent_total_timeout_returns_explicit_degraded_reply(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM should not be called after total timeout budget is already exhausted")

    monkeypatch.setattr("fitness_agent.loop.AGENT_TOTAL_TIMEOUT_SEC", -0.01)
    monkeypatch.setattr("ai_planner._call_llm", fail_if_called)
    token = auth.generate_token(_uid(), "agent_total_timeout_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"
    assert "总耗时上限" in data["reply"]
    assert data["agent_loop"].get("total_timeout_reached") is True
    assert any(t.get("event") == "total_timeout" for t in data["agent_loop"].get("recovery", []))


def test_fitness_agent_plan_generation_returns_draft_not_write_approval(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"tool_calls":[{"name":"draft_workout_plan","args":{"goal":"增肌","weeks":1}}]}'
        return '{"final":"我已生成训练计划草稿，你可以先编辑再导入到我的训练计划。"}'

    def fake_generate_plan(conn, user_id, goal, weeks=4, import_to_plans=True):
        assert import_to_plans is False
        return {
            "ok": True,
            "draft": True,
            "plan_name": "AI 计划-增肌 1周",
            "plans": [{"week": 1, "day": 1, "exercise_type": "squat", "target_sets": 3, "target_reps": 15, "intensity_note": "膝盖对齐脚尖"}],
        }

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    monkeypatch.setattr("ai_planner.generate_plan", fake_generate_plan)
    token = auth.generate_token(_uid(), "agent_plan_draft_test")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "请生成一份增肌训练计划", "mode": "plan"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data.get("plan_draft")
    assert data["plan_draft"]["name"] == "AI 计划-增肌 1周"
    assert data["plan_draft"]["exercises"][0]["type"] == "squat"
    assert data["pending_approvals"] == []
    assert data["run_status"] == "completed"


def test_fitness_agent_model_identity_uses_fixed_product_copy(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("identity questions should not call the LLM")

    monkeypatch.setattr("ai_planner._call_llm", fail_if_called)
    token = auth.generate_token(_uid(), "agent_identity_copy")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "你当前是什么模型", "mode": "coach"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"
    assert data["reply"] == "我是 Smart Fitness 专属健身 Agent，当前由后端配置的 LLM 调用链驱动。"
    assert "GPT-4" not in data["reply"]
    assert "OpenAI" not in data["reply"]
    assert data["agent_loop"].get("forced_identity") is True


def test_fitness_agent_provider_chain_respects_total_timeout_budget(monkeypatch):
    attempts = []

    def slow_empty_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        attempts.append((chain, timeout))
        time.sleep(0.08)
        return None

    monkeypatch.setenv("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan")
    monkeypatch.setattr("fitness_agent.loop.AGENT_TOTAL_TIMEOUT_SEC", 0.12)
    monkeypatch.setattr("ai_planner.AGENT_LLM_TIMEOUT", 1)
    monkeypatch.setattr("ai_planner._call_llm", slow_empty_llm)
    token = auth.generate_token(_uid(), "agent_chain_budget_test")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    started = time.time()
    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    elapsed = time.time() - started
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"
    assert data["agent_loop"].get("total_timeout_reached") is True
    assert len(attempts) <= 2
    assert elapsed < 0.45


def test_fitness_agent_max_turns_returns_explicit_recovery(monkeypatch):
    monkeypatch.setattr("fitness_agent.loop.MAX_AGENT_TURNS", 2)

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        return '{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]}'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_max_turn_recovery")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"
    assert "已达到本次分析的工具调用上限" in data["reply"]
    assert data["agent_loop"].get("max_turns_reached") is True
