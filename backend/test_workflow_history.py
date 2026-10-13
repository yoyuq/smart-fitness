"""History regressions; run: python -B backend/test_workflow_history.py.

Exercise the real FastAPI app, JWT validation and SQL against temporary data in
a child process. Startup hooks are skipped to avoid hardware and model calls.
"""
import importlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BACKEND = Path(__file__).resolve().parent


def _history_checks():
    from fastapi.testclient import TestClient
    import mqtt_client
    import requests

    check = unittest.TestCase()
    real_connect = sqlite3.connect
    with tempfile.TemporaryDirectory(prefix="fitness-history-") as temp:
        db = str(Path(temp) / "fitness.db")

        def isolated_connect(database, *args, **kwargs):
            # main.init_db runs at import time; redirect before importing it.
            return real_connect(db, *args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=isolated_connect), \
             mock.patch.object(requests.sessions.Session, "request",
                               side_effect=AssertionError("HTTP network disabled")), \
             mock.patch.object(mqtt_client.MQTTClientHandler, "connect",
                               side_effect=AssertionError("MQTT disabled")) as mqtt_connect:
            main = importlib.import_module("main")
            import ai_planner
            import auth

            check.assertIn("main_v2_extra", sys.modules)
            path = "/api/v2/sessions/history"
            check.assertIn(path, {getattr(route, "path", "") for route in main.app.routes})

            with mock.patch.object(ai_planner, "_call_llm",
                                   side_effect=AssertionError("LLM disabled")) as llm:
                # Without a context manager, TestClient does not run startup hooks.
                client = TestClient(main.app)
                try:
                    users = []
                    for username in ("history-owner", "history-other", "history-empty"):
                        response = client.post("/api/auth/register", json={
                            "username": username, "password": "synthetic-history-password",
                        })
                        check.assertEqual(response.status_code, 200, response.text)
                        user = response.json()
                        check.assertTrue(user["ok"], user)
                        users.append(user)
                    owner, other, empty = users
                    headers = {"Authorization": "Bearer " + owner["token"]}

                    # More than the maximum, with another user's newer records
                    # interleaved, makes both truncation and SQL ownership visible.
                    rows = []
                    for i in range(225):
                        for user in (owner, other):
                            rows.append((
                                f"history-{user['user_id']}-{i}", "synthetic-device",
                                str(user["user_id"]), "squat",
                                1_700_000_000 + i * 10 + (user is other),
                                1_700_000_005 + i * 10, i, 90.0, "completed",
                            ))
                    conn = real_connect(db)
                    try:
                        conn.executemany(
                            "INSERT INTO sessions (session_id, device_id, user_id, exercise_type, "
                            "start_time, end_time, total_reps, avg_form_score, status) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows,
                        )
                        conn.commit()
                        before = conn.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
                    finally:
                        conn.close()

                    fields = {"session_id", "device_id", "user_id", "exercise_type",
                              "start_time", "end_time", "total_reps", "avg_form_score", "status"}

                    def assert_history(params, count, user=owner):
                        response = client.get(path, params=params, headers={
                            "Authorization": "Bearer " + user["token"],
                        })
                        check.assertEqual(response.status_code, 200, response.text)
                        payload = response.json()
                        check.assertEqual(set(payload), {"sessions"})
                        sessions = payload["sessions"]
                        check.assertIsInstance(sessions, list)
                        check.assertEqual(len(sessions), count)
                        check.assertEqual(
                            [row["session_id"] for row in sessions],
                            [f"history-{user['user_id']}-{i}" for i in range(224, 224 - count, -1)],
                        )
                        for row in sessions:
                            check.assertEqual(set(row), fields)
                            check.assertEqual(row["user_id"], str(user["user_id"]))
                        check.assertEqual(
                            [row["start_time"] for row in sessions],
                            sorted((row["start_time"] for row in sessions), reverse=True),
                        )

                    assert_history({}, 50)
                    for limit in (1, 50, 199, 200):
                        assert_history({"limit": limit}, limit)
                    assert_history({"limit": 200, "user_id": other["user_id"]}, 200)
                    assert_history({"user_id": owner["user_id"]}, 50, user=other)
                    assert_history({"user_id": owner["user_id"]}, 0, user=empty)

                    for invalid in ("-1", "-200", "0", "201", "99999999999999999999",
                                    "1.5", "abc", "", "null"):
                        response = client.get(path, params={"limit": invalid}, headers=headers)
                        check.assertEqual(response.status_code, 422, (invalid, response.text))
                        details = response.json()["detail"]
                        check.assertIsInstance(details, list)
                        check.assertTrue(any(item["loc"] == ["query", "limit"] for item in details))

                    # Keep the real decoder in the request path for JWT failures.
                    import jwt
                    expired = jwt.encode({
                        "user_id": owner["user_id"], "username": owner["username"],
                        "exp": 1,
                    }, auth._SECRET, algorithm=auth._JWT_ALGO)
                    for authorization in (None, "Bearer invalid-token", "Bearer " + expired):
                        response = client.get(path, headers=(
                            {} if authorization is None else {"Authorization": authorization}
                        ))
                        check.assertEqual(response.status_code, 401, response.text)
                        check.assertEqual(response.json(), {"ok": False, "error": "unauthorized"})

                    conn = real_connect(db)
                    try:
                        check.assertEqual(
                            conn.execute("SELECT * FROM sessions ORDER BY session_id").fetchall(), before,
                        )
                    finally:
                        conn.close()
                    llm.assert_not_called()
                    mqtt_connect.assert_not_called()
                finally:
                    client.close()


class HistoryRouteTests(unittest.TestCase):
    def test_real_app_history_contract(self):
        env = dict(os.environ, AI_AGENT_MCP_SERVERS_JSON="[]", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--history-checks"],
            cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--history-checks"]:
        _history_checks()
    else:
        unittest.main(verbosity=2)
