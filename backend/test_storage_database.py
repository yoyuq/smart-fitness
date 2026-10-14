import os
import uuid
import pytest
from storage.database import connect, initialize, column_names, table_exists, bind_qmarks, Connection


def test_concurrent_sqlite_first_boot_across_processes(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    env=os.environ.copy();env.pop('DATABASE_URL',None)
    backend=Path(__file__).resolve().parent
    env['DB_PATH']=str(tmp_path/'concurrent.db')
    processes=[subprocess.Popen([sys.executable,'-c','from storage.database import initialize; initialize()'],cwd=backend,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(4)]
    outputs=[p.communicate(timeout=30) for p in processes]
    assert all(p.returncode==0 for p in processes), outputs


def test_qmark_literals_and_comments():
    sql, args = bind_qmarks("SELECT '?' AS value, ? AS n /* ? */ -- ?\n", (12,))
    assert sql == "SELECT '?' AS value, :p0 AS n /* ? */ -- ?\n"
    assert args == {'p0': 12}
    with pytest.raises(ValueError):
        bind_qmarks('SELECT ?,?', (1,))


def test_auth_device_plans_scores_and_transactions():
    import auth
    initialize()
    user = auth.register('storage_' + uuid.uuid4().hex, 'test-password')
    assert user['ok'], user
    uid = user['user_id']
    assert auth.login(user['username'], 'test-password')['ok']
    assert not auth.login(user['username'], 'wrong')['ok']
    assert auth.get_user_profile(uid)['username'] == user['username']
    dev='storage-dev-'+uuid.uuid4().hex
    assert auth.register_device(dev, user_id=uid)['ok']
    conn=connect()
    conn.execute('UPDATE devices SET firmware_version=? WHERE device_id=?', ('keep-me',dev))
    conn.commit();conn.close()
    assert auth.register_device(dev, name='updated', user_id=uid)['ok']
    plan=auth.create_plan(uid, 'test plan', [{'exercise':'squat'}]);assert plan['ok']
    assert auth.get_plans(uid)[0]['plan_id']==plan['plan_id']
    assert not auth.delete_plan(plan['plan_id'], uid+100000)
    assert auth.delete_plan(plan['plan_id'],uid)
    conn=connect()
    assert conn.execute('SELECT firmware_version FROM devices WHERE device_id=?',(dev,)).fetchone()[0]=='keep-me'
    assert table_exists(conn,'rep_scores') and 'clip_dir' in column_names(conn,'rep_scores')
    sid='storage-session-'+uuid.uuid4().hex
    conn.execute('INSERT INTO sessions(session_id,user_id,device_id,status) VALUES (?,?,?,?)',(sid,str(uid),dev,'completed'))
    conn.execute('INSERT INTO rep_scores(session_id,rep_index,total,exercise,ts) VALUES (?,?,?,?,?)',(sid,1,85.5,'squat',123.5))
    conn.commit()
    count=conn.execute('SELECT COUNT(*) FROM rep_scores WHERE session_id=?',(sid,)).fetchone()[0]
    assert count==1
    conn.execute('INSERT INTO exercise_log(user_id,exercise_type,reps,created_at) VALUES (?,?,?,?)',(uid,'squat',1,123))
    conn.commit()
    from fitness_agent.tools import execute_tool
    assert execute_tool(conn,uid,'get_recent_workouts',{'days':365000,'limit':5})['ok']
    assert execute_tool(conn,uid,'get_recent_workouts',{'days':365000,'limit':5,'exercise':'squat'})['ok']
    conn.execute('INSERT INTO rep_scores(session_id,rep_index,total) VALUES (?,?,?)',(sid,2,40))
    conn.rollback()
    assert conn.execute('SELECT COUNT(*) FROM rep_scores WHERE session_id=?',(sid,)).fetchone()[0]==count
    # A failed statement must not poison the surrounding PostgreSQL transaction.
    try: conn.execute('INSERT INTO users(id,username,password_hash,created_at) VALUES (?,?,?,?)',(uid,'duplicate','hash',1.0))
    except Exception: pass
    assert conn.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone()[0]==user['username']
    conn.close()


def test_new_thread_and_mcp_share_database():
    from concurrent.futures import ThreadPoolExecutor
    from fitness_agent.loop import _db_path_for_conn, _open_worker_conn
    from fitness_agent.mcp_server import _open_conn
    conn=connect();initialize()
    source=_db_path_for_conn(conn)
    assert source
    def check():
        worker=_open_worker_conn(source)
        try:return worker.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        finally:worker.close()
    expected=conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    with ThreadPoolExecutor(1) as pool: assert pool.submit(check).result()==expected
    mcp=_open_conn()
    assert mcp.execute('SELECT COUNT(*) FROM users').fetchone()[0]==expected
    mcp.close();conn.close()
