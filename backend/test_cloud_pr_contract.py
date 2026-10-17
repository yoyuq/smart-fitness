"""Cloud PR contract tests for storage.database.

Covers qmark binding behavior, parameter count validation, committed row
visibility across fresh connections, and rollback semantics verified from
a fresh connection.  Uses only tmp_path / monkeypatch and synthetic data.
"""
import os
import pytest
from pathlib import Path

from storage.database import connect, bind_qmarks


BACKEND_DIR = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _isolate_db_env(monkeypatch, tmp_path):
    """Clear production-style env vars and point DB_PATH at a scratch file."""
    for key in ("DATABASE_URL", "DB_ENGINE", "POSTGRES_USER",
                "POSTGRES_PASSWORD", "POSTGRES_DB", "FITNESS_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "contract.db"))


# ---------------------------------------------------------------------------
# 1. qmark binding: placeholders inside quoted SQL and comments are skipped
# ---------------------------------------------------------------------------
def test_qmark_binding_skips_quoted_sql_and_comments():
    sql_in = (
        "SELECT 'literal?' AS a, \"col?\" AS b, ? AS c "
        "/* inline ? comment */ -- line ? end\n"
        "WHERE d = ?"
    )
    sql_out, args = bind_qmarks(sql_in, (100, 200))
    # Only the two real ? outside literals/comments should be replaced.
    assert sql_out == (
        "SELECT 'literal?' AS a, \"col?\" AS b, :p0 AS c "
        "/* inline ? comment */ -- line ? end\n"
        "WHERE d = :p1"
    )
    assert args == {"p0": 100, "p1": 200}


# ---------------------------------------------------------------------------
# 2. mismatched parameter count raises ValueError
# ---------------------------------------------------------------------------
def test_mismatched_parameter_count_raises():
    # Too few parameters.
    with pytest.raises(ValueError, match="parameter count mismatch"):
        bind_qmarks("SELECT ?, ?, ?", (1, 2))
    # Too many parameters.
    with pytest.raises(ValueError, match="parameter count mismatch"):
        bind_qmarks("SELECT ?", (1, 2, 3))
    # Zero placeholders but parameters supplied.
    with pytest.raises(ValueError, match="parameter count mismatch"):
        bind_qmarks("SELECT 1", (1,))
    # Zero placeholders and zero parameters is fine.
    sql_out, args = bind_qmarks("SELECT 1", ())
    assert sql_out == "SELECT 1"
    assert args == {}


# ---------------------------------------------------------------------------
# 3. committed rows visible through a fresh connection
# ---------------------------------------------------------------------------
def test_committed_rows_visible_through_fresh_connection(tmp_path):
    db_path = str(tmp_path / "visible.db")

    # Connection A: create table, insert a row, commit.
    conn_a = connect(db_path)
    conn_a.execute(
        "CREATE TABLE contract_items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    conn_a.execute(
        "INSERT INTO contract_items (id, name) VALUES (?, ?)",
        (1, "synthetic-row"),
    )
    conn_a.commit()
    conn_a.close()

    # Connection B: open fresh and verify the row is there.
    conn_b = connect(db_path)
    row = conn_b.execute(
        "SELECT name FROM contract_items WHERE id = ?", (1,)
    ).fetchone()
    assert row is not None
    assert row["name"] == "synthetic-row"
    conn_b.close()


# ---------------------------------------------------------------------------
# 4. rollback leaves committed schema intact and no inserted row
# ---------------------------------------------------------------------------
def test_rollback_preserves_schema_and_discards_insert(tmp_path):
    db_path = str(tmp_path / "rollback.db")

    # Connection A: create table (committed), then insert + rollback.
    conn_a = connect(db_path)
    conn_a.execute(
        "CREATE TABLE contract_rollback (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
    )
    conn_a.commit()
    conn_a.execute(
        "INSERT INTO contract_rollback (id, label) VALUES (?, ?)",
        (42, "should-not-survive"),
    )
    conn_a.rollback()
    conn_a.close()

    # Fresh connection: schema must still exist, row must not be there.
    conn_b = connect(db_path)
    count = conn_b.execute(
        "SELECT COUNT(*) AS n FROM contract_rollback"
    ).fetchone()["n"]
    assert count == 0, "Rolled-back row must not be visible from a fresh connection"

    # Schema still exists (table query succeeds and returns columns).
    cols = conn_b.execute("PRAGMA table_info(contract_rollback)").fetchall()
    col_names = [c["name"] for c in cols]
    assert "id" in col_names
    assert "label" in col_names
    conn_b.close()
