"""Offline bootstrap regressions; run: python -B backend/test_workflow_bootstrap.py.

All databases are synthetic. The route smoke check uses a fresh interpreter and
the real FastAPI application without running its MQTT/model startup hooks.
"""
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXPECTED_TOOLS = {
    "get_user_context_snapshot", "get_body_metrics", "get_recent_workouts",
    "get_exercise_summary", "get_session_rep_scores", "get_rep_analysis",
    "get_last_training_analysis", "get_scoring_evidence", "get_active_plans",
    "get_coach_memory", "get_memory_snapshot", "search_fitness_kb",
    "search_fitness_web", "analyze_rep_image", "analyze_rep_two_stage",
    "analyze_session_two_stage", "vision_providers_status",
    "analyze_pose_image_url", "list_ai_coach_reports", "get_ai_coach_report",
    "todo_write", "save_coach_memory",
    "update_body_metrics", "draft_workout_plan", "create_workout_plan",
    "delete_workout_plan",
}


class ToolkitBootstrapTests(unittest.TestCase):
    def test_compatibility_exports_and_registered_tools(self):
        from fitness_agent import toolkit, tools
        from fitness_agent.toolkit.registry import tool_names

        self.assertIs(tools.TOOL_SPECS, toolkit.TOOL_SPECS)
        self.assertIs(tools.execute_tool, toolkit.execute_tool)
        self.assertIs(tools.tool_specs_for_prompt, toolkit.tool_specs_for_prompt)
        self.assertEqual({item["name"] for item in toolkit.TOOL_SPECS}, EXPECTED_TOOLS)
        self.assertEqual(len(toolkit.TOOL_SPECS), len(EXPECTED_TOOLS))
        self.assertEqual(set(tool_names()), EXPECTED_TOOLS)
        with mock.patch.object(toolkit, "list_mcp_tool_specs", return_value=[]):
            specs = json.loads(tools.tool_specs_for_prompt())
        self.assertEqual({item["name"] for item in specs}, EXPECTED_TOOLS)

    def test_real_internal_handler_filters_user_data(self):
        from fitness_agent.tools import execute_tool

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE user_body_metrics (user_id INTEGER, weight_kg REAL, "
                     "height_cm REAL, body_fat_pct REAL, timestamp REAL)")
        conn.executemany("INSERT INTO user_body_metrics VALUES (?, ?, ?, ?, ?)", [
            (7, 60.0, 170.0, 20.0, 1.0), (8, 90.0, 180.0, 25.0, 2.0),
        ])
        result = execute_tool(conn, 7, "get_body_metrics", {"limit": 5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["metrics"], [{
            "weight_kg": 60.0, "height_cm": 170.0,
            "body_fat_pct": 20.0, "timestamp": 1.0,
        }])
        self.assertEqual(execute_tool(conn, 7, "not_allowed")["error_type"], "unknown_tool")

    def test_mcp_dispatch_and_prompt_composition(self):
        from fitness_agent import toolkit

        external = {"name": "mcp__bootstrap__echo", "description": "synthetic tool"}
        with mock.patch.object(toolkit, "list_mcp_tool_specs", return_value=[external]):
            self.assertIn(external, json.loads(toolkit.tool_specs_for_prompt()))
        with mock.patch.object(toolkit, "call_mcp_tool", return_value={"ok": True}) as call:
            self.assertEqual(toolkit.execute_tool(None, 7, external["name"], {"x": 1}),
                             {"ok": True})
            call.assert_called_once_with(external["name"], {"x": 1})


class AuthBootstrapTests(unittest.TestCase):
    def setUp(self):
        import auth

        self.auth = auth
        temp = tempfile.TemporaryDirectory(prefix="fitness-auth-bootstrap-")
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "auth.db")
        patcher = mock.patch.object(auth, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def initialize_and_assert_closed(self):
        conn = self.auth._get_conn()
        with mock.patch.object(self.auth, "_get_conn", return_value=conn):
            self.auth.init_auth_db()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_empty_database_and_repeated_initialization(self):
        self.initialize_and_assert_closed()
        self.initialize_and_assert_closed()
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"users", "workout_plans", "devices", "rate_limits"} <= tables)
        conn.execute("INSERT INTO devices (device_id) VALUES ('new-device')")
        conn.commit()
        self.assertEqual(conn.execute("SELECT device_type, user_id FROM devices").fetchone(),
                         ("phone", None))
        self.initialize_and_assert_closed()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)

    def test_legacy_and_partially_migrated_devices_keep_rows(self):
        for has_type, has_user in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(device_type=has_type, user_id=has_user):
                conn = sqlite3.connect(self.db)
                try:
                    conn.execute("DROP TABLE IF EXISTS devices")
                    conn.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT, "
                                 "firmware_version TEXT, last_seen REAL, wifi_rssi REAL, "
                                 "status TEXT DEFAULT 'offline')")
                    if has_type:
                        conn.execute("ALTER TABLE devices ADD COLUMN device_type TEXT DEFAULT 'phone'")
                    if has_user:
                        conn.execute("ALTER TABLE devices ADD COLUMN user_id INTEGER")
                    conn.execute("INSERT INTO devices (device_id, name, firmware_version, last_seen, "
                                 "wifi_rssi, status) VALUES ('legacy', 'saved', 'v1', 123, -50, 'online')")
                    if has_type:
                        conn.execute("UPDATE devices SET device_type='esp32'")
                    if has_user:
                        conn.execute("UPDATE devices SET user_id=41")
                    conn.commit()
                    self.initialize_and_assert_closed()
                    self.initialize_and_assert_closed()
                    self.assertEqual(conn.execute(
                        "SELECT device_id, name, firmware_version, last_seen, wifi_rssi, status, "
                        "device_type, user_id FROM devices"
                    ).fetchall(), [("legacy", "saved", "v1", 123.0, -50.0, "online",
                                    "esp32" if has_type else "phone", 41 if has_user else None)])
                finally:
                    conn.close()

    def test_connection_closes_when_creation_migration_or_commit_fails(self):
        for phase in ("create", "migrate", "commit"):
            with self.subTest(phase=phase):
                class FailingConnection(sqlite3.Connection):
                    def executescript(self, sql):
                        if phase == "create":
                            raise sqlite3.OperationalError("synthetic create failure")
                        return super().executescript(sql)

                    def execute(self, sql, *args):
                        if phase == "migrate" and sql.startswith("ALTER TABLE"):
                            raise sqlite3.OperationalError("synthetic migrate failure")
                        return super().execute(sql, *args)

                    def commit(self):
                        if phase == "commit":
                            raise sqlite3.OperationalError("synthetic commit failure")
                        return super().commit()

                conn = sqlite3.connect(":memory:", factory=FailingConnection)
                conn.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY)")
                with mock.patch.object(self.auth, "_get_conn", return_value=conn):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic"):
                        self.auth.init_auth_db()
                with self.assertRaises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")

    def test_migration_keeps_extra_values_constraints_and_dependents(self):
        self.auth.init_auth_db()
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        conn.executescript("""
            DROP TABLE devices;
            INSERT INTO users (id, username, password_hash) VALUES (41, 'saved', 'synthetic');
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY, device_type TEXT DEFAULT 'phone',
                name TEXT, user_id INTEGER, last_seen REAL,
                token TEXT UNIQUE, note TEXT DEFAULT 'a, ''quoted'' (note)',
                CHECK (length(note) > 0),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            INSERT INTO devices (device_id, device_type, name, user_id, last_seen, token)
                VALUES ('saved', 'esp32', 'old name', 41, 12, 'saved-token');
            CREATE INDEX devices_owner ON devices(user_id);
            CREATE TABLE device_audit (device_id TEXT, name TEXT);
            CREATE TRIGGER devices_audit AFTER UPDATE OF name ON devices BEGIN
                INSERT INTO device_audit VALUES (NEW.device_id, NEW.name);
            END;
            CREATE TABLE device_children (device_id TEXT REFERENCES devices(device_id) ON DELETE CASCADE);
            INSERT INTO device_children VALUES ('saved');
        """)
        objects = conn.execute("SELECT name, sql FROM sqlite_master WHERE tbl_name='devices' "
                               "AND type IN ('index', 'trigger') ORDER BY name").fetchall()
        foreign_keys = conn.execute("PRAGMA foreign_key_list(devices)").fetchall()
        original_fields = "device_id, device_type, name, user_id, last_seen, token, note"
        original = conn.execute("SELECT " + original_fields + " FROM devices").fetchall()
        migrating = self.auth._get_conn()
        migrating.execute("PRAGMA foreign_keys=ON")
        with mock.patch.object(self.auth, "_get_conn", return_value=migrating):
            self.auth.init_auth_db()
        self.initialize_and_assert_closed()
        self.assertEqual(conn.execute("SELECT " + original_fields + " FROM devices").fetchall(), original)
        self.assertEqual(conn.execute("SELECT * FROM device_children").fetchall(), [("saved",)])
        self.assertEqual(conn.execute("PRAGMA foreign_key_list(devices)").fetchall(), foreign_keys)
        self.assertEqual(conn.execute("SELECT name, sql FROM sqlite_master WHERE tbl_name='devices' "
                                      "AND type IN ('index', 'trigger') ORDER BY name").fetchall(), objects)
        conn.execute("UPDATE devices SET name='updated' WHERE device_id='saved'")
        self.assertEqual(conn.execute("SELECT * FROM device_audit").fetchall(), [("saved", "updated")])
        conn.execute("INSERT INTO devices (device_id) VALUES ('new')")
        self.assertEqual(conn.execute("SELECT note FROM devices WHERE device_id='new'").fetchone(),
                         ("a, 'quoted' (note)",))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO devices (device_id, token) VALUES ('duplicate', 'saved-token')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO devices (device_id, note) VALUES ('invalid', '')")

    def test_failed_rebuild_rolls_back_original_table_and_rows(self):
        self.auth.init_auth_db()
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        conn.executescript("""
            DROP TABLE devices;
            CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT, user_id INTEGER);
            CREATE INDEX saved_owner ON devices(user_id);
            INSERT INTO devices VALUES ('saved', 'saved name', 41);
        """)
        before = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()

        class FailingRename(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE devices_bootstrap_new"):
                    raise sqlite3.OperationalError("synthetic rename failure")
                return super().execute(sql, *args)

        migrating = sqlite3.connect(self.db, factory=FailingRename)
        with mock.patch.object(self.auth, "_get_conn", return_value=migrating):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic rename failure"):
                self.auth.init_auth_db()
        with self.assertRaises(sqlite3.ProgrammingError):
            migrating.execute("SELECT 1")
        self.assertEqual(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall(), before)
        self.assertEqual(conn.execute("SELECT * FROM devices").fetchall(), [("saved", "saved name", 41)])


def _route_smoke():
    """Run only in a child process so imported app globals cannot leak to other tests."""
    from fastapi.testclient import TestClient
    import mqtt_client
    import requests

    real_connect = sqlite3.connect
    with tempfile.TemporaryDirectory(prefix="fitness-route-bootstrap-") as temp:
        db = str(Path(temp) / "fitness.db")

        def isolated_connect(database, *args, **kwargs):
            # main.init_db executes at import time, before DB_PATH can be patched.
            return real_connect(db, *args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=isolated_connect), \
             mock.patch.object(requests.sessions.Session, "request",
                               side_effect=AssertionError("HTTP network disabled")), \
             mock.patch.object(mqtt_client.MQTTClientHandler, "connect",
                               side_effect=AssertionError("MQTT disabled")) as mqtt_connect:
            main = importlib.import_module("main")
            import ai_planner

            check = unittest.TestCase()
            # A swallowed import failure must fail the smoke test rather than yield a partial app.
            check.assertIn("main_v2_routes", sys.modules)
            check.assertIn("main_v2_extra", sys.modules)
            routes = {getattr(route, "path", "") for route in main.app.routes}
            check.assertTrue({"/api/auth/profile", "/api/v2/auth/profile",
                              "/api/v2/training/start", "/api/v2/sessions/history",
                              "/api/v2/agent/chat", "/api/v2/agent/kb"} <= routes)
            with mock.patch.object(ai_planner, "_call_llm",
                                   side_effect=AssertionError("LLM disabled")) as llm:
                # No context manager: entering TestClient would run hardware startup hooks.
                client = TestClient(main.app)
                try:
                    check.assertEqual(client.get("/api/v2/agent/kb").status_code, 401)
                    registered = client.post("/api/auth/register", json={
                        "username": "bootstrap-user", "password": "synthetic-password",
                    })
                    check.assertEqual(registered.status_code, 200)
                    data = registered.json()
                    check.assertTrue(data["ok"], data)
                    headers = {"Authorization": "Bearer " + data["token"]}
                    for path in ("/api/auth/profile", "/api/v2/auth/profile"):
                        profile = client.get(path, headers=headers)
                        check.assertEqual(profile.status_code, 200)
                        check.assertEqual(profile.json()["user"]["id"], data["user_id"])
                    response = client.get("/api/v2/agent/kb", headers=headers)
                    check.assertEqual(response.status_code, 200)
                    check.assertTrue(response.json()["ok"])
                    check.assertTrue({"nutrition", "coach", "analysis", "plan"} <=
                                     {item["id"] for item in response.json()["domains"]})
                    llm.assert_not_called()
                    mqtt_connect.assert_not_called()
                finally:
                    client.close()


class RouteBootstrapTests(unittest.TestCase):
    def test_real_app_import_and_authenticated_routes(self):
        env = dict(os.environ, AI_AGENT_MCP_SERVERS_JSON="[]", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--route-smoke"],
            cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def _shared_database_checks(order):
    """Exercise each startup order in its own interpreter and one real database."""
    from fastapi.testclient import TestClient
    import auth
    import mqtt_client
    import requests

    check = unittest.TestCase()
    real_connect = sqlite3.connect
    with tempfile.TemporaryDirectory(prefix="fitness-shared-bootstrap-") as temp:
        db = str(Path(temp) / "shared.db")
        if order == "legacy-auth":
            conn = real_connect(db)
            try:
                conn.execute("""CREATE TABLE devices (
                    device_id TEXT PRIMARY KEY,
                    device_type TEXT NOT NULL DEFAULT 'phone',
                    name TEXT DEFAULT '', user_id INTEGER, last_seen REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )""")
                conn.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?)",
                             ("saved-device", "esp32", "saved name", 730002, 12.0))
                conn.commit()
            finally:
                conn.close()

        def isolated_connect(database, *args, **kwargs):
            # main initializes before its DB_PATH can be assigned. Redirect
            # import-time connections from every module to this same file too.
            return real_connect(db, *args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=isolated_connect), \
             mock.patch.object(auth, "DB_PATH", db), \
             mock.patch.object(requests.sessions.Session, "request",
                               side_effect=AssertionError("HTTP network disabled")) as http, \
             mock.patch.object(mqtt_client.MQTTClientHandler, "connect",
                               side_effect=AssertionError("MQTT disabled")) as mqtt_connect:
            if order in ("auth-main", "legacy-auth"):
                auth.init_auth_db()
            main = importlib.import_module("main")
            main.DB_PATH = db
            check.assertEqual(auth.DB_PATH, main.DB_PATH)
            check.assertIn("main_v2_routes", sys.modules)
            check.assertIn("main_v2_extra", sys.modules)
            import ai_planner

            conn = real_connect(db)
            # Do not enter TestClient: its lifespan starts hardware/model hooks.
            client = TestClient(main.app, raise_server_exceptions=False)
            try:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(devices)")]
                check.assertEqual(columns[:6], ["device_id", "name", "firmware_version",
                                                "last_seen", "wifi_rssi", "status"])
                check.assertTrue({"device_type", "user_id", "registered_at"} <= set(columns))
                schema = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
                before = conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
                for _ in range(2):
                    auth.init_auth_db()
                    main.init_db()
                check.assertEqual(conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
                                  schema)
                check.assertEqual(conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall(),
                                  before)
                if order == "legacy-auth":
                    check.assertEqual(conn.execute(
                        "SELECT device_id, device_type, name, user_id, last_seen FROM devices"
                    ).fetchall(), [("saved-device", "esp32", "saved name", 730002, 12.0)])

                with mock.patch.object(ai_planner, "_call_llm",
                                       side_effect=AssertionError("LLM disabled")) as llm:
                    for device_id, payload, name, firmware in (
                        ("registered-device", {"name": "真实设备", "firmware_version": "test-v1"},
                         "真实设备", "test-v1"),
                        ("default-device", {}, "default-device", "unknown"),
                    ):
                        response = client.post("/api/devices/register", json={
                            "device_id": device_id, **payload,
                        })
                        check.assertEqual(response.status_code, 200, response.text)
                        check.assertEqual(response.json(), {"status": "registered", "device_id": device_id})
                        row = conn.execute(
                            "SELECT name, firmware_version, status, registered_at, last_seen, "
                            "device_type FROM devices WHERE device_id=?", (device_id,),
                        ).fetchone()
                        check.assertEqual(row[:3], (name, firmware, "online"))
                        check.assertIsInstance(row[3], (int, float))
                        check.assertGreater(row[3], 0)
                        check.assertAlmostEqual(row[3], row[4], delta=5)
                        check.assertEqual(row[5], "phone")
                    check.assertTrue(auth.register_device("auth-device", "phone", "auth name")["ok"])
                    listing = client.get("/api/devices")
                    check.assertEqual(listing.status_code, 200, listing.text)
                    fields = ("device_id", "name", "firmware_version", "last_seen", "wifi_rssi", "status")
                    expected = [dict(zip(fields, row)) for row in conn.execute(
                        "SELECT " + ", ".join(fields) + " FROM devices ORDER BY last_seen DESC"
                    )]
                    check.assertEqual(listing.json(), expected)
                    if order == "legacy-auth":
                        check.assertEqual(next(row for row in listing.json()
                                               if row["device_id"] == "saved-device"), {
                            "device_id": "saved-device", "name": "saved name", "firmware_version": None,
                            "last_seen": 12.0, "wifi_rssi": None, "status": "offline",
                        })
                    saved = conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
                    main.init_db()
                    auth.init_auth_db()
                    check.assertEqual(conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall(),
                                      saved)
                    llm.assert_not_called()
                mqtt_connect.assert_not_called()
                http.assert_not_called()
            finally:
                client.close()
                conn.close()


class SharedDatabaseBootstrapTests(unittest.TestCase):
    def check_order(self, order):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--shared-db", order],
            cwd=BACKEND,
            env=dict(os.environ, AI_AGENT_MCP_SERVERS_JSON="[]", PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_auth_then_main_share_database(self):
        self.check_order("auth-main")

    def test_main_then_auth_share_database(self):
        self.check_order("main-auth")

    def test_legacy_auth_table_shares_database(self):
        self.check_order("legacy-auth")


if __name__ == "__main__":
    if sys.argv[1:] == ["--route-smoke"]:
        _route_smoke()
    elif len(sys.argv) == 3 and sys.argv[1] == "--shared-db":
        _shared_database_checks(sys.argv[2])
    else:
        unittest.main(verbosity=2)
