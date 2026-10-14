import sqlite3
import pytest
import sqlalchemy as sa
import os
import uuid
import subprocess
import sys
from pathlib import Path
from contextlib import closing
from storage.schema import initialize_sqlite
from storage.transfer import snapshot, classify, import_sqlite, export_recovery


def make_source(path):
    engine=sa.create_engine('sqlite:///'+path.as_posix())
    initialize_sqlite(engine);engine.dispose()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (41,'migration-user','hash',123.25)")
        conn.execute("INSERT INTO sessions(session_id,user_id) VALUES ('sess','41')")
    return path


def target(path):
    url='sqlite:///'+path.as_posix()
    engine=sa.create_engine(url);initialize_sqlite(engine);engine.dispose()
    return url


def test_online_snapshot_includes_committed_wal(tmp_path):
    source=make_source(tmp_path/'live.db')
    conn=sqlite3.connect(source)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute("INSERT INTO coach_memory(user_id,note,created_at) VALUES (41,'kept in WAL',123)")
    conn.commit()
    output=tmp_path/'snapshot.db';snapshot(source,output)
    renamed=tmp_path/'renamed.db';output.rename(renamed);renamed.rename(output)
    rows,report=classify([output])
    assert rows['coach_memory'][0]['note']=='kept in WAL'
    assert report['tables']['users']['count']==1
    with pytest.raises(ValueError):snapshot(source,output)
    conn.close()


def test_two_sources_classify_deduplicate_reject_conflict(tmp_path):
    one=make_source(tmp_path/'one.db');two=make_source(tmp_path/'two.db')
    rows,report=classify([one,two])
    assert len(rows['users'])==1
    assert report['sources'][1]['tables']['users']['identical_duplicates']==1
    with sqlite3.connect(two) as conn:conn.execute("UPDATE users SET username='conflict' WHERE id=41")
    with pytest.raises(ValueError,match='Conflicting'):classify([one,two])


def test_import_verifies_ids_content_and_nonempty_refusal(tmp_path):
    source=make_source(tmp_path/'source.db');url=target(tmp_path/'target.db')
    report=import_sqlite([source],url)
    assert report['verified']
    with sqlite3.connect(tmp_path/'target.db') as conn:
        assert conn.execute('SELECT id FROM users').fetchone()[0]==41
        assert conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('after','hash',1)").lastrowid>41
    with pytest.raises(ValueError,match='empty'):import_sqlite([source],url)


def test_orphans_and_unknown_columns_are_not_silently_lost(tmp_path):
    source=make_source(tmp_path/'source.db')
    with sqlite3.connect(source) as conn:conn.execute("UPDATE sessions SET user_id='999'")
    with pytest.raises(ValueError,match='orphan'):import_sqlite([source],target(tmp_path/'one.db'))
    report=import_sqlite([source],target(tmp_path/'two.db'),allow_legacy_orphans=True)
    assert report['legacy_orphans_preserved']==1
    with sqlite3.connect(source) as conn:conn.execute('ALTER TABLE users ADD COLUMN unrecognized TEXT')
    with pytest.raises(ValueError,match='Unrecognized'):classify([source])


def test_failed_import_rolls_back_prior_tables(tmp_path):
    source=make_source(tmp_path/'source.db')
    with sqlite3.connect(source) as conn:
        conn.execute('DROP INDEX IF EXISTS idx_agent_background_dedupe')
        # Violate a target-only business condition after earlier tables insert.
    url=target(tmp_path/'target.db')
    engine=sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER fail_users BEFORE INSERT ON users BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sa.exc.IntegrityError):import_sqlite([source],url)
    with engine.connect() as conn:assert conn.exec_driver_sql('SELECT COUNT(*) FROM sessions').scalar()==0
    engine.dispose()


def test_recovery_preserves_new_writes(tmp_path):
    source=make_source(tmp_path/'source.db')
    url='sqlite:///'+source.as_posix()
    with sqlite3.connect(source) as conn:conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (42,'new-after-switch','hash',1)")
    dest=tmp_path/'recovery.db';report=export_recovery(url,dest)
    assert report['verified'] and report['tables']['users']['count']==2
    with sqlite3.connect(dest) as conn:assert conn.execute('SELECT COUNT(*) FROM users WHERE id=42').fetchone()[0]==1


def test_deleted_id_sequence_high_water_survives_import_and_export(tmp_path):
    source=make_source(tmp_path/'source.db')
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (91,'deleted-high','hash',1)")
        conn.execute('DELETE FROM users WHERE id=91');conn.commit()
    url=target(tmp_path/'target.db')
    assert import_sqlite([source],url)['sequences']['users']==91
    with closing(sqlite3.connect(tmp_path/'target.db')) as conn:
        assert conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('after-high','hash',1)").lastrowid>91
    export_recovery('sqlite:///'+source.as_posix(),tmp_path/'recovery-high.db')
    with closing(sqlite3.connect(tmp_path/'recovery-high.db')) as conn:
        assert conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('after-export','hash',1)").lastrowid>91


def test_recovery_rejects_unrecognized_columns(tmp_path):
    source=make_source(tmp_path/'source.db')
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('ALTER TABLE users ADD COLUMN extra_business_field TEXT');conn.commit()
    destination=tmp_path/'refused.db'
    with pytest.raises(ValueError,match='schema mismatch'):export_recovery('sqlite:///'+source.as_posix(),destination)
    assert not destination.exists()


def test_cli_closes_snapshots_and_protects_database_paths(tmp_path):
    source=make_source(tmp_path/'source.db');report=tmp_path/'classification.json'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+source.as_posix()}
    backend=Path(__file__).parent
    result=subprocess.run([sys.executable,'-m','storage','classify','--source',str(source),'--report',str(report)],cwd=backend,env=env,capture_output=True)
    assert result.returncode==0,result.stderr
    assert report.is_file()
    dest=tmp_path/'recovery.db'
    result=subprocess.run([sys.executable,'-m','storage','export-recovery','--output',str(dest),'--report',str(dest),'--writers-stopped'],cwd=backend,env=env,capture_output=True)
    assert result.returncode!=0 and not dest.exists()
    original=source.read_bytes()
    result=subprocess.run([sys.executable,'-m','storage','classify','--source',str(source),'--report',str(source)],cwd=backend,env=env,capture_output=True)
    assert result.returncode!=0 and source.read_bytes()==original


@pytest.mark.skipif(not os.getenv('DATABASE_URL','').startswith('postgresql'),reason='requires isolated PostgreSQL')
def test_real_postgresql_import_sequences_and_new_write_recovery(tmp_path):
    from sqlalchemy.engine import make_url
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    url=make_url(os.environ['DATABASE_URL'])
    admin=sa.create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT',hide_parameters=True)
    name='transfer_'+uuid.uuid4().hex[:12]
    with admin.connect() as conn:conn.exec_driver_sql('CREATE DATABASE '+name)
    pg_url=url.set(database=name)
    try:
        cfg=Config(str(Path(__file__).parent/'alembic.ini'))
        cfg.set_main_option('script_location',str(Path(__file__).parent/'storage/migrations'))
        cfg.attributes['database_url']=pg_url
        command.upgrade(cfg,'head')
        source=make_source(tmp_path/'source.db')
        with sqlite3.connect(source) as conn:conn.execute("INSERT INTO rep_scores(id,session_id,model_quality,evidence_score) VALUES (51,'sess',82.5,90)")
        report=import_sqlite([source],pg_url)
        assert report['verified']
        pg=sa.create_engine(pg_url,hide_parameters=True)
        with pg.begin() as conn:
            assert conn.exec_driver_sql("INSERT INTO users(username,password_hash,created_at) VALUES ('new-pg-write','hash',1) RETURNING id").scalar()>41
            assert conn.exec_driver_sql("INSERT INTO rep_scores(session_id,total) VALUES ('sess',88) RETURNING id").scalar()>51
            conn.exec_driver_sql("INSERT INTO users(id,username,password_hash,created_at) VALUES (199,'deleted-high','hash',1)")
            conn.exec_driver_sql("SELECT setval(pg_get_serial_sequence('users','id'),199,true)")
            conn.exec_driver_sql('DELETE FROM users WHERE id=199')
        recovery=export_recovery(pg_url,tmp_path/'recovered.db')
        assert recovery['tables']['users']['count']==2
        assert recovery['tables']['rep_scores']['count']==2
        with closing(sqlite3.connect(tmp_path/'recovered.db')) as conn:
            assert conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('after-recovery','hash',1)").lastrowid>199
        pg.dispose()
    finally:
        with admin.connect() as conn:conn.exec_driver_sql('DROP DATABASE '+name+' WITH (FORCE)')
        admin.dispose()
