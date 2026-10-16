# QUOTA_RECOVERY_DRAFT_PRESERVED
"""Isolated regression checks for the original storage.database contract."""

import pytest

from storage.database import bind_qmarks, connect, initialize


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_ENGINE", raising=False)
    path = tmp_path / "quota_contract.db"
    monkeypatch.setenv("DB_PATH", str(path))
    initialize()
    return path


def test_qmarks_inside_quotes_and_comments_are_not_bound(database_path):
    sql = "SELECT '?' AS single_quote, \"?\" AS double_quote, ? AS first /* ? */ -- ?\n, ? AS second"

    bound, parameters = bind_qmarks(sql, ("synthetic", 7))

    assert bound == "SELECT '?' AS single_quote, \"?\" AS double_quote, :p0 AS first /* ? */ -- ?\n, :p1 AS second"
    assert parameters == {"p0": "synthetic", "p1": 7}


def test_qmark_parameter_count_must_match(database_path):
    with pytest.raises(ValueError, match="SQL parameter count mismatch"):
        bind_qmarks("SELECT ? AS first, ? AS second", (1,))
    with pytest.raises(ValueError, match="SQL parameter count mismatch"):
        bind_qmarks("SELECT '?' AS literal, ? AS first", (1, 2))


def test_commit_is_visible_to_a_fresh_connection(database_path):
    writer = connect()
    try:
        writer.execute(
            "INSERT INTO devices(device_id, name) VALUES (?, ?)",
            ("synthetic-device", "committed"),
        )
        writer.commit()
    finally:
        writer.close()

    reader = connect()
    try:
        row = reader.execute(
            "SELECT name FROM devices WHERE device_id=?", ("synthetic-device",)
        ).fetchone()
        assert row is not None
        assert row[0] == "committed"
    finally:
        reader.close()


def test_rollback_discards_changes_for_a_fresh_connection(database_path):
    writer = connect()
    try:
        writer.execute(
            "INSERT INTO devices(device_id, name) VALUES (?, ?)",
            ("synthetic-device", "original"),
        )
        writer.commit()
        writer.execute(
            "UPDATE devices SET name=? WHERE device_id=?",
            ("rolled-back", "synthetic-device"),
        )
        writer.execute(
            "INSERT INTO devices(device_id, name) VALUES (?, ?)",
            ("temporary-device", "temporary"),
        )
        writer.rollback()
    finally:
        writer.close()

    reader = connect()
    try:
        rows = reader.execute(
            "SELECT device_id, name FROM devices ORDER BY device_id"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("synthetic-device", "original")
        ]
    finally:
        reader.close()
