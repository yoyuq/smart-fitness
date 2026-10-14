"""Exercise role filtering against isolated SQLite history without app imports."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest


spec = importlib.util.spec_from_file_location(
    "agent_history_roles", Path(__file__).resolve().parents[1] / "backend/fitness_agent/history.py"
)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryRolesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        history.ensure_agent_chat_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO agent_chat_messages "
            "(user_id, role, content, mode, domains_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "user", "OpenAI user text", "auto", '["strength"]', 300),
                (2, "assistant", "private", "auto", "[]", 200),
                (1, "assistant", " GPT-4 OpenAI ", None, "bad json", 100),
                (1, "user", "recent user", "auto", "{}", 50),
                (1, "assistant", "recent assistant", "auto", "[]", 1),
            ],
        )
        self.conn.commit()

    def ids(self, **kwargs):
        return [row["id"] for row in history.get_agent_chat_history(self.conn, 1, **kwargs)]

    def test_default_behavior_and_user_isolation(self):
        self.assertEqual(self.ids(), [1, 3, 4, 5])
        self.assertEqual(history.get_agent_chat_history(self.conn, 1),
                         history.get_agent_chat_history(self.conn, 1, roles=None))
        self.assertEqual(history.get_agent_chat_history(self.conn, 99, roles=["assistant"]), [])
        self.assertEqual([r["id"] for r in history.get_agent_chat_history(
            self.conn, 2, roles=["assistant"])], [2])

    def test_filter_before_limit_and_original_insertion_order(self):
        self.assertEqual(self.ids(limit=2, roles=["user"]), [1, 4])
        self.assertEqual(self.ids(roles=("assistant",)), [3, 5])
        self.assertEqual([r["id"] for r in history.get_agent_chat_history(
            self.conn, 1, 1, ("assistant",))], [5])

    def test_empty_and_repeated_roles(self):
        for roles in ([], ()):
            with self.subTest(roles=roles):
                self.assertEqual(self.ids(roles=roles), [])
        self.assertEqual(self.ids(roles=["user", "user"]), [1, 4])
        self.assertEqual(self.ids(roles=("assistant", "user", "assistant")), self.ids())

    def test_invalid_containers_and_members(self):
        for roles in ("user", "", 1, False, {"user"}, {"user": 1},
                      iter(["user"]), ["system"], ["User"], [None], [[]],
                      [1], ["user", {}], ("assistant", ""), ["user' OR 1=1 --"]):
            with self.subTest(roles=repr(roles)), self.assertRaises(ValueError):
                history.get_agent_chat_history(self.conn, 1, roles=roles)

    def test_filtered_content_and_metadata(self):
        row = history.get_agent_chat_history(self.conn, 1, roles=["assistant"])[0]
        self.assertEqual(row, {
            "id": 3, "role": "assistant",
            "content": "后端配置的 LLM 调用链 后端配置的模型服务",
            "mode": "auto", "domains": [], "created_at": 100,
        })
        users = history.get_agent_chat_history(self.conn, 1, roles=["user"])
        self.assertEqual(users[0]["content"], "OpenAI user text")
        self.assertEqual(users[0]["domains"], ["strength"])
        self.assertEqual(users[1]["domains"], [])

    def test_original_limit_defaults_and_bounds(self):
        self.conn.executemany(
            "INSERT INTO agent_chat_messages (user_id, role, content, created_at) "
            "VALUES (1, 'user', 'message', 1)", [()] * 205,
        )
        for roles in (None, ["user"]):
            for limit, expected in ((None, 50), (0, 50), (-2, 1), (999, 200), ("2", 2)):
                with self.subTest(roles=roles, limit=limit):
                    self.assertEqual(len(self.ids(limit=limit, roles=roles)), expected)


if __name__ == "__main__":
    unittest.main()
