"""Real route/auth regression in a fresh process and an isolated SQLite DB.

Run: python -B backend/test_web_records.py (or pytest this file).
No real database, dotenv, MQTT startup, or model invocation is needed.
"""
import os
from pathlib import Path
import subprocess
import sys


def test_web_records_contract():
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--isolated"],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def run_isolated():
    from contextlib import closing
    import importlib
    import json
    import secrets
    import sqlite3
    import tempfile
    import unittest
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    backend = Path(__file__).resolve().parent
    sys.path.insert(0, str(backend))
    connect = sqlite3.connect
    with tempfile.TemporaryDirectory(prefix="sf-records-") as tmp:
        db = str(Path(tmp) / "test.sqlite3")

        def isolated_connect(path, *args, **kwargs):
            if str(path) != ":memory:" and Path(path).resolve() == backend / "fitness.db":
                path = db
            return connect(path, *args, **kwargs)

        # Redirect import-time initialization before importing any app module.
        os.environ["JWT_SECRET"] = secrets.token_hex(32)
        with patch("sqlite3.connect", side_effect=isolated_connect), patch("dotenv.load_dotenv"):
            main = importlib.import_module("main")
        for name in ("main", "auth", "main_v2_routes", "main_v2_extra"):
            module = sys.modules.get(name)
            assert module is not None, f"Required routes failed to load: {name}"
            module.DB_PATH = db
        import auth
        import main_v2_extra as extra
        from fitness_agent.tools import TOOL_SPECS, execute_tool
        assert {"get_recent_workouts", "list_ai_coach_reports", "get_ai_coach_report"} <= {x["name"] for x in TOOL_SPECS}
        assert not execute_tool(None, 1, "not_allowed", {})["ok"]
        extra._ensure_ai_coach_reports_table()
        one = auth.register("records-one", "isolation-password")
        two = auth.register("records-two", "isolation-password")
        assert one["ok"] and two["ok"]
        headers = {"Authorization": "Bearer " + one["token"]}
        other_headers = {"Authorization": "Bearer " + two["token"]}
        with closing(connect(db)) as c, c:
            rows = [
                ("b", one["user_id"], "squat", 100, 160, 0, 0, "completed"),
                ("a", one["user_id"], "squat", 100, None, None, None, "active"),
                ("older", one["user_id"], "push_up", 90, 210, 12, 85, "completed"),
                ("foreign", two["user_id"], "private-exercise", 200, 220, 4, 50, "completed"),
            ]
            c.executemany(
                "INSERT INTO sessions(session_id,user_id,exercise_type,start_time,end_time,"
                "total_reps,avg_form_score,status) VALUES(?,?,?,?,?,?,?,?)", rows
            )
            saved = {"overall_assessment": {"strengths": ["实际已保存的报告"]},
                     "guidance": {"next_session_focus": ["保持节奏"]}}
            c.executemany(
                "INSERT INTO ai_coach_reports(report_id,user_id,session_id,report_json,created_at) "
                "VALUES(?,?,?,?,?)",
                [("saved", one["user_id"], "b", json.dumps(saved), 180),
                 ("foreign-report", two["user_id"], "older", "{}", 200)],
            )
        # No lifespan context: do not start MQTT, camera warmup or background agents.
        client = TestClient(main.app)

        class RecordsContract(unittest.TestCase):
            def test_01_auth_and_isolation(self):
                for path in ("/api/v2/sessions/history", "/api/v2/sessions/detail/b",
                             "/api/v2/ai_coach/reports", "/api/v2/ai_coach/reports/saved"):
                    self.assertEqual(client.get(path).status_code, 401)
                    self.assertEqual(client.get(path, headers={"Authorization": "Bearer expired"}).status_code, 401)
                self.assertEqual(client.get("/api/v2/sessions/detail/b", headers=other_headers).status_code, 404)
                self.assertEqual(client.get("/api/v2/sessions/detail/foreign", headers=headers).status_code, 404)
                self.assertEqual(client.get("/api/v2/sessions/detail/missing", headers=headers).status_code, 404)
                self.assertEqual(client.get("/api/v2/ai_coach/reports/saved", headers=other_headers).status_code, 404)

            def test_02_pagination_filters_and_old_shape(self):
                r = client.get("/api/v2/sessions/history", headers=headers, params={"user_id": two["user_id"]}).json()
                self.assertEqual([x["session_id"] for x in r["sessions"]], ["b", "a", "older"])
                self.assertEqual(r["exercises"], ["push_up", "squat"])
                self.assertEqual([x["has_report"] for x in r["sessions"]], [1, 0, 0])
                r = client.get("/api/v2/sessions/history?exercise=squat&limit=1", headers=headers).json()
                self.assertEqual(r["sessions"][0]["session_id"], "b")
                self.assertTrue(r["has_more"])
                self.assertEqual(r["next_offset"], 1)
                r = client.get("/api/v2/sessions/history?exercise=squat&limit=1&offset=1", headers=headers).json()
                self.assertEqual(r["sessions"][0]["session_id"], "a")
                self.assertFalse(r["has_more"])
                for exercise in ("missing", "squat' OR 1=1 --"):
                    r = client.get("/api/v2/sessions/history", headers=headers, params={"exercise": exercise}).json()
                    self.assertEqual(r["sessions"], [])
                self.assertEqual(client.get("/api/v2/sessions/history?offset=99", headers=headers).json()["sessions"], [])

            def test_03_bad_page_parameters(self):
                for query in ("limit=0", "limit=-1", "limit=201", "offset=-1", "offset=bad", "exercise=" + "x"*101):
                    self.assertEqual(client.get("/api/v2/sessions/history?"+query, headers=headers).status_code, 422)

            def test_04_detail_zero_missing_and_saved_reports(self):
                zero = client.get("/api/v2/sessions/detail/b", headers=headers).json()["session"]
                self.assertEqual((zero["total_reps"], zero["avg_form_score"]), (0, 0))
                missing = client.get("/api/v2/sessions/detail/a", headers=headers).json()["session"]
                self.assertIsNone(missing["end_time"])
                self.assertIsNone(missing["total_reps"])
                self.assertIsNone(missing["avg_form_score"])
                report = client.get("/api/v2/ai_coach/reports?session_id=b&limit=1", headers=headers).json()
                self.assertEqual(report["reports"][0]["report_id"], "saved")
                self.assertEqual(client.get("/api/v2/ai_coach/reports/saved", headers=headers).json()["report"], saved)
                self.assertEqual(client.get("/api/v2/ai_coach/reports?session_id=older", headers=headers).json()["reports"], [])

            def test_05_login_and_static_entry(self):
                r = client.post("/api/auth/login", json={"username": "records-one", "password": "isolation-password"}).json()
                self.assertTrue(r["ok"])
                r = client.post("/api/auth/login", json={"username": "records-one", "password": "wrong-password"}).json()
                self.assertFalse(r["ok"])
                self.assertEqual(client.get("/app", follow_redirects=False).status_code, 307)
                self.assertEqual(client.get("/static/records.html").status_code, 200)
                self.assertIn('/static/records.html', client.get("/static/index.html").text)

            def test_99_no_archives_table(self):
                with closing(connect(db)) as c, c:
                    c.execute("DROP TABLE ai_coach_reports")
                r = client.get("/api/v2/sessions/history", headers=headers)
                self.assertEqual(r.status_code, 200)
                self.assertTrue(all(x["has_report"] == 0 for x in r.json()["sessions"]))

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RecordsContract)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        client.close()
        return result.wasSuccessful()


if __name__ == "__main__":
    if "--isolated" in sys.argv:
        raise SystemExit(0 if run_isolated() else 1)
    test_web_records_contract()
    print("Real auth / records / archived reports / static entry: PASS")
