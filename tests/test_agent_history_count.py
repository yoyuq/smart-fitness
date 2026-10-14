"""Per-user counts use the same role semantics without a retrieval cap."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest


spec = importlib.util.spec_from_file_location(
    "agent_history_count", Path(__file__).resolve().parents[1] / "backend/fitness_agent/history.py"
)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryCountTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        history.ensure_agent_chat_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO agent_chat_messages (user_id, role, content, created_at) VALUES (?, ?, 'message', 1)",
            [(1, "user")] * 205 + [(1, "assistant")] * 3 + [(2, "assistant")] * 7,
        )
        self.conn.commit()

    def test_count_is_int_unlimited_and_user_isolated(self):
        count = history.count_agent_chat_history(self.conn, 1)
        self.assertIs(type(count), int)
        self.assertEqual(count, 208)
        self.assertEqual(history.count_agent_chat_history(self.conn, 2), 7)
        self.assertEqual(history.count_agent_chat_history(self.conn, 99), 0)
        self.assertEqual(history.count_agent_chat_history(self.conn, 1, None), count)
        self.assertEqual(history.count_agent_chat_history(self.conn, 2, ["user"]), 0)
        self.assertEqual(history.count_agent_chat_history(self.conn, 2, ["assistant"]), 7)

    def test_filters_empty_and_duplicates(self):
        for roles, expected in ((["user"], 205), (("assistant",), 3),
                                (["user", "user"], 205),
                                (("assistant", "user", "assistant"), 208),
                                ([], 0), ((), 0)):
            with self.subTest(roles=roles):
                count = history.count_agent_chat_history(self.conn, 1, roles)
                self.assertIs(type(count), int)
                self.assertEqual(count, expected)

    def test_invalid_roles_raise_value_error(self):
        for roles in ("user", "", 1, False, {"user"}, {"role": "user"},
                      iter(["user"]), ["system"], ["User"], [None], [[]],
                      [1], ["user", {}], ("assistant", ""), ["user' OR 1=1 --"]):
            with self.subTest(roles=repr(roles)), self.assertRaises(ValueError):
                history.count_agent_chat_history(self.conn, 1, roles)

    def test_row_factory_and_existing_delete_api(self):
        self.conn.row_factory = sqlite3.Row
        self.assertEqual(history.count_agent_chat_history(self.conn, 1, ["assistant"]), 3)
        self.assertEqual(history.delete_agent_chat_history(self.conn, 1), 208)
        self.assertEqual(history.count_agent_chat_history(self.conn, 1), 0)
        self.assertEqual(history.count_agent_chat_history(self.conn, 2), 7)

    def test_fresh_database_initializes_schema(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.assertEqual(history.count_agent_chat_history(conn, 1), 0)
        self.assertEqual(history.count_agent_chat_history(conn, 1, []), 0)


if __name__ == "__main__":
    unittest.main()
