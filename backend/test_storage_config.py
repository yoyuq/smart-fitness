import sqlite3
from storage.database import connect, default_sqlite_path


def test_db_path_and_url_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    target = tmp_path / 'legacy.db'
    monkeypatch.setenv('DB_PATH', str(target))
    assert default_sqlite_path() == str(target)
    conn = connect()
    conn.execute('CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT)')
    conn.execute('INSERT INTO example VALUES (?,?)', (1, 'hello'))
    conn.commit()
    conn.close()
    url_target = tmp_path / 'preferred.db'
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + url_target.as_posix())
    conn = connect(str(target))
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='example'").fetchone() is None
    conn.close()
    assert url_target.exists()


def test_worker_reconnect_preserves_source(monkeypatch, tmp_path):
    from fitness_agent.loop import _db_path_for_conn, _open_worker_conn
    monkeypatch.delenv('DATABASE_URL', raising=False)
    conn = connect(tmp_path / 'jobs.db')
    conn.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)')
    conn.commit()
    worker = _open_worker_conn(_db_path_for_conn(conn))
    worker.execute('INSERT INTO jobs VALUES (?)', (7,))
    worker.commit()
    worker.close()
    assert conn.execute('SELECT id FROM jobs').fetchone()[0] == 7
    conn.close()
