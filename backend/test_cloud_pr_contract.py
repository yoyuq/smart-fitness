"""Cloud PR contract tests for storage.database.

Four targeted checks covering qmark binding, parameter count mismatch,
committed visibility across fresh connections, and rollback behavior.
Uses only synthetic data and tmp_path; never touches production stores.
"""
import os

import pytest

from storage.database import bind_qmarks, connect


def test_qmark_binding_outside_quoted_sql_and_comments():
    """Question marks inside string literals, -- comments and /* */ comments
    must be left alone; only bare ? tokens become :pN parameters."""
    sql_in = (
        "SELECT '?' AS literal, \"?\" AS quoted, ? AS first "
        "/* this ? stays */ -- that ? stays\n"
        ", ? AS second"
    )
    sql_out, params = bind_qmarks(sql_in, (101, 202))
    # Literal and quoted question marks are preserved verbatim.
    assert "'?'" in sql_out
    assert '"?"' in sql_out
    assert "/* this ? stays */" in sql_out
    assert "-- that ? stays" in sql_out
    # Two real placeholders were bound in order.
    assert params == {"p0": 101, "p1": 202}
    assert ":p0" in sql_out
    assert ":p1" in sql_out


def test_mismatched_parameter_count_raises():
    """bind_qmarks must reject when parameter count disagrees with
    the number of real (non-literal, non-comment) ? placeholders."""
    with pytest.raises(ValueError):
        bind_qmarks("SELECT ?, ?, ?", (1, 2))
    with pytest.raises(ValueError):
        bind_qmarks("SELECT ?", (1, 2))
    # And a matching count still works.
    sql_out, params = bind_qmarks("SELECT ?, ?", (7, 8))
    assert params == {"p0": 7, "p1": 8}
    assert sql_out.count(":p") == 2


def test_committed_rows_visible_through_fresh_connection(tmp_path, monkeypatch):
    """After commit on one connection, a brand-new connection opened
    against the same file must see the inserted row."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FITNESS_DB_PATH", raising=False)

    db_file = tmp_path / "contract.db"
    conn_a = connect(db_file)
    conn_a.execute("CREATE TABLE fruits (name TEXT, qty INTEGER)")
    conn_a.commit()
    conn_a.execute("INSERT INTO fruits VALUES (?, ?)", ("apple", 5))
    conn_a.execute("INSERT INTO fruits VALUES (?, ?)", ("banana", 3))
    conn_a.commit()
    conn_a.close()

    conn_b = connect(db_file)
    rows = conn_b.execute("SELECT name, qty FROM fruits ORDER BY name").fetchall()
    conn_b.close()

    assert [(r[0], r[1]) for r in rows] == [("apple", 5), ("banana", 3)]


def test_rollback_leaves_committed_schema_and_no_inserted_row(tmp_path, monkeypatch):
    """A rollback after an INSERT must discard that row, while schema
    changes committed before the write remain visible from a fresh
    connection."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FITNESS_DB_PATH", raising=False)

    db_file = tmp_path / "rollback.db"
    conn = connect(db_file)
    # Schema is committed for good.
    conn.execute("CREATE TABLE widgets (label TEXT PRIMARY KEY, value INTEGER)")
    conn.execute("INSERT INTO widgets VALUES (?, ?)", ("keep", 1))
    conn.commit()
    # Second write is intentionally rolled back.
    conn.execute("INSERT INTO widgets VALUES (?, ?)", ("drop-me", 2))
    conn.rollback()
    conn.close()

    fresh = connect(db_file)
    rows = fresh.execute("SELECT label FROM widgets ORDER BY label").fetchall()
    # Schema survives: the table still exists and the committed row is present.
    assert [r[0] for r in rows] == ["keep"]
    # The rolled-back row is gone.
    assert fresh.execute(
        "SELECT COUNT(*) AS n FROM widgets WHERE label = ?", ("drop-me",)
    ).fetchone()[0] == 0
    fresh.close()
