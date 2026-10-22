"""Bootstrap tests for fitness_agent.toolkit package.

Validates that the toolkit ``__init__.py`` correctly imports all category
modules, registers every tool handler, and exposes the public API that
``fitness_agent.tools`` re-exports.

Uses stdlib unittest so it can be discovered from the repository root
without pytest.  The test module inserts the backend path itself.
"""
import json
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
    fd, path = tempfile.mkstemp(suffix=".db", prefix="toolkit_boot_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Minimal schema so tool handlers that query tables do not crash.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username) VALUES (1, 'test_user')"
    )
    conn.commit()
    return conn, path


class ToolkitImportTests(unittest.TestCase):
    """Verify the toolkit package imports cleanly and exposes its API."""

    def test_import_toolkit_package(self):
        """fitness_agent.toolkit is importable as a package."""
        import fitness_agent.toolkit as tk
        self.assertTrue(hasattr(tk, "TOOL_SPECS"))
        self.assertTrue(hasattr(tk, "execute_tool"))
        self.assertTrue(hasattr(tk, "tool_specs_for_prompt"))

    def test_tools_shim_re_exports(self):
        """fitness_agent.tools re-exports the same API from toolkit."""
        from fitness_agent import tools
        from fitness_agent.toolkit import TOOL_SPECS, execute_tool, tool_specs_for_prompt
        # Same objects (not just equal) so there is no double-import surprise.
        self.assertIs(tools.TOOL_SPECS, TOOL_SPECS)
        self.assertIs(tools.execute_tool, execute_tool)
        self.assertIs(tools.tool_specs_for_prompt, tool_specs_for_prompt)

    def test_tool_specs_is_non_empty_list(self):
        """TOOL_SPECS is a list containing at least the core tool categories."""
        from fitness_agent.toolkit import TOOL_SPECS
        self.assertIsInstance(TOOL_SPECS, list)
        self.assertGreater(len(TOOL_SPECS), 0, "No tools were registered")
        # Every entry is a dict with the required keys.
        for spec in TOOL_SPECS:
            self.assertIsInstance(spec, dict)
            self.assertIn("name", spec)
            self.assertIn("description", spec)
            self.assertIn("args", spec)
            self.assertIn("read_only", spec)

    def test_tool_specs_for_prompt_returns_valid_json(self):
        """tool_specs_for_prompt() returns a JSON string matching TOOL_SPECS."""
        from fitness_agent.toolkit import TOOL_SPECS, tool_specs_for_prompt
        raw = tool_specs_for_prompt()
        self.assertIsInstance(raw, str)
        parsed = json.loads(raw)
        self.assertEqual(len(parsed), len(TOOL_SPECS))
        # Name order matches (both come from the same registry dict).
        self.assertEqual([t["name"] for t in parsed], [t["name"] for t in TOOL_SPECS])


class ToolExecutionTests(unittest.TestCase):
    """Verify execute_tool behavior with a real SQLite connection."""

    def setUp(self):
        self.conn, self.db_path = _make_temp_db()

    def tearDown(self):
        # Explicitly close before removal — Windows requires it.
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_unknown_tool_returns_error(self):
        """Calling an unregistered tool returns a structured error, not an exception."""
        from fitness_agent.toolkit import execute_tool
        result = execute_tool(self.conn, 1, "nonexistent_tool_xyz", {})
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("ok", True))
        self.assertEqual(result.get("error_type"), "unknown_tool")
        self.assertIn("tool not allowed", result.get("error", ""))
        # Available tools list is present for debugging.
        self.assertIn("available_tools", result)
        self.assertIsInstance(result["available_tools"], list)
        self.assertGreater(len(result["available_tools"]), 0)

    def test_known_tool_names_in_specs_and_available(self):
        """Every tool in TOOL_SPECS appears in the unknown-tool available_tools list."""
        from fitness_agent.toolkit import TOOL_SPECS, execute_tool
        result = execute_tool(self.conn, 1, "definitely_not_a_tool_12345", {})
        available = set(result["available_tools"])
        spec_names = {t["name"] for t in TOOL_SPECS}
        self.assertTrue(spec_names.issubset(available),
                        f"Missing from available_tools: {spec_names - available}")

    def test_memory_tool_runs_against_real_db(self):
        """A read-only tool executes cleanly against a real (minimal) SQLite connection."""
        from fitness_agent.toolkit import execute_tool
        result = execute_tool(self.conn, 1, "get_coach_memory", {"limit": 5})
        self.assertIsInstance(result, dict)
        # The tool should return ok=True or a structured error — never raise.
        self.assertIn("ok", result)


class CategoryCoverageTests(unittest.TestCase):
    """Sanity-check that every expected tool category registered at least one tool."""

    EXPECTED_CATEGORY_HINTS = [
        # (substring in tool name or description, label for error message)
        ("memory", "memory"),
        ("workout", "workouts"),
        ("rep", "reps"),
        ("todo", "todos"),
        ("kb", "knowledge"),
        ("web", "web"),
        ("plan", "plans"),
        ("context", "context"),
        ("vision", "vision"),
    ]

    def test_all_categories_registered(self):
        """At least one tool from each major category is present in TOOL_SPECS."""
        from fitness_agent.toolkit import TOOL_SPECS
        haystack = " ".join(
            f"{t['name']} {t['description']}" for t in TOOL_SPECS
        ).lower()
        missing = []
        for hint, label in self.EXPECTED_CATEGORY_HINTS:
            if hint not in haystack:
                missing.append(label)
        self.assertEqual(
            missing, [],
            f"Tool categories missing from registry: {missing}"
        )


if __name__ == "__main__":
    unittest.main()
