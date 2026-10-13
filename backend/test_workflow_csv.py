"""CSV 回归测试：python -B backend/test_workflow_csv.py。

通过实际 FastAPI 应用、JWT 和临时 SQLite 发起 HTTP 请求；子进程隔离导入，
不执行涉及硬件、MQTT 和模型的启动钩子。
"""
import csv
import importlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


BACKEND = Path(__file__).resolve().parent
NOW = 1_800_000_000
DAY = 86400
COLUMNS = [
    "log_id", "exercise_type", "reps", "duration_s", "avg_form_score",
    "device_id", "created_at", "timestamp",
]


def _csv_checks():
    from fastapi.testclient import TestClient
    import jwt
    import mqtt_client
    import requests

    check = unittest.TestCase()
    real_connect = sqlite3.connect
    endpoint = "/api/v2/export/csv"
    with tempfile.TemporaryDirectory(prefix="fitness-csv-") as temp:
        db = str(Path(temp) / "fitness.db")

        def isolated_connect(database, *args, **kwargs):
            # main 导入时就会初始化 SQLite，须提前重定向所有连接。
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
            extra = sys.modules["main_v2_extra"]
            check.assertIn(endpoint, {getattr(route, "path", "") for route in main.app.routes})
            headers = {
                user_id: {"Authorization": "Bearer " + auth.generate_token(user_id, f"csv-{user_id}")}
                for user_id in (11, 22, 33)
            }
            exercise = '深蹲, "标准"\n动作'
            device = '设备, "一号"'
            rows = [
                (101, 11, exercise, 0, 0, 0.0, device, NOW - 1),
                (102, 11, "俯卧撑", 2, 20, 0, "phone", NOW - 2),
                (103, 11, "拉伸", 3, 30, None, "phone", NOW - 3),
                (104, 11, "squat", 4, 40, 87.5, "phone", NOW - DAY),
                (105, 11, "squat", 5, 50, 12.25, "phone", NOW - DAY - 1),
                (106, 11, "squat", 6, 60, 45.5, "phone", NOW - 365 * DAY),
                (107, 11, "squat", 7, 70, 60, "phone", NOW - 365 * DAY - 1),
                (108, 11, "squat", 8, 80, 70, "phone", NOW - 3650 * DAY),
                (109, 11, "squat", 9, 90, 90, "phone", NOW - 3650 * DAY - 1),
                (201, 22, "另一用户", 10, 100, 99, "other-device", NOW),
            ]
            conn = real_connect(db)
            # 不使用上下文管理器，避免 TestClient 触发 startup 钩子。
            client = TestClient(main.app)
            try:
                conn.execute("""CREATE TABLE IF NOT EXISTS exercise_log (
                    log_id INTEGER PRIMARY KEY, user_id INTEGER, exercise_type TEXT,
                    reps INTEGER, duration_s REAL, avg_form_score REAL,
                    device_id TEXT, created_at REAL
                )""")
                conn.executemany(
                    "INSERT INTO exercise_log (log_id, user_id, exercise_type, reps, "
                    "duration_s, avg_form_score, device_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
                before = conn.execute("SELECT * FROM exercise_log ORDER BY log_id").fetchall()
                originals = {row[0]: row for row in rows}

                def assert_export(params, expected_ids, user_id=11):
                    response = client.get(endpoint, params=params, headers=headers[user_id])
                    check.assertEqual(response.status_code, 200, response.text)
                    check.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
                    check.assertEqual(
                        response.headers["content-disposition"],
                        f"attachment; filename=workout_{user_id}_{NOW}.csv",
                    )
                    text = response.content.decode("utf-8")
                    parsed = list(csv.reader(io.StringIO(text, newline="")))
                    check.assertEqual(parsed[0], COLUMNS)
                    check.assertEqual([int(row[0]) for row in parsed[1:]], expected_ids)
                    for row in parsed[1:]:
                        check.assertEqual(len(row), 8)
                        original = originals[int(row[0])]
                        check.assertEqual(original[1], user_id)
                        check.assertEqual(row[1], original[2])
                        check.assertEqual(int(row[2]), original[3])
                        check.assertEqual(float(row[3]), original[4])
                        if original[5] is None:
                            check.assertEqual(row[4], "")
                        else:
                            check.assertNotEqual(row[4], "")
                            check.assertEqual(float(row[4]), original[5])
                        check.assertEqual(row[5], original[6])
                        check.assertEqual(float(row[6]), original[7])
                        check.assertEqual(
                            row[7], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(original[7])),
                        )
                    return text

                # 只冻结导出模块的时钟，JWT 仍使用真实时钟与签名校验。
                with mock.patch.object(extra, "time", SimpleNamespace(time=lambda: NOW)), \
                     mock.patch.object(ai_planner, "_call_llm",
                                       side_effect=AssertionError("LLM disabled")) as llm:
                    text = assert_export({}, list(range(101, 107)))
                    check.assertIn('"深蹲, ""标准""\n动作"', text)
                    check.assertIn('"设备, ""一号"""', text)
                    for days, expected_ids in (
                        (1, list(range(101, 105))),
                        (365, list(range(101, 107))),
                        (366, list(range(101, 108))),
                        (3650, list(range(101, 109))),
                    ):
                        assert_export({"days": days}, expected_ids)
                    assert_export({"days": 3650, "user_id": 22}, list(range(101, 109)))
                    assert_export({"user_id": 11}, [201], user_id=22)
                    empty = assert_export({"user_id": 11}, [], user_id=33)
                    check.assertEqual(empty, ",".join(COLUMNS) + "\r\n")

                    for invalid in ("-1", "0", "3651", "99999999999999999999",
                                    "1.5", "abc", "", "null", "NaN", "true"):
                        response = client.get(endpoint, params={"days": invalid}, headers=headers[11])
                        check.assertEqual(response.status_code, 422, (invalid, response.text))
                        details = response.json()["detail"]
                        check.assertIsInstance(details, list)
                        check.assertTrue(any(item["loc"] == ["query", "days"] for item in details))

                    expired = jwt.encode(
                        {"user_id": 11, "username": "csv-11", "exp": 1},
                        auth._SECRET, algorithm=auth._JWT_ALGO,
                    )
                    for authorization in (None, "Bearer invalid-token", "Bearer " + expired):
                        response = client.get(endpoint, headers=(
                            {} if authorization is None else {"Authorization": authorization}
                        ))
                        check.assertEqual(response.status_code, 401, response.text)
                        check.assertEqual(response.json(), {"ok": False, "error": "unauthorized"})
                    llm.assert_not_called()
                mqtt_connect.assert_not_called()
                check.assertEqual(
                    conn.execute("SELECT * FROM exercise_log ORDER BY log_id").fetchall(), before,
                )
            finally:
                client.close()
                conn.close()


class CsvRouteTests(unittest.TestCase):
    def test_real_app_csv_contract(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--csv-checks"],
            cwd=BACKEND,
            env=dict(os.environ, AI_AGENT_MCP_SERVERS_JSON="[]", PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--csv-checks"]:
        _csv_checks()
    else:
        unittest.main(verbosity=2)
