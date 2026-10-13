"""Streak regressions: python -B backend/test_workflow_streak.py.

Exercise the real FastAPI route and JWT authentication with temporary SQLite
data. Run in a child process to isolate app imports; skip hardware startup hooks.
"""
import importlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        local = cls(2026, 1, 2, 12)
        return local if tz is None else local.astimezone(tz)


def _route_checks():
    from fastapi.testclient import TestClient
    import auth
    import mqtt_client
    import requests

    real_connect = sqlite3.connect
    check = unittest.TestCase()
    today = FrozenDatetime.now().date()
    endpoint = "/api/v2/stats/streak"

    def expected(current, longest, last_offset):
        return {
            "ok": True,
            "current_streak": current,
            "longest_streak": longest,
            "last_active": (
                str(today + timedelta(days=last_offset))
                if last_offset is not None else None
            ),
        }

    with tempfile.TemporaryDirectory(prefix="fitness-streak-") as temp:
        db = str(Path(temp) / "fitness.db")

        def isolated_connect(database, *args, **kwargs):
            # main initializes SQLite at import time, before DB_PATH can be patched.
            return real_connect(db, *args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=isolated_connect), \
             mock.patch.object(requests.sessions.Session, "request",
                               side_effect=AssertionError("HTTP network disabled")), \
             mock.patch.object(mqtt_client.MQTTClientHandler, "connect",
                               side_effect=AssertionError("MQTT disabled")) as mqtt_connect:
            main = importlib.import_module("main")
            check.assertIn("main_v2_extra", sys.modules)
            headers = {
                "Authorization": "Bearer " + auth.generate_token(11, "streak-user"),
            }
            other_headers = {
                "Authorization": "Bearer " + auth.generate_token(22, "other-user"),
            }
            conn = real_connect(db)
            # No context manager: entering TestClient starts MQTT/model hooks.
            client = TestClient(main.app)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS exercise_log "
                             "(user_id INTEGER, created_at REAL)")
                # Offsets are local calendar days, including the year boundary.
                cases = [
                    ("empty", [], [], expected(0, 0, None)),
                    ("today", [0], [], expected(1, 1, 0)),
                    ("yesterday", [-1], [], expected(1, 1, -1)),
                    ("continuous_today", [0, -1, -2], [], expected(3, 3, 0)),
                    ("continuous_yesterday", [-1, -2, -3], [], expected(3, 3, -1)),
                    ("gap_today", [0, -2, -3], [], expected(1, 2, 0)),
                    ("gap_yesterday", [-1, -3, -4], [], expected(1, 2, -1)),
                    ("inactive", [-2, -3, -4], [], expected(0, 3, -2)),
                    ("duplicates", [-1, -1, -2, -2], [], expected(2, 2, -1)),
                    ("historical_longest", [0, -1, -5, -6, -7, -8], [],
                     expected(2, 4, 0)),
                    ("other_user_only", [], [0, -1, -2], expected(0, 0, None)),
                    ("other_user_cannot_fill_gap", [0, -2], [-1], expected(1, 1, 0)),
                ]
                for name, own_days, other_days, wanted in cases:
                    conn.execute("DELETE FROM exercise_log")
                    for user_id, offsets in ((11, own_days), (22, other_days)):
                        for index, offset in enumerate(offsets):
                            local = datetime.combine(
                                today + timedelta(days=offset), datetime.min.time()
                            ) + timedelta(hours=12, seconds=index)
                            conn.execute(
                                "INSERT INTO exercise_log (user_id, created_at) VALUES (?, ?)",
                                (user_id, local.timestamp()),
                            )
                    conn.commit()
                    with mock.patch("datetime.datetime", FrozenDatetime):
                        response = client.get(endpoint, headers=headers)
                    check.assertEqual(response.status_code, 200, name)
                    check.assertEqual(response.json(), wanted, name)

                # The final dataset gives the other user an independent yesterday streak.
                with mock.patch("datetime.datetime", FrozenDatetime):
                    response = client.get(endpoint, headers=other_headers)
                check.assertEqual(response.status_code, 200)
                check.assertEqual(response.json(), expected(1, 1, -1))
                for unauthorized in ({}, {"Authorization": "Bearer invalid-token"}):
                    response = client.get(endpoint, headers=unauthorized)
                    check.assertEqual(response.status_code, 401)
                    check.assertEqual(response.json(), {"ok": False, "error": "unauthorized"})
                mqtt_connect.assert_not_called()
            finally:
                client.close()
                conn.close()


class StreakRouteTests(unittest.TestCase):
    def test_real_streak_route_with_isolated_calendar_cases(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--route-checks"],
            cwd=BACKEND,
            env=dict(os.environ, AI_AGENT_MCP_SERVERS_JSON="[]", PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--route-checks"]:
        _route_checks()
    else:
        unittest.main(verbosity=2)
