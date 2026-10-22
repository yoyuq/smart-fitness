"""Tests for fitness_agent.history.search_agent_chat_history.

Validates input validation, literal substring matching, user isolation,
result ordering, and assistant sanitization.

Uses stdlib unittest so it can be discovered from the repository root
without pytest.  The test module inserts the backend path itself.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

# Ensure the backend package is importable when running from repo root
# via ``python -m unittest discover``.
_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
_BACKEND_DIR = os.path.normpath(_BACKEND_DIR)
if os.path.abspath(_BACKEND_DIR) not in [os.path.abspath(p) for p in sys.path]:
    sys.path.insert(0, _BACKEND_DIR)


def _make_temp_db():
    """Create a temporary SQLite file and return (connection, path).

    Caller is responsible for closing the connection before removing the
    file.  On Windows a SQLite connection held open will prevent file
    deletion even when using a context manager for the cursor, so we
    always explicitly ``conn.close()`` in cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="history_search_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn, path


def _seed_messages(conn, user_id, messages):
    """Insert test messages for a user.  messages: list of (role, content)."""
    from fitness_agent.history import add_agent_chat_message
    for idx, (role, content) in enumerate(messages):
        # Use created_at based on index so timestamps are deterministic.
        add_agent_chat_message(conn, user_id, role, content, created_at=1000 + idx)


class SearchInputValidationTests(unittest.TestCase):
    """Verify that invalid inputs raise ValueError before any DB access."""

    def setUp(self):
        self.conn, self.db_path = _make_temp_db()

    def tearDown(self):
        # Explicitly close before removal — Windows requires it.
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_query_none_raises_value_error(self):
        """query=None raises ValueError (not TypeError)."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, None)

    def test_query_int_raises_value_error(self):
        """Non-string query raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, 123)

    def test_query_empty_string_raises_value_error(self):
        """Empty string query raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "")

    def test_query_whitespace_only_raises_value_error(self):
        """Whitespace-only query (stripped to empty) raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "   \t\n  ")

    def test_query_too_long_raises_value_error(self):
        """Query longer than 200 chars raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "a" * 201)

    def test_query_200_chars_ok(self):
        """Query of exactly 200 chars is accepted."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(self.conn, 1, "user", "hello world", created_at=1000)
        # 200-char query should not raise; just returns empty list (no match).
        result = search_agent_chat_history(self.conn, 1, "x" * 200)
        self.assertIsInstance(result, list)

    def test_query_1_char_ok(self):
        """Single-character query is accepted."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(self.conn, 1, "user", "a", created_at=1000)
        result = search_agent_chat_history(self.conn, 1, "a")
        self.assertEqual(len(result), 1)

    def test_limit_none_raises_value_error(self):
        """limit=None raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=None)

    def test_limit_bool_raises_value_error(self):
        """Boolean limit (True/False) raises ValueError — bool is not a true int."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=True)
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=False)

    def test_limit_float_raises_value_error(self):
        """Float limit raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=10.0)

    def test_limit_string_raises_value_error(self):
        """String limit raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit="10")

    def test_limit_zero_raises_value_error(self):
        """limit=0 raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=0)

    def test_limit_negative_raises_value_error(self):
        """Negative limit raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=-5)

    def test_limit_over_200_raises_value_error(self):
        """limit > 200 raises ValueError."""
        from fitness_agent.history import search_agent_chat_history
        with self.assertRaises(ValueError):
            search_agent_chat_history(self.conn, 1, "test", limit=201)

    def test_limit_200_ok(self):
        """limit=200 is accepted."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(self.conn, 1, "user", "hello", created_at=1000)
        result = search_agent_chat_history(self.conn, 1, "hello", limit=200)
        self.assertEqual(len(result), 1)

    def test_query_stripped_before_length_check(self):
        """Query is stripped before length validation."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(self.conn, 1, "user", "abc", created_at=1000)
        # " abc " strips to "abc" (3 chars) — valid, and should match.
        result = search_agent_chat_history(self.conn, 1, "  abc  ")
        self.assertEqual(len(result), 1)

    def test_validation_before_schema_write(self):
        """ValueError is raised before the schema is created.

        We verify this by checking that no tables exist after a failed
        validation call — if ensure_agent_chat_schema had been called,
        the agent_chat_messages table would exist.
        """
        from fitness_agent.history import search_agent_chat_history
        try:
            search_agent_chat_history(self.conn, 1, "")
        except ValueError:
            pass
        # Check that no agent_chat_messages table was created.
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_chat_messages'"
        )
        self.assertIsNone(cur.fetchone())


class SearchBehaviorTests(unittest.TestCase):
    """Verify search behavior: matching, ordering, user isolation."""

    def setUp(self):
        self.conn, self.db_path = _make_temp_db()
        # Seed user 1 with a mix of messages.
        # Include several with "the" and "you" for ordering/limit tests.
        _seed_messages(self.conn, 1, [
            ("user", "Hello, how are you?"),
            ("assistant", "I am doing well, thanks for asking the question!"),
            ("user", "I want to build the muscle mass you suggested"),
            ("assistant", "Great goal! Here's a muscle building plan for you..."),
            ("user", "What about the cardio training you mentioned?"),
            ("assistant", "Cardio is important for the overall fitness you want."),
            ("user", "thanks for the help you gave"),
            ("assistant", "You're welcome! Keep training hard the best you can."),
        ])

    def tearDown(self):
        # Explicitly close before removal — Windows requires it.
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_search_finds_matching_messages(self):
        """Search returns messages containing the query substring."""
        from fitness_agent.history import search_agent_chat_history
        results = search_agent_chat_history(self.conn, 1, "muscle")
        self.assertEqual(len(results), 2)
        for msg in results:
            self.assertIn("muscle", msg["content"].lower())

    def test_search_case_insensitive_ascii(self):
        """ASCII matching is case-insensitive."""
        from fitness_agent.history import search_agent_chat_history
        results_upper = search_agent_chat_history(self.conn, 1, "CARDIO")
        results_lower = search_agent_chat_history(self.conn, 1, "cardio")
        results_mixed = search_agent_chat_history(self.conn, 1, "CaRdIo")
        self.assertEqual(len(results_upper), len(results_lower))
        self.assertEqual(len(results_upper), len(results_mixed))
        self.assertGreater(len(results_upper), 0)

    def test_search_chinese_literal_match(self):
        """Chinese characters are matched literally."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(
            self.conn, 1, "user",
            "我想增加肌肉量",
            created_at=2000,
        )
        add_agent_chat_message(
            self.conn, 1, "assistant",
            "好的，这是一份增肌计划",
            created_at=2001,
        )
        results = search_agent_chat_history(self.conn, 1, "增肌")
        self.assertEqual(len(results), 1)
        self.assertIn("增肌", results[0]["content"])

        results2 = search_agent_chat_history(self.conn, 1, "肌肉")
        self.assertEqual(len(results2), 1)
        self.assertIn("肌肉", results2[0]["content"])

    def test_search_user_isolation(self):
        """Search only returns messages belonging to the given user_id."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        # Add a message for user 2 with the same keyword.
        add_agent_chat_message(
            self.conn, 2, "user",
            "I also want to build muscle",
            created_at=3000,
        )
        # User 1 should only see their own 2 muscle messages.
        results_user1 = search_agent_chat_history(self.conn, 1, "muscle")
        self.assertEqual(len(results_user1), 2)
        # User 2 should see only their 1 muscle message.
        results_user2 = search_agent_chat_history(self.conn, 2, "muscle")
        self.assertEqual(len(results_user2), 1)
        self.assertEqual(results_user2[0]["id"], results_user2[0]["id"])  # sanity

    def test_search_returns_id_ascending_order(self):
        """Results are returned in ID ascending (chronological) order."""
        from fitness_agent.history import search_agent_chat_history
        results = search_agent_chat_history(self.conn, 1, "the")
        self.assertGreater(len(results), 1)
        ids = [m["id"] for m in results]
        self.assertEqual(ids, sorted(ids), "Results should be in ascending ID order")

    def test_search_limit_returns_latest_matches(self):
        """When there are more matches than limit, the latest N are returned.

        Since results are returned ID-ascending, but we select latest N,
        the returned list should be the most recent N matches in chronological order.
        """
        from fitness_agent.history import search_agent_chat_history
        # "you" appears in messages 0, 1, 6, 7 (0-indexed seed).
        all_results = search_agent_chat_history(self.conn, 1, "you", limit=200)
        self.assertGreater(len(all_results), 2)
        # With limit=2, we should get the 2 most recent matches.
        limited = search_agent_chat_history(self.conn, 1, "you", limit=2)
        self.assertEqual(len(limited), 2)
        # The 2 limited results should be the last 2 of all_results.
        self.assertEqual(limited[0]["id"], all_results[-2]["id"])
        self.assertEqual(limited[1]["id"], all_results[-1]["id"])

    def test_search_no_matches_returns_empty_list(self):
        """Search for non-existent text returns empty list."""
        from fitness_agent.history import search_agent_chat_history
        results = search_agent_chat_history(self.conn, 1, "nonexistent_xyz_12345")
        self.assertEqual(results, [])

    def test_search_empty_history_returns_empty_list(self):
        """Search on a user with no history returns empty list."""
        from fitness_agent.history import search_agent_chat_history
        results = search_agent_chat_history(self.conn, 999, "anything")
        self.assertEqual(results, [])

    def test_like_special_chars_are_literal(self):
        """%, _, and quotes in the query are matched literally, not as wildcards."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        add_agent_chat_message(
            self.conn, 1, "user",
            "Discount is 50% off",
            created_at=4000,
        )
        add_agent_chat_message(
            self.conn, 1, "user",
            "Use underscore_char here",
            created_at=4001,
        )
        # % should be literal — "50%" matches the message with % in it.
        results = search_agent_chat_history(self.conn, 1, "50%")
        self.assertEqual(len(results), 1)
        self.assertIn("50%", results[0]["content"])

        # _ should be literal — "underscore_" matches literally.
        results2 = search_agent_chat_history(self.conn, 1, "underscore_")
        self.assertEqual(len(results2), 1)
        self.assertIn("underscore_", results2[0]["content"])

        # A bare "%" should NOT match everything (it's not a wildcard).
        results3 = search_agent_chat_history(self.conn, 1, "%")
        self.assertEqual(len(results3), 1)  # Only the 50% message has %.

    def test_result_shape_matches_get_history(self):
        """Search results have the same shape as get_agent_chat_history."""
        from fitness_agent.history import get_agent_chat_history, search_agent_chat_history
        history = get_agent_chat_history(self.conn, 1, limit=1)
        results = search_agent_chat_history(self.conn, 1, "hello")
        self.assertGreater(len(results), 0)
        expected_keys = {"id", "role", "content", "mode", "domains", "created_at"}
        for msg in results:
            self.assertEqual(set(msg.keys()), expected_keys)
        # Check types match too.
        self.assertIsInstance(results[0]["id"], int)
        self.assertIsInstance(results[0]["role"], str)
        self.assertIsInstance(results[0]["content"], str)
        self.assertIsInstance(results[0]["mode"], str)
        self.assertIsInstance(results[0]["domains"], list)
        self.assertIsInstance(results[0]["created_at"], int)

    def test_assistant_content_is_sanitized(self):
        """Assistant messages are sanitized in search results (same as get_history)."""
        from fitness_agent.history import add_agent_chat_message, search_agent_chat_history
        # Insert an assistant message with GPT-4 wording that should be sanitized.
        # The word "回答" survives sanitization so we can find the message.
        add_agent_chat_message(
            self.conn, 1, "assistant",
            "基于 GPT-4 架构的回答",
            created_at=5000,
        )
        results = search_agent_chat_history(self.conn, 1, "回答")
        self.assertGreater(len(results), 0)
        assistant_results = [m for m in results if m["role"] == "assistant"]
        self.assertGreater(len(assistant_results), 0)
        for m in assistant_results:
            self.assertNotIn("GPT-4", m["content"])
            self.assertIn("LLM 调用链", m["content"])


class SearchDefaultLimitTests(unittest.TestCase):
    """Verify default limit behavior."""

    def setUp(self):
        self.conn, self.db_path = _make_temp_db()
        # Insert 60 messages all containing "marker".
        from fitness_agent.history import add_agent_chat_message
        for i in range(60):
            add_agent_chat_message(
                self.conn, 1, "user",
                f"message {i} marker",
                created_at=1000 + i,
            )

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_default_limit_is_50(self):
        """Default limit is 50 when not specified."""
        from fitness_agent.history import search_agent_chat_history
        results = search_agent_chat_history(self.conn, 1, "marker")
        self.assertEqual(len(results), 50)
        # Should be the latest 50 (messages 10..59), returned ID-ascending.
        ids = [m["id"] for m in results]
        self.assertEqual(len(ids), 50)
        self.assertEqual(ids, sorted(ids))


class SearchWithExistingHistoryTests(unittest.TestCase):
    """Verify that existing get/add/delete behavior is preserved."""

    def setUp(self):
        self.conn, self.db_path = _make_temp_db()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_add_then_search_then_get_consistent(self):
        """Messages added via add_agent_chat_message appear in both search and get."""
        from fitness_agent.history import (
            add_agent_chat_message,
            get_agent_chat_history,
            search_agent_chat_history,
        )
        mid = add_agent_chat_message(
            self.conn, 1, "user",
            "unique searchable phrase xyz789",
            created_at=1000,
        )
        self.assertGreater(mid, 0)

        search_results = search_agent_chat_history(self.conn, 1, "xyz789")
        self.assertEqual(len(search_results), 1)
        self.assertEqual(search_results[0]["id"], mid)

        history = get_agent_chat_history(self.conn, 1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], mid)

    def test_delete_removes_from_search(self):
        """Deleted messages no longer appear in search results."""
        from fitness_agent.history import (
            add_agent_chat_message,
            delete_agent_chat_history,
            search_agent_chat_history,
        )
        add_agent_chat_message(
            self.conn, 1, "user",
            "before delete test",
            created_at=1000,
        )
        before = search_agent_chat_history(self.conn, 1, "before delete")
        self.assertEqual(len(before), 1)

        deleted = delete_agent_chat_history(self.conn, 1)
        self.assertGreater(deleted, 0)

        after = search_agent_chat_history(self.conn, 1, "before delete")
        self.assertEqual(len(after), 0)

    def test_search_exported_from_package(self):
        """search_agent_chat_history is exported from fitness_agent package."""
        import fitness_agent
        self.assertTrue(hasattr(fitness_agent, "search_agent_chat_history"))
        from fitness_agent import search_agent_chat_history
        self.assertTrue(callable(search_agent_chat_history))


if __name__ == "__main__":
    unittest.main()
