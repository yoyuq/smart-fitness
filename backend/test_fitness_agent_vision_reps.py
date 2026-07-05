"""Unit tests for rep-analysis tools + vision tool + academic search."""
import os
import sqlite3
import sys
import time
import uuid

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fitness_agent.tools import execute_tool


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
        CREATE TABLE rep_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, rep_index INTEGER,
            exercise TEXT, depth REAL, control REAL, symmetry REAL, total REAL,
            peak_angle REAL, duration_s REAL, feedback TEXT, ts REAL,
            start_frame TEXT, peak_frame TEXT, end_frame TEXT, angle_series TEXT,
            true_label TEXT, error_type TEXT, clip_id TEXT, clip_dir TEXT
        );
        CREATE TABLE exercise_log (
            id INTEGER PRIMARY KEY, user_id INTEGER, exercise_type TEXT,
            reps INTEGER, duration_s REAL, avg_form_score REAL, created_at INTEGER
        );
        """
    )
    now = int(time.time())
    db.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess_A", "esp32cam-001", "42", "squat", now - 3600, now - 3000, 10, 78.0, "finished"),
    )
    db.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess_B_other", "esp32cam-001", "999", "squat", now - 300, now - 100, 5, 85.0, "finished"),
    )
    for i, (depth, control, sym, total, feedback) in enumerate(
        [
            (80, 90, 95, 85, "膝盖再深一点"),
            (60, 70, 85, 72, "膝盖再深一点; 躯干过度前倾"),
            (55, 75, 80, 70, "膝盖再深一点"),
            (75, 85, 90, 82, "标准!"),
        ],
        start=1,
    ):
        db.execute(
            "INSERT INTO rep_scores (session_id, rep_index, exercise, depth, control, symmetry, total, peak_angle, duration_s, feedback, ts, peak_frame, angle_series) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_A",
                i,
                "squat",
                depth,
                control,
                sym,
                total,
                92.0 + i,
                1.8,
                feedback,
                now - 3600 + i * 10,
                f"/tmp/frames/sess_A/rep{i}_peak.jpg",
                '{"knee_L":[170,120,95,90,110,150,170], "knee_R":[169,121,96,91,111,151,169]}',
            ),
        )
    db.execute(
        "INSERT INTO rep_scores (session_id, rep_index, exercise, depth, control, symmetry, total, peak_angle, duration_s, feedback, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess_B_other", 1, "squat", 90, 90, 90, 90, 95, 1.5, "标准!", now - 200),
    )
    db.commit()
    yield db
    db.close()


# ------------- get_session_rep_scores -------------
def test_get_session_rep_scores_returns_owned_session_only(conn):
    out = execute_tool(conn, 42, "get_session_rep_scores", {"session_id": "sess_A"})
    assert out["ok"] is True
    assert len(out["reps"]) == 4
    summary = out["summary_by_exercise"][0]
    assert summary["exercise"] == "squat"
    assert summary["reps"] == 4
    assert summary["avg_total"] > 70

    denied = execute_tool(conn, 42, "get_session_rep_scores", {"session_id": "sess_B_other"})
    assert denied["ok"] is False
    assert "current user" in denied["error"]

    missing = execute_tool(conn, 42, "get_session_rep_scores", {"session_id": "nope"})
    assert missing["ok"] is False


def test_get_session_rep_scores_missing_param(conn):
    out = execute_tool(conn, 42, "get_session_rep_scores", {})
    assert out["ok"] is False
    assert "session_id" in out["error"]


# ------------- get_rep_analysis -------------
def test_get_rep_analysis_shrinks_angle_series(conn):
    rep_id = conn.execute("SELECT id FROM rep_scores WHERE session_id='sess_A' AND rep_index=2").fetchone()[0]
    out = execute_tool(conn, 42, "get_rep_analysis", {"rep_id": rep_id})
    assert out["ok"] is True
    rep = out["rep"]
    assert rep["exercise"] == "squat"
    assert rep["feedback"] == "膝盖再深一点; 躯干过度前倾"
    assert rep["angle_series"] is not None
    assert "knee_L" in rep["angle_series"]
    k = rep["angle_series"]["knee_L"]
    assert k["n"] == 7
    assert k["min"] == 90.0
    assert k["max"] == 170.0
    assert isinstance(k["sampled"], list)


def test_get_rep_analysis_rejects_foreign_rep(conn):
    rep_id = conn.execute("SELECT id FROM rep_scores WHERE session_id='sess_B_other'").fetchone()[0]
    out = execute_tool(conn, 42, "get_rep_analysis", {"rep_id": rep_id})
    assert out["ok"] is False
    assert "current user" in out["error"]


# ------------- get_last_training_analysis -------------
def test_get_last_training_analysis_aggregates_by_session(conn):
    out = execute_tool(conn, 42, "get_last_training_analysis", {"days": 7})
    assert out["ok"] is True
    sessions = out["sessions"]
    # Only sess_A belongs to user 42
    ids = [s["session_id"] for s in sessions]
    assert "sess_A" in ids
    assert "sess_B_other" not in ids
    only = [s for s in sessions if s["session_id"] == "sess_A"][0]
    ra = only["rep_analysis"]
    assert ra["reps_scored"] == 4
    # Most frequent issue must be the one repeated in feedback strings
    top_issue = ra["top_issues"][0]["issue"]
    assert "膝盖再深" in top_issue


# ------------- analyze_rep_image (vision tool) -------------
def test_analyze_rep_image_rejects_when_vision_disabled(conn, monkeypatch):
    monkeypatch.setenv("AI_AGENT_VISION_ENABLED", "false")
    rep_id = conn.execute("SELECT id FROM rep_scores WHERE session_id='sess_A' LIMIT 1").fetchone()[0]
    out = execute_tool(conn, 42, "analyze_rep_image", {"rep_id": rep_id})
    assert out["ok"] is False
    assert "disabled" in out["error"]


def test_analyze_rep_image_rejects_missing_api_key(conn, monkeypatch):
    monkeypatch.setenv("AI_AGENT_VISION_ENABLED", "true")
    monkeypatch.delenv("VOLC_ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    rep_id = conn.execute("SELECT id FROM rep_scores WHERE session_id='sess_A' LIMIT 1").fetchone()[0]
    out = execute_tool(conn, 42, "analyze_rep_image", {"rep_id": rep_id})
    # It will fail with either "not configured" (before file check) or path-not-inside-roots
    # depending on env; either is acceptable, but must be ok:false.
    assert out["ok"] is False


def test_analyze_rep_image_rejects_missing_rep(conn):
    out = execute_tool(conn, 42, "analyze_rep_image", {"rep_id": 999999})
    assert out["ok"] is False


def test_analyze_rep_image_calls_volc_and_returns_findings(conn, monkeypatch, tmp_path):
    # Create a tiny fake image inside an allowed root
    img_dir = tmp_path / "frames" / "sess_A"
    img_dir.mkdir(parents=True)
    img_path = img_dir / "rep1_peak.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    monkeypatch.setenv("AI_AGENT_VISION_ENABLED", "true")
    monkeypatch.setenv("AI_AGENT_VISION_FRAME_ROOTS", str(tmp_path))
    monkeypatch.setenv("VOLC_ARK_API_KEY", "test-key")

    # Update rep row to point peak_frame at our fake image
    rep_id = conn.execute("SELECT id FROM rep_scores WHERE session_id='sess_A' AND rep_index=1").fetchone()[0]
    conn.execute("UPDATE rep_scores SET peak_frame=? WHERE id=?", (str(img_path), rep_id))
    conn.commit()

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"exercise_visible":"squat","posture_findings":[{"issue":"膝盖内扣","severity":"medium"}],"positive_points":["核心稳定"],"recommendations":["拓宽站姿"],"confidence":0.7}'}}
                ]
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("fitness_agent.vision.requests.post", fake_post)

    out = execute_tool(conn, 42, "analyze_rep_image", {"rep_id": rep_id})
    assert out["ok"] is True
    assert out["analysis"]["exercise_visible"] == "squat"
    assert out["analysis"]["posture_findings"][0]["issue"] == "膝盖内扣"
    assert out["rep_id"] == rep_id
    # Payload contains a data-URI image message
    parts = captured["json"]["messages"][0]["content"]
    assert any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/") for p in parts)


# ------------- get_scoring_evidence -------------
def test_get_scoring_evidence_all(conn):
    out = execute_tool(conn, 42, "get_scoring_evidence", {})
    assert out["ok"] is True
    keys = set(out["evidence"].keys())
    assert "squat.knee_deep" in keys
    assert "push_up.elbow_shallow" in keys
    assert any("pubmed" in v.get("url", "") for v in out["evidence"].values())


def test_get_scoring_evidence_by_exercise(conn):
    out = execute_tool(conn, 42, "get_scoring_evidence", {"exercise": "squat"})
    assert out["ok"] is True
    assert out["exercise"] == "squat"
    # Every returned key should be scoped to the squat exercise.
    assert all(k.startswith("squat.") for k in out["evidence"].keys())
    assert any("Escamilla" in v["authors"] for v in out["evidence"].values())


def test_get_scoring_evidence_unknown(conn):
    out = execute_tool(conn, 42, "get_scoring_evidence", {"exercise": "deadlift"})
    assert out["ok"] is False
    assert "known" in out


# ------------- academic web search -------------
def test_search_fitness_web_academic_biases_query_and_filters_hosts(monkeypatch):
    monkeypatch.setattr("fitness_agent.web_search._enabled", lambda: True)
    monkeypatch.setattr("fitness_agent.web_search._llm_guard_enabled", lambda: False)

    captured = {}

    class FakeResp:
        text = ""
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResp()

    def fake_parse(text, limit):
        return [
            {"title": "Squat depth EMG study", "url": "https://pubmed.ncbi.nlm.nih.gov/12345/", "domain": "pubmed.ncbi.nlm.nih.gov", "snippet": "...", "trusted_hint": True},
            {"title": "Random blog", "url": "https://blog.example.com/squat", "domain": "blog.example.com", "snippet": "...", "trusted_hint": False},
            {"title": "ACSM Position stand", "url": "https://acsm.org/foo", "domain": "acsm.org", "snippet": "...", "trusted_hint": True},
        ]

    monkeypatch.setattr("fitness_agent.web_search.requests.get", fake_get)
    monkeypatch.setattr("fitness_agent.web_search._parse_duckduckgo_html", fake_parse)

    from fitness_agent.web_search import search_fitness_web

    out = search_fitness_web("squat depth peer reviewed", limit=5, academic=True)
    assert out["ok"] is True
    assert out["academic"] is True
    assert "site%3A" in captured["url"] or "site:" in captured["url"]  # academic bias applied
    domains = {r["domain"] for r in out["results"]}
    assert "pubmed.ncbi.nlm.nih.gov" in domains
    assert "acsm.org" in domains
    assert "blog.example.com" not in domains
