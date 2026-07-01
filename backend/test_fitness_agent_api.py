import os, sys, uuid
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401


def _uid() -> int:
    return 400000 + (uuid.uuid4().int % 500000)


def test_fitness_agent_kb_lists_domains():
    token = auth.generate_token(_uid(), "hjl")
    client = TestClient(app)
    r = client.get("/api/v2/agent/kb", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    ids = {d["id"] for d in data["domains"]}
    assert {"nutrition", "coach", "analysis", "plan"}.issubset(ids)


def test_fitness_agent_restricted_web_search_tool(monkeypatch):
    import fitness_agent.web_search as ws

    html = '''
    <div class="result">
      <a class="result__a" href="https://example.org/running-protein">Running protein guide</a>
      <a class="result__snippet">Protein and carbohydrate timing for endurance training.</a>
    </div>
    '''

    class FakeResp:
        text = html
        def raise_for_status(self):
            return None

    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(ws.ai_planner, "_call_llm", lambda *a, **k: '{"allowed":true,"category":"nutrition","reason":"fitness related","search_query":"跑步训练 蛋白 碳水"}')
    ok = ws.search_fitness_web("跑步 训练 蛋白 碳水", limit=3)
    assert ok["ok"] is True
    assert ok["decision"]["allowed"] is True
    assert ok["results"][0]["title"] == "Running protein guide"
    bad = ws.search_fitness_web("api key token", limit=3)
    assert bad["ok"] is False


def test_fitness_agent_requires_message():
    token = auth.generate_token(_uid(), "hjl")

    client = TestClient(app)
    r = client.post("/api/v2/agent/chat", headers={"Authorization": f"Bearer {token}"}, json={"message": ""})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_fitness_agent_loop_can_call_safe_tool(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"tool_calls":[{"name":"get_exercise_summary","args":{"days":28}}]}'
        assert "工具结果" in messages[-1]["content"]
        return '{"final":"已根据最近训练汇总给出建议。"}'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_loop_test")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析一下最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["agent_loop"]["enabled"] is True
    assert data["agent_loop"]["turns"] == 2
    assert data["agent_loop"]["trace"][0]["name"] == "get_exercise_summary"
    assert data["agent_loop"].get("hooks")
    hook_events = {h["event"] for h in data["agent_loop"].get("hooks", [])}
    assert {"UserPromptSubmit", "PreToolUse", "Stop"}.issubset(hook_events)
    assert data.get("run_id")
    assert data.get("run_status") == "completed"
    rr = client.get(f"/api/v2/agent/runs/{data['run_id']}", headers=headers)
    assert rr.status_code == 200
    run = rr.json()["run"]
    assert run["status"] == "completed"
    assert run["trace"][0]["name"] == "get_exercise_summary"
    assert "最近训练汇总" in data["reply"]

    h = client.get("/api/v2/agent/history?limit=5", headers=headers).json()
    assert h["ok"] is True
    assert len(h["messages"]) >= 2


def test_fitness_agent_loop_write_tool_requires_approval(monkeypatch):
    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None):
        last = messages[-1]["content"]
        if "后端已经执行完毕" in messages[0]["content"]:
            return '{"final":"体重已经更新为55.5kg，后续我会按这个数据给你分析训练和饮食。"}'
        if "工具结果" not in last:
            return '{"tool_calls":[{"name":"update_body_metrics","args":{"weight_kg":55.5,"height_cm":170,"notes":"测试审批"}}]}'
        return '{"final":"我已准备更新身体指标，需要你确认后才会写入。"}'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    token = auth.generate_token(_uid(), "agent_perm_test")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get("/api/v2/metrics/body", headers=headers).json().get("metrics", [])
    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "帮我把体重更新成55.5kg", "mode": "auto"})
    assert r.status_code == 200
    data = r.json()
    approvals = data.get("pending_approvals") or []
    assert approvals
    assert approvals[0]["tool_name"] == "update_body_metrics"
    assert approvals[0].get("run_id") == data.get("run_id")
    assert data.get("run_status") == "waiting_approval"

    mid = client.get("/api/v2/metrics/body", headers=headers).json().get("metrics", [])
    assert len(mid) == len(before)  # not executed before approval

    approval_id = approvals[0]["approval_id"]
    ar = client.post(f"/api/v2/agent/approvals/{approval_id}/approve", headers=headers)
    assert ar.status_code == 200
    ar_json = ar.json()
    assert ar_json["ok"] is True
    assert ar_json.get("run_id") == data.get("run_id")
    assert ar_json.get("run_status") == "completed"
    assert ar_json.get("resume", {}).get("resumed") is True
    assert "55.5" in ar_json.get("reply", "")
    after = client.get("/api/v2/metrics/body", headers=headers).json().get("metrics", [])
    assert len(after) == len(before) + 1
    assert abs(float(after[0]["weight_kg"]) - 55.5) < 0.01
    rr = client.get(f"/api/v2/agent/runs/{data['run_id']}", headers=headers)
    assert rr.status_code == 200
    run = rr.json()["run"]
    assert run["status"] == "completed"
    assert run["pending_approval_ids"] == []
    assert any(t.get("resume") for t in run["trace"])
    assert "55.5" in (run.get("final_text") or "")


def test_fitness_agent_explicit_metric_update_is_forced_to_approval(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("explicit metric update should not depend on LLM JSON compliance")

    monkeypatch.setattr("ai_planner._call_llm", fail_if_called)
    token = auth.generate_token(_uid(), "agent_forced_metric_test")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "Update my weight to 56kg", "mode": "auto"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "waiting_approval"
    assert data["pending_approvals"]
    approval = data["pending_approvals"][0]
    assert approval["tool_name"] == "update_body_metrics"
    assert abs(float(approval["args"]["weight_kg"]) - 56.0) < 0.01
    assert data["agent_loop"].get("forced_tool") is True


def test_fitness_agent_context_compact_summarizes_old_history(monkeypatch):
    import sqlite3
    import fitness_agent

    monkeypatch.setattr("ai_planner._call_llm", lambda *a, **k: '{"summary":"用户目标是提升跑步和力量训练表现；偏好食堂饮食；需要后续继续跟踪训练表现。"}')
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    user_id = 9001
    try:
        for i in range(22):
            role = "user" if i % 2 == 0 else "assistant"
            fitness_agent.add_agent_chat_message(conn, user_id, role, f"历史消息{i}：跑步训练和饮食记录", mode="analysis")
        messages = fitness_agent.get_agent_chat_history(conn, user_id, limit=50)
        llm_history = fitness_agent.prepare_llm_history_with_summary(conn, user_id, messages, recent_limit=6)
        assert llm_history[0]["role"] == "user"
        assert "旧对话压缩摘要" in llm_history[0]["content"]
        assert "跑步" in llm_history[0]["content"]
        assert len(llm_history) <= 7
        summary = fitness_agent.get_context_summary(conn, user_id)
        assert summary and summary["source_until_id"] > 0
    finally:
        conn.close()
