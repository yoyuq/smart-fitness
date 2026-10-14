"""Behavior tests for history cursors using an isolated SQLite database."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest


spec = importlib.util.spec_from_file_location(
    "history_under_test",
    Path(__file__).resolve().parents[1] / "backend" / "fitness_agent" / "history.py",
)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryPaginationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        history.ensure_agent_chat_schema(self.conn)
        # Gaps and interleaved users make cursor and isolation errors observable.
        for row_id, user_id in [(2, 1), (4, 2), (7, 1), (9, 1), (12, 2), (15, 1)]:
            self.conn.execute(
                "INSERT INTO agent_chat_messages "
                "(id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (row_id, user_id, "user", f"message {row_id}", 100 - row_id),
            )
        self.conn.commit()

    def ids(self, user_id=1, **kwargs):
        return [row["id"] for row in history.get_agent_chat_history(self.conn, user_id, **kwargs)]

    def test_none_preserves_existing_signature_and_results(self):
        expected = history.get_agent_chat_history(self.conn, 1, 2)
        self.assertEqual([row["id"] for row in expected], [9, 15])
        self.assertEqual(expected, history.get_agent_chat_history(self.conn, 1, 2, None))
        self.assertEqual(self.ids(), [2, 7, 9, 15])

    def test_cursor_is_strict_and_selects_newest_matches(self):
        self.assertEqual(self.ids(limit=2, before_id=15), [7, 9])
        self.assertEqual(self.ids(limit=2, before_id=10), [7, 9])
        self.assertEqual(self.ids(limit=2, before_id=9), [2, 7])
        self.assertEqual(self.ids(before_id=2), [])
        self.assertEqual(self.ids(before_id=1), [])

    def test_multiple_pages_have_no_duplicates_or_missing_rows(self):
        newest = history.get_agent_chat_history(self.conn, 1, 2)
        older = history.get_agent_chat_history(self.conn, 1, 2, newest[0]["id"])
        self.assertEqual([row["id"] for row in older + newest], [2, 7, 9, 15])
        self.assertEqual(self.ids(before_id=older[0]["id"]), [])

    def test_user_isolation_including_other_users_cursor(self):
        self.assertEqual(self.ids(before_id=12), [2, 7, 9])
        self.assertEqual(self.ids(user_id=2, before_id=15), [4, 12])
        self.assertEqual(self.ids(user_id=99, before_id=15), [])

    def test_invalid_cursors_raise_value_error(self):
        for cursor in [True, False, 0, -1, 1.0, "9", "", [], {}, float("nan")]:
            with self.subTest(cursor=cursor):
                with self.assertRaises(ValueError):
                    history.get_agent_chat_history(self.conn, 1, before_id=cursor)

    def test_cursor_above_sqlite_integer_range(self):
        self.assertEqual(self.ids(before_id=2**100), [2, 7, 9, 15])

    def test_cursor_at_sqlite_integer_boundary_is_exclusive(self):
        max_id = 2**63 - 1
        self.conn.execute(
            "INSERT INTO agent_chat_messages "
            "(id, user_id, role, content, created_at) VALUES (?, 1, 'user', 'last', 1)",
            (max_id,),
        )
        self.assertEqual(self.ids(before_id=max_id), [2, 7, 9, 15])
        self.assertEqual(self.ids(before_id=max_id + 1), [2, 7, 9, 15, max_id])

    def test_limit_normalization_is_preserved_with_cursor(self):
        self.assertEqual(self.ids(limit=-1, before_id=15), [9])
        self.assertEqual(self.ids(limit=0, before_id=15), [2, 7, 9])
        self.assertEqual(self.ids(limit="2", before_id=15), [7, 9])
        self.conn.executemany(
            "INSERT INTO agent_chat_messages (user_id, role, content, created_at) "
            "VALUES (1, 'user', 'bulk', 1)",
            [()] * 205,
        )
        rows = self.ids(limit=999, before_id=220)
        self.assertEqual(rows, list(range(20, 220)))

    def test_paginated_rows_preserve_sanitization_and_shape(self):
        self.conn.execute(
            "UPDATE agent_chat_messages SET role='assistant', content=?, "
            "mode='', domains_json=? WHERE id=9",
            (" GPT-4 OpenAI ", "invalid json"),
        )
        self.conn.execute(
            "UPDATE agent_chat_messages SET content='GPT-4', domains_json=? WHERE id=7",
            ('["fitness"]',),
        )
        rows = history.get_agent_chat_history(self.conn, 1, 2, 15)
        self.assertEqual(rows[0]["content"], "GPT-4")
        self.assertEqual(rows[0]["domains"], ["fitness"])
        self.assertEqual(rows[1], {
            "id": 9, "role": "assistant",
            "content": "后端配置的 LLM 调用链 后端配置的模型服务",
            "mode": "auto", "domains": [], "created_at": 91,
        })
        self.conn.execute("UPDATE agent_chat_messages SET domains_json='{}' WHERE id=9")
        self.assertEqual(history.get_agent_chat_history(self.conn, 1, 1, 15)[0]["domains"], [])


if __name__ == "__main__":
    unittest.main()
