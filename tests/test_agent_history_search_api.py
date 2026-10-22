"""Integration tests for GET /api/v2/agent/history/search.

Validates auth, input validation (422), successful search behavior,
user isolation, and compatibility with existing history routes.

Uses stdlib unittest + FastAPI TestClient with a temporary SQLite DB.

The test module patches ``sqlite3.connect`` BEFORE importing any backend
modules, so every connection that would open the production
``backend/fitness.db`` is redirected to a single shared temporary DB.
This guarantees the checkout database is never touched, even by module
imports or transitive dependencies.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup: make the backend package importable when running from repo root
# via ``python -m unittest discover``.
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
_BACKEND_DIR = os.path.normpath(_BACKEND_DIR)
if os.path.abspath(_BACKEND_DIR) not in [os.path.abspath(p) for p in sys.path]:
    sys.path.insert(0, _BACKEND_DIR)

# ---------------------------------------------------------------------------
# Production DB path (absolute, normalized).  Any sqlite3.connect call whose
# database argument resolves to this path is redirected to the temp DB.
# ---------------------------------------------------------------------------
_PRODUCTION_DB = os.path.abspath(os.path.join(_BACKEND_DIR, "fitness.db"))


def _make_temp_db():
    """Create a temporary SQLite file and return (connection, path).

    Caller is responsible for closing the connection before removing the
    file.  On Windows a SQLite connection held open will prevent file
    deletion even when using a context manager for the cursor, so we
    always explicitly ``conn.close()`` in cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="api_search_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn, path


class AgentHistorySearchApiTests(unittest.TestCase):
    """Test the /api/v2/agent/history/search HTTP endpoint.

    A single shared temporary DB is used for all tests.  ``sqlite3.connect``
    is patched before any backend imports so that module-level code and
    endpoint handlers never open the real ``backend/fitness.db``.
    """

    # Class-level fixtures set up once in setUpClass.
    _patcher = None
    _conn = None
    _db_path = None
    _connections_opened_to_checkout = 0
    _connections_opened_to_temp = 0
    app = None
    user1_token = None
    user2_token = None
    _client = None

    @classmethod
    def setUpClass(cls):
        """Patch sqlite3.connect, create temp DB, then import and seed.

        Order is critical:
          1. Create the temp DB file (so its path is known).
          2. Install the sqlite3.connect redirect patch.
          3. Import backend modules (they will all hit the temp DB).
          4. Seed test data.

        The patch counts how many times a caller tried to open the
        production checkout DB; tests assert this count stays at zero.
        """
        # 1. Create the shared temporary DB.
        cls._conn, cls._db_path = _make_temp_db()

        # Track connections opened to each database, keyed by thread id.
        cls._temp_connections = []  # list of (thread_id, connection)
        cls._checkout_open_count = 0  # actual opens of the real checkout DB
        cls._redirect_count = 0  # redirections from checkout path to temp
        original_connect = sqlite3.connect
        temp_db_abs = os.path.abspath(cls._db_path)

        def patched_connect(database, *args, **kwargs):
            db_str = str(database)
            # :memory: and other non-file paths pass through unchanged.
            if db_str == ":memory:":
                return original_connect(database, *args, **kwargs)
            try:
                db_abs = os.path.abspath(db_str)
            except Exception:
                return original_connect(database, *args, **kwargs)

            redirected = False
            if db_abs == _PRODUCTION_DB:
                # Redirect to temp DB.
                cls._redirect_count += 1
                database = cls._db_path
                db_abs = temp_db_abs
                redirected = True

            conn = original_connect(database, *args, **kwargs)

            # Verify the ACTUAL opened database file is not the checkout DB.
            # Use PRAGMA database_list to inspect the real file path after
            # connection — this proves the checkout DB was never opened.
            try:
                row = conn.execute("PRAGMA database_list").fetchone()
                if row:
                    actual_file = row[2]  # "file" column
                    if actual_file and os.path.abspath(actual_file) == _PRODUCTION_DB:
                        cls._checkout_open_count += 1
            except Exception:
                pass

            # Track same-thread connections to temp DB so we can close them.
            if db_abs == temp_db_abs:
                cls._temp_connections.append((threading.get_ident(), conn))
            return conn

        # 2. Install the patch BEFORE any backend imports.
        cls._patcher = patch("sqlite3.connect", patched_connect)
        cls._patcher.start()

        # Sanity: no checkout opens yet.
        assert cls._checkout_open_count == 0, (
            "checkout DB opened before imports"
        )

        # 3. Import backend modules (all sqlite3.connect calls are redirected).
        import main  # noqa: F401  — triggers app construction
        import auth
        import main_v2_extra
        from fitness_agent.history import (
            add_agent_chat_message,
            ensure_agent_chat_schema,
        )
        from main import app

        cls.app = app
        cls._auth = auth
        cls._main_v2_extra = main_v2_extra

        # Assert imports did NOT open the checkout database.
        assert cls._checkout_open_count == 0, (
            f"import opened checkout DB ({cls._checkout_open_count} times)"
        )
        # Sanity: the patch did intercept at least one production-path
        # request (proves the redirect is active, not a no-op).
        assert cls._redirect_count > 0, (
            "no redirects occurred — patch may not be intercepting connect calls"
        )

        # 4. Seed test data.
        ensure_agent_chat_schema(cls._conn)

        # Seed messages for user 1 (mixed content).
        seed_messages = [
            ("user", "Hello, how are you?"),
            ("assistant", "I am doing well, thanks for asking the question!"),
            ("user", "I want to build the muscle mass you suggested"),
            ("assistant", "Great goal! Here's a muscle building plan for you..."),
            ("user", "What about the cardio training you mentioned?"),
            ("assistant", "Cardio is important for the overall fitness you want."),
            ("user", "thanks for the help you gave"),
            ("assistant", "You're welcome! Keep training hard the best you can."),
        ]
        for idx, (role, content) in enumerate(seed_messages):
            add_agent_chat_message(
                cls._conn, 1, role, content, created_at=1000 + idx
            )

        # Seed user 2 with one message that contains "muscle" (for isolation test).
        add_agent_chat_message(
            cls._conn, 2, "user",
            "I also want to build muscle",
            created_at=2000,
        )

        cls._conn.commit()

        # Generate valid tokens for test users.
        cls.user1_token = auth.generate_token(1, "testuser1")
        cls.user2_token = auth.generate_token(2, "testuser2")

        # Pre-create one TestClient we'll reuse (closed in tearDownClass).
        from fastapi.testclient import TestClient
        cls._client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        """Close all same-thread temp connections, stop patch, remove file."""
        # Close the shared client first (it may hold a connection internally).
        try:
            cls._client.close()
        except Exception:
            pass

        # Explicitly close every same-thread connection we tracked to the
        # temp DB.  On Windows an open handle prevents file deletion.
        this_thread = threading.get_ident()
        for owner, conn in cls._temp_connections:
            if owner == this_thread:
                try:
                    conn.close()
                except Exception:
                    pass

        # Stop the patch (must happen before file removal on some paths).
        try:
            cls._patcher.stop()
        except Exception:
            pass

        # Remove the temp DB file.
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def setUp(self):
        """Reset the checkout-open counter at the start of each test."""
        # We track opens per-test for fine-grained assertions.
        self._open_count_before = type(self)._checkout_open_count

    def _assert_no_checkout_db_opened(self):
        """Assert this test did not cause any opens of the checkout DB."""
        count = type(self)._checkout_open_count - self._open_count_before
        self.assertEqual(
            count, 0,
            f"test opened checkout fitness.db {count} time(s)",
        )

    # ------------------------------------------------------------------
    # Fixture integrity: DB isolation assertion
    # ------------------------------------------------------------------
    def test_import_and_requests_do_not_open_checkout_db(self):
        """Neither module imports nor endpoint requests touch the real DB.

        This is the compact assertion that the sqlite3.connect patch is
        effective: both at import time and during request handling, the
        checkout ``backend/fitness.db`` path is never actually opened.
        We also verify redirections DID occur, proving the patch is active
        and not a no-op.
        """
        redirects_before = type(self)._redirect_count
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        self._assert_no_checkout_db_opened()
        # Prove the patch intercepted at least one connect call during
        # this request (the search endpoint opens a DB connection).
        self.assertGreater(
            type(self)._redirect_count, redirects_before,
            "no DB redirects during request — patch may be inactive",
        )

    # ------------------------------------------------------------------
    # Auth tests
    # ------------------------------------------------------------------
    def test_unauthenticated_returns_401(self):
        """Request without auth token returns 401."""
        r = self._client.get("/api/v2/agent/history/search?q=hello")
        self.assertEqual(r.status_code, 401)
        self._assert_no_checkout_db_opened()

    def test_invalid_token_returns_401(self):
        """Request with invalid token returns 401."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        self.assertEqual(r.status_code, 401)
        self._assert_no_checkout_db_opened()

    # ------------------------------------------------------------------
    # Query validation (422) tests
    # ------------------------------------------------------------------
    def test_missing_q_returns_422(self):
        """Request with no q parameter returns 422 (default "" is blank)."""
        r = self._client.get(
            "/api/v2/agent/history/search",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_empty_q_returns_422(self):
        """Request with q= (empty string) returns 422."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_whitespace_only_q_returns_422(self):
        """Request with whitespace-only q returns 422."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=%20%20%09%0A",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_q_too_long_returns_422(self):
        """Request with q longer than 200 chars returns 422."""
        long_q = "a" * 201
        r = self._client.get(
            f"/api/v2/agent/history/search?q={long_q}",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_q_200_chars_ok(self):
        """q of exactly 200 chars is accepted (200 OK or empty results)."""
        q_200 = "x" * 200
        r = self._client.get(
            f"/api/v2/agent/history/search?q={q_200}",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        # Should not be 422 — either 200 with empty list or some matches.
        self.assertNotEqual(r.status_code, 422)
        self.assertEqual(r.status_code, 200)
        self._assert_no_checkout_db_opened()

    # ------------------------------------------------------------------
    # Limit validation (422) tests
    # ------------------------------------------------------------------
    def test_limit_zero_returns_422(self):
        """limit=0 returns 422."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello&limit=0",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_limit_negative_returns_422(self):
        """Negative limit returns 422."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello&limit=-5",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_limit_over_200_returns_422(self):
        """limit > 200 returns 422."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello&limit=201",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_limit_non_integer_returns_422(self):
        """Non-integer limit (e.g. 'abc') returns 422 (FastAPI validation)."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello&limit=abc",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_limit_float_returns_422(self):
        """Float limit (e.g. '10.5') returns 422 (FastAPI validation)."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=hello&limit=10.5",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()

    def test_limit_200_ok(self):
        """limit=200 is accepted."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=the&limit=200",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        self._assert_no_checkout_db_opened()

    def test_limit_1_ok(self):
        """limit=1 is accepted."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=the&limit=1",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        self._assert_no_checkout_db_opened()

    # ------------------------------------------------------------------
    # Successful search tests
    # ------------------------------------------------------------------
    def test_search_success_returns_ok_true_and_messages(self):
        """Successful search returns {ok: true, messages: [...]}."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=muscle",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["messages"], list)
        self.assertEqual(len(data["messages"]), 2)
        self._assert_no_checkout_db_opened()

    def test_search_user_isolation(self):
        """Search only returns messages belonging to the authenticated user."""
        # User 1 has 2 "muscle" messages
        r1 = self._client.get(
            "/api/v2/agent/history/search?q=muscle",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(r1.json()["messages"]), 2)

        # User 2 has 1 "muscle" message
        r2 = self._client.get(
            "/api/v2/agent/history/search?q=muscle",
            headers={"Authorization": f"Bearer {self.user2_token}"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.json()["messages"]), 1)
        self._assert_no_checkout_db_opened()

    def test_search_results_id_ascending(self):
        """Results are returned in ID ascending (chronological) order."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=you",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        messages = r.json()["messages"]
        self.assertGreater(len(messages), 1)
        ids = [m["id"] for m in messages]
        self.assertEqual(ids, sorted(ids))
        self._assert_no_checkout_db_opened()

    def test_search_default_limit_50(self):
        """When limit is not specified, default is 50."""
        # "the" should match several messages but fewer than 50.
        r = self._client.get(
            "/api/v2/agent/history/search?q=the",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        messages = r.json()["messages"]
        self.assertGreater(len(messages), 0)
        self.assertLessEqual(len(messages), 50)
        self._assert_no_checkout_db_opened()

    def test_search_no_matches_returns_empty_list(self):
        """Search for non-existent text returns empty list."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=nonexistent_xyz_12345",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["messages"], [])
        self._assert_no_checkout_db_opened()

    def test_search_case_insensitive_ascii(self):
        """ASCII search is case-insensitive."""
        r_upper = self._client.get(
            "/api/v2/agent/history/search?q=CARDIO",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        r_lower = self._client.get(
            "/api/v2/agent/history/search?q=cardio",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r_upper.status_code, 200)
        self.assertEqual(r_lower.status_code, 200)
        self.assertEqual(
            len(r_upper.json()["messages"]),
            len(r_lower.json()["messages"]),
        )
        self.assertGreater(len(r_upper.json()["messages"]), 0)
        self._assert_no_checkout_db_opened()

    def test_search_query_is_trimmed(self):
        """Leading/trailing whitespace in q is stripped before search."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=%20%20muscle%20%20",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["messages"]), 2)
        self._assert_no_checkout_db_opened()

    # ------------------------------------------------------------------
    # Existing route compatibility tests
    # ------------------------------------------------------------------
    def test_existing_get_history_still_works(self):
        """GET /api/v2/agent/history still works after adding search."""
        r = self._client.get(
            "/api/v2/agent/history?limit=10",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["messages"], list)
        self.assertGreater(len(data["messages"]), 0)
        self._assert_no_checkout_db_opened()

    def test_existing_delete_history_still_works(self):
        """DELETE /api/v2/agent/history still works after adding search.

        This test operates on the shared temp DB (which is fine — deletion
        is isolated to user 99, a user no other test uses), and verifies
        that the original delete route and search route both continue to
        function correctly alongside each other.
        """
        from fitness_agent.history import (
            add_agent_chat_message,
        )

        test_uid = 99
        add_agent_chat_message(
            self._conn, test_uid, "user", "to be deleted", created_at=9999,
        )
        self._conn.commit()

        token = self._auth.generate_token(test_uid, "delete_test_user")

        # Verify message exists via search.
        r = self._client.get(
            "/api/v2/agent/history/search?q=deleted",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["messages"]), 1)

        # Delete history.
        r_del = self._client.delete(
            "/api/v2/agent/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r_del.status_code, 200)
        self.assertTrue(r_del.json()["ok"])

        # Verify search now returns empty.
        r_after = self._client.get(
            "/api/v2/agent/history/search?q=deleted",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r_after.status_code, 200)
        self.assertEqual(len(r_after.json()["messages"]), 0)
        self._assert_no_checkout_db_opened()

    # ------------------------------------------------------------------
    # Response shape tests
    # ------------------------------------------------------------------
    def test_success_response_shape(self):
        """Success response has exactly the expected shape."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=muscle",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        data = r.json()
        # Exactly two top-level keys: ok and messages
        self.assertEqual(set(data.keys()), {"ok", "messages"})
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["messages"], list)
        if data["messages"]:
            msg = data["messages"][0]
            expected_keys = {"id", "role", "content", "mode", "domains", "created_at"}
            self.assertTrue(expected_keys.issubset(set(msg.keys())))
        self._assert_no_checkout_db_opened()

    def test_422_response_is_not_500(self):
        """Invalid input returns 422, not 500."""
        r = self._client.get(
            "/api/v2/agent/history/search?q=",
            headers={"Authorization": f"Bearer {self.user1_token}"},
        )
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 422)
        self._assert_no_checkout_db_opened()


if __name__ == "__main__":
    unittest.main()
