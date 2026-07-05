import os, sys, time, uuid
import sqlite3
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401
import fitness_agent


def _uid() -> int:
    return 920000 + (uuid.uuid4().int % 50000)


def _headers(user_id=None):
    token = auth.generate_token(user_id or _uid(), "agent_background_test")
    return {"Authorization": f"Bearer {token}"}, int(auth.verify_token(token)["user_id"])


def _conn():
    c = sqlite3.connect(main_v2_extra.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def test_background_no_training_creates_one_daily_reminder_and_is_idempotent():
    headers, user_id = _headers()
    conn = _conn()
    try:
        result = fitness_agent.run_background_checks(conn, user_id, job="daily_checkin", now=int(time.time()))
        assert result["ok"] is True
        assert result["created"] >= 1
        assert any(i["kind"] == "inactivity_reminder" for i in result["items"])

        again = fitness_agent.run_background_checks(conn, user_id, job="daily_checkin", now=int(time.time()))
        assert again["ok"] is True
        assert again["created"] == 0
    finally:
        conn.close()

    client = TestClient(app)
    r = client.get("/api/v2/agent/background/items?status=pending", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    items = data["items"]
    assert any(i["kind"] == "inactivity_reminder" and "还没有训练记录" in i["title"] for i in items)
    assert all(not i.get("requires_approval") for i in items)


def test_background_weekly_report_and_plan_suggestion_are_read_only():
    headers, user_id = _headers()
    now = int(time.time())
    conn = _conn()
    try:
        for days_ago, exercise, reps in [(1, "pushup", 30), (3, "squat", 45), (6, "running", 1)]:
            conn.execute(
                "INSERT INTO exercise_log (user_id, device_id, exercise_type, reps, duration_s, avg_form_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, "test-device", exercise, reps, 900, 86.0, now - days_ago * 86400),
            )
        conn.commit()

        result = fitness_agent.run_background_checks(conn, user_id, job="weekly_review", now=now)
        assert result["ok"] is True
        kinds = {i["kind"] for i in result["items"]}
        assert "weekly_report" in kinds
        assert "weekly_plan_suggestion" in kinds
        assert all(i.get("requires_approval") is False for i in result["items"])

        plans_before = conn.execute("SELECT COUNT(*) AS n FROM workout_plans WHERE user_id=?", (user_id,)).fetchone()["n"]
        assert plans_before == 0
    finally:
        conn.close()

    client = TestClient(app)
    r = client.get("/api/v2/agent/background/items", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    weekly = next(i for i in items if i["kind"] == "weekly_report")
    assert weekly["payload"]["summary"]["sessions"] == 3
    assert "本周训练" in weekly["message"]
    plan = next(i for i in items if i["kind"] == "weekly_plan_suggestion")
    assert plan["payload"]["draft"]["exercises"]
    assert "草案" in plan["message"]


def test_background_item_mark_read_endpoint():
    headers, user_id = _headers()
    conn = _conn()
    try:
        result = fitness_agent.run_background_checks(conn, user_id, job="daily_checkin", now=int(time.time()))
        item_id = result["items"][0]["item_id"]
    finally:
        conn.close()

    client = TestClient(app)
    r = client.post(f"/api/v2/agent/background/items/{item_id}/read", headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    rr = client.get("/api/v2/agent/background/items?status=read", headers=headers)
    assert rr.status_code == 200
    assert any(i["item_id"] == item_id and i["status"] == "read" for i in rr.json()["items"])
