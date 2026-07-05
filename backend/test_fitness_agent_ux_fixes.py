"""Additional regression tests: workouts session_id + JSON leak sanitizer."""
import os, sqlite3, sys, time

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fitness_agent.tools import execute_tool
from fitness_agent.loop import _sanitize_leaked_agent_json


@pytest.fixture()
def conn(tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, device_id TEXT, user_id TEXT,
            exercise_type TEXT, start_time REAL, end_time REAL,
            total_reps INTEGER, avg_form_score REAL, status TEXT
        );
        CREATE TABLE exercise_log (
            log_id INTEGER PRIMARY KEY, user_id INTEGER, device_id TEXT,
            exercise_type TEXT, reps INTEGER, duration_s REAL,
            avg_form_score REAL, created_at INTEGER
        );
        """
    )
    now = int(time.time())
    db.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess_A", "dev", "42", "squat", now - 600, now - 300, 10, 85.0, "finished"),
    )
    db.execute(
        "INSERT INTO exercise_log (user_id, device_id, exercise_type, reps, duration_s, avg_form_score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (42, "dev", "squat", 10, 300.0, 85.0, now - 300),
    )
    db.commit()
    yield db
    db.close()


def test_get_recent_workouts_now_includes_session_id(conn):
    r = execute_tool(conn, 42, "get_recent_workouts", {"days": 7, "exercise": "squat"})
    assert r["ok"] is True
    assert r["workouts"], "expected at least one workout row"
    row = r["workouts"][0]
    assert row.get("session_id") == "sess_A", f"session_id should be filled in, got {row}"


def test_sanitize_leaked_json_recovers_final_field():
    raw = '{"final": "## 深蹲\\n\\n这是第一段。\\n\\n- 要点"}'
    out = _sanitize_leaked_agent_json(raw)
    assert not out.startswith("{"), f"still leaking JSON wrapper: {out!r}"
    assert "深蹲" in out
    assert "要点" in out


def test_sanitize_leaked_json_leaves_clean_prose_alone():
    raw = "## 深蹲\n\n这是普通回答"
    out = _sanitize_leaked_agent_json(raw)
    assert out == raw


def test_sanitize_leaked_json_handles_literal_newlines():
    raw = '{"final": "标题\n\n段落一\n段落二"}'
    out = _sanitize_leaked_agent_json(raw)
    assert not out.startswith("{")
    assert "段落一" in out


def test_sanitize_leaked_json_returns_original_when_no_final():
    raw = '{"tool_calls": [{"name": "x"}]}'
    out = _sanitize_leaked_agent_json(raw)
    assert out == raw
