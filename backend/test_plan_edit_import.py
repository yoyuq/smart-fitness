import os, sys, uuid
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401
import main_v2_routes  # noqa: F401


def _headers():
    uid = 700000 + (uuid.uuid4().int % 200000)
    token = auth.generate_token(uid, "plan_edit_test")
    return {"Authorization": f"Bearer {token}"}


def test_plan_create_update_normalizes_agent_style_exercises():
    client = TestClient(app)
    headers = _headers()
    raw = [{"exercise_type": "squat", "target_sets": 3, "target_reps": 15, "intensity_note": "膝盖对齐脚尖"}]
    r = client.post("/api/v2/plans", headers=headers, json={"name": "草稿导入", "exercises": raw})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    plan_id = data["plan_id"]

    upd = client.put(
        f"/api/v2/plans/{plan_id}",
        headers=headers,
        json={"name": "编辑后计划", "exercises": [{"type": "push_up", "sets": 4, "reps": 12, "note": "核心收紧"}]},
    )
    assert upd.status_code == 200
    body = upd.json()
    assert body["ok"] is True
    assert body["plan"]["name"] == "编辑后计划"
    assert '"type": "push_up"' in body["plan"]["exercises"]


def test_open_plan_ai_draft_and_checkin_support_running(monkeypatch):
    def fake_llm(*args, **kwargs):
        return '{"reason":"用户已有跑步习惯，先用轻松跑维持有氧，再配合拉伸恢复。","exercises":[{"week":1,"day":1,"title":"5km轻松跑","type":"running","category":"cardio","duration_min":35,"distance_km":5,"sets":0,"reps":0,"intensity":"中等","note":"跑后拉伸"}]}'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    client = TestClient(app)
    headers = _headers()
    draft = client.post("/api/v2/plans/ai_draft", headers=headers, json={"prompt": "想把跑步和力量结合起来", "weeks": 1})
    assert draft.status_code == 200
    data = draft.json()
    assert data["ok"] is True
    assert data["reason"]
    assert data["exercises"][0]["type"] == "running"
    assert data["exercises"][0]["duration_min"] == 35

    create = client.post("/api/v2/plans", headers=headers, json={"name": "跑步开放计划", "exercises": data["exercises"]})
    plan_id = create.json()["plan_id"]
    checkin = client.post(f"/api/v2/plans/{plan_id}/checkin", headers=headers, json={"item": data["exercises"][0]})
    assert checkin.status_code == 200
    assert checkin.json()["ok"] is True
    assert checkin.json()["exercise_type"] == "running"


def test_plan_ai_draft_accepts_builder_metadata(monkeypatch):
    captured = {}

    def fake_generate_plan(conn, user_id, goal, weeks=2, import_to_plans=False):
        captured["goal"] = goal
        captured["weeks"] = weeks
        captured["import_to_plans"] = import_to_plans
        return {
            "ok": True,
            "plan_name": "暑期体能提升计划",
            "reason": "按跑步、力量、恢复组合安排。",
            "plans": [
                {
                    "week": 1,
                    "day": 1,
                    "title": "轻松跑",
                    "type": "running",
                    "category": "cardio",
                    "duration_min": 30,
                    "distance_km": 5,
                    "sets": 0,
                    "reps": 0,
                    "intensity": "中等",
                    "note": "保持轻松配速",
                }
            ],
        }

    monkeypatch.setattr("ai_planner.generate_plan", fake_generate_plan)
    client = TestClient(app)
    headers = _headers()
    r = client.post(
        "/api/v2/plans/ai_draft",
        headers=headers,
        json={
            "prompt": "提高 5km 成绩，同时保持力量训练",
            "weeks": 4,
            "plan_name": "暑期体能提升计划",
            "categories": ["跑步 / 田径类", "健身 / 力量类"],
            "selected_items": ["轻松跑", {"title": "俯卧撑", "type": "push_up"}],
            "weekly_training_days": 4,
            "session_minutes": 45,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["name"] == "暑期体能提升计划"
    assert data["exercises"][0]["type"] == "running"
    assert captured["weeks"] == 4
    assert captured["import_to_plans"] is False
    assert "暑期体能提升计划" in captured["goal"]
    assert "跑步 / 田径类" in captured["goal"]
    assert "俯卧撑" in captured["goal"]
    assert "每周训练天数: 4" in captured["goal"]


def test_ai_plan_generate_can_return_draft_without_insert(monkeypatch):
    def fake_llm(*args, **kwargs):
        return '[{"week":1,"day":1,"exercise_type":"squat","target_reps":15,"target_sets":3,"intensity_note":"下蹲稳定"}]'

    monkeypatch.setattr("ai_planner._call_llm", fake_llm)
    client = TestClient(app)
    headers = _headers()
    before = client.get("/api/v2/plans", headers=headers).json().get("plans", [])
    r = client.post("/api/v2/ai/plan_generate", headers=headers, json={"goal": "增肌", "weeks": 1, "import_to_plans": False})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["draft"] is True
    assert data["inserted"] == 0
    assert data["plan_id"] is None
    after = client.get("/api/v2/plans", headers=headers).json().get("plans", [])
    assert len(after) == len(before)
