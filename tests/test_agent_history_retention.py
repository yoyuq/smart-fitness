"""Retention behavior against isolated, in-memory SQLite databases."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest


spec = importlib.util.spec_from_file_location(
    "history_retention_under_test",
    Path(__file__).resolve().parents[1] / "backend" / "fitness_agent" / "history.py",
)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        history.ensure_agent_chat_schema(self.conn)
        # Interleaved users, gaps, and reversed timestamps expose wrong ordering.
        for row_id, user_id in [(2, 1), (4, 2), (7, 1), (9, 1), (12, 2), (15, 1)]:
            self.conn.execute(
                "INSERT INTO agent_chat_messages "
                "(id, user_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
                (row_id, user_id, f"message {row_id}", 100 - row_id),
            )
        self.conn.commit()

    def rows(self, user_id=1):
        return [tuple(row) for row in self.conn.execute(
            "SELECT * FROM agent_chat_messages WHERE user_id=? ORDER BY id", (user_id,),
        )]

    def ids(self, user_id=1):
        return [row[0] for row in self.rows(user_id)]

    def test_keeps_newest_ids_and_preserves_other_users_and_rows(self):
        expected = self.rows()[-2:]
        other_user = self.rows(2)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 2), 2)
        self.assertEqual(self.rows(), expected)
        self.assertEqual(self.rows(2), other_user)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 2), 0)

    def test_zero_deletes_all_only_for_requested_user(self):
        other_user = self.rows(2)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 0), 4)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows(2), other_user)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 0), 0)

    def test_default_keeps_exactly_200(self):
        self.conn.executemany(
            "INSERT INTO agent_chat_messages (user_id, role, content, created_at) "
            "VALUES (1, 'user', 'bulk', 1)", [()] * 201,
        )
        self.conn.commit()
        expected = self.rows()[-200:]
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1), 5)
        self.assertEqual(self.rows(), expected)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1), 0)

    def test_one_and_upper_bound_are_accepted(self):
        original = self.rows()
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 10000), 0)
        self.assertEqual(self.rows(), original)
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 1), 3)
        self.assertEqual(self.ids(), [15])

    def test_missing_user_and_fresh_database_return_zero(self):
        original = self.rows()
        self.assertEqual(history.prune_agent_chat_history(self.conn, 99, 0), 0)
        self.assertEqual(self.rows(), original)
        with sqlite3.connect(":memory:") as fresh:
            self.assertEqual(history.prune_agent_chat_history(fresh, 1), 0)

    def test_invalid_values_do_not_execute_sql_or_change_rows(self):
        original = self.rows()
        other_user = self.rows(2)
        for invalid in [True, False, -1, 10001, 2**100, 2.0, "2", None, [], {}, float("nan")]:
            with self.subTest(keep_last=invalid):
                statements = []
                self.conn.set_trace_callback(statements.append)
                try:
                    with self.assertRaises(ValueError):
                        history.prune_agent_chat_history(self.conn, 1, invalid)
                finally:
                    self.conn.set_trace_callback(None)
                self.assertEqual(statements, [])
                self.assertEqual(self.rows(), original)
                self.assertEqual(self.rows(2), other_user)

    def test_invalid_input_does_not_commit_pending_changes(self):
        original = self.rows()
        self.conn.execute("UPDATE agent_chat_messages SET content='pending' WHERE id=2")
        with self.assertRaises(ValueError):
            history.prune_agent_chat_history(self.conn, 1, False)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.rows(), original)

    def test_pruning_commits_deletion(self):
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 2), 2)
        self.assertFalse(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.ids(), [9, 15])

    def test_before_id_pagination_after_pruning(self):
        self.assertEqual(history.prune_agent_chat_history(self.conn, 1, 3), 1)
        newest = history.get_agent_chat_history(self.conn, 1, 2)
        older = history.get_agent_chat_history(self.conn, 1, 2, newest[0]["id"])
        self.assertEqual([row["id"] for row in older + newest], [7, 9, 15])
        self.assertEqual(history.get_agent_chat_history(self.conn, 1, before_id=7), [])
        self.assertEqual(
            [row["id"] for row in history.get_agent_chat_history(self.conn, 2, before_id=15)],
            [4, 12],
        )


if __name__ == "__main__":
    unittest.main()
