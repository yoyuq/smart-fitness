"""Tests for the S11/S08/S05 additions on top of the Fitness Agent loop.

- S11: unknown-tool recovery + empty tool_calls nudge.
- S08: per-turn tool result compaction fed back to the LLM prompt.
- S05: TODO status block injected into the next LLM turn.
"""
import json
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401


def _uid() -> int:
    return 700000 + (uuid.uuid4().int % 200000)


@pytest.fixture(autouse=True)
def _clear_provider_breakers():
    import fitness_agent.loop as loop
    loop._PROVIDER_BREAKERS.clear()
    yield
    loop._PROVIDER_BREAKERS.clear()


def _client(username: str):
    token = auth.generate_token(_uid(), username)
    return TestClient(app), {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# S11: unknown tool + empty tool_calls
# ---------------------------------------------------------------------------
def test_s11_unknown_tool_is_recovered(monkeypatch):
    calls = {"n": 0}
    prompts = []

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        prompts.append(list(messages))
        if calls["n"] == 1:
            return json.dumps({"tool_calls": [{"name": "query_openai_web", "args": {"q": "foo"}}]})
        # After seeing unknown_tool, the model should be nudged to give a final answer.
        assert any("unknown_tool" in (m.get("content") or "") for m in messages)
        return json.dumps({"final": "对不起，我没有 query_openai_web 这个工具，我只能用健身相关的白名单工具。"})

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    client, headers = _client("agent_unknown_tool")

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["run_status"] == "completed"

    trace = data["agent_loop"]["trace"]
    assert trace[0]["name"] == "query_openai_web"
    assert trace[0]["result"]["ok"] is False
    assert trace[0]["result"]["error_type"] == "unknown_tool"
    assert "available_tools" in trace[0]["result"]

    recovery = data["agent_loop"]["recovery"]
    assert any(t.get("event") == "unknown_tool" and t.get("tool") == "query_openai_web" for t in recovery)


def test_s11_empty_tool_calls_array_is_nudged(monkeypatch):
    calls = {"n": 0}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"tool_calls": []})
        # After the nudge, the model gives a final answer.
        assert any("tool_calls 数组为空" in (m.get("content") or "") for m in messages)
        return json.dumps({"final": "我直接给你一个保守的分析结论。"})

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    client, headers = _client("agent_empty_tool_calls")

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "分析最近训练", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"
    assert "保守" in data["reply"]
    recovery = data["agent_loop"]["recovery"]
    assert any(t.get("event") == "empty_tool_calls" for t in recovery)


# ---------------------------------------------------------------------------
# S08: tool result compaction into next-turn prompt
# ---------------------------------------------------------------------------
def test_s08_large_tool_result_is_compacted_before_next_turn(monkeypatch):
    huge_note = "x" * 5000  # far above MAX_TOOL_STRING (~900)
    calls = {"n": 0}
    seen_tool_prompt = {"content": None}

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"tool_calls": [{"name": "get_recent_workouts", "args": {"days": 7}}]})
        # Turn 2: model receives compacted tool results.
        tool_prompt = None
        for m in messages:
            if (m.get("content") or "").startswith("工具结果(JSON)：\n"):
                tool_prompt = m["content"]
        assert tool_prompt is not None
        seen_tool_prompt["content"] = tool_prompt
        return json.dumps({"final": "已根据近期训练给出简要建议。"})

    def fake_tool(conn, user_id, name, args=None):
        assert name == "get_recent_workouts"
        return {
            "ok": True,
            "note": huge_note,
            "records": [{"exercise": "squat", "reps": i} for i in range(120)],
        }

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    monkeypatch.setattr("fitness_agent.loop.execute_tool", fake_tool)
    client, headers = _client("agent_s08_compact")

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "最近训练怎么样", "mode": "analysis"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"

    prompt = seen_tool_prompt["content"] or ""
    payload = prompt.split("工具结果(JSON)：\n", 1)[1]
    parsed = json.loads(payload)
    assert isinstance(parsed, list) and parsed
    result = parsed[0]["result"]
    # Long string truncated
    assert isinstance(result["note"], str)
    assert len(result["note"]) < 1200
    assert "truncated" in result["note"]
    # List truncated to MAX_TOOL_LIST_ITEMS with trailing marker
    assert isinstance(result["records"], list)
    assert len(result["records"]) <= 9  # 8 items + 1 trailing marker
    last = result["records"][-1]
    assert isinstance(last, dict) and "_truncated_items" in last

    # Original full-size result still preserved in stored trace (compact_trace
    # will happen at persistence layer; the returned trace here is already
    # compacted by runtime.compact_trace which follows the same rules).
    stored_trace = data["agent_loop"]["trace"]
    assert stored_trace[0]["name"] == "get_recent_workouts"


# ---------------------------------------------------------------------------
# S05: todo status block fed back to the model
# ---------------------------------------------------------------------------
def test_s05_todo_status_block_visible_to_next_turn(monkeypatch):
    calls = {"n": 0}
    todo_blocks_seen = []

    def fake_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        calls["n"] += 1
        # Capture any prompt content mentioning the TODO status block.
        for m in messages:
            content = m.get("content") or ""
            if content.startswith("当前 TODO 状态"):
                todo_blocks_seen.append(content)
        if calls["n"] == 1:
            return json.dumps({
                "tool_calls": [
                    {
                        "name": "todo_write",
                        "args": {
                            "todos": [
                                {"content": "调取最近训练量", "status": "in_progress"},
                                {"content": "对齐当前计划", "status": "pending"},
                                {"content": "生成饮食建议", "status": "pending"},
                            ]
                        },
                    }
                ]
            })
        if calls["n"] == 2:
            # Model should now see the TODO block; move item 1 -> completed, item 2 -> in_progress.
            return json.dumps({
                "tool_calls": [
                    {
                        "name": "todo_write",
                        "args": {
                            "todos": [
                                {"content": "调取最近训练量", "status": "completed"},
                                {"content": "对齐当前计划", "status": "in_progress"},
                                {"content": "生成饮食建议", "status": "pending"},
                            ]
                        },
                    }
                ]
            })
        return json.dumps({"final": "已根据训练量给出下一步建议。"})

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    client, headers = _client("agent_s05_todo")

    r = client.post("/api/v2/agent/chat", headers=headers, json={"message": "帮我做个综合复盘", "mode": "coach"})
    assert r.status_code == 200
    data = r.json()
    assert data["run_status"] == "completed"

    # Final todos snapshot must be the last todo_write payload.
    todos = data["agent_loop"]["todos"]
    assert [t["status"] for t in todos] == ["completed", "in_progress", "pending"]

    # The next turn after the first todo_write must have received the status block.
    assert todo_blocks_seen, "expected TODO status block to reach the model on the next turn"
    first_block = todo_blocks_seen[0]
    assert "调取最近训练量" in first_block
    assert "<in_progress>" in first_block
    assert "[~]" in first_block  # in_progress marker
    assert "[ ]" in first_block  # pending marker
