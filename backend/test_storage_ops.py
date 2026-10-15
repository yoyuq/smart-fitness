import os,time,uuid
import pytest
from fastapi.testclient import TestClient
from storage.database import connect,initialize,database_source
from storage import ops


def fixture_session():
    import auth
    initialize();user=auth.register('ops_'+uuid.uuid4().hex,'test-password')
    sid='ops-'+uuid.uuid4().hex;conn=connect()
    conn.execute('INSERT INTO sessions(session_id,user_id,status) VALUES (?,?,?)',(sid,str(user['user_id']),'completed'));conn.commit();conn.close()
    return user,sid


def test_postgres_environment_credentials_encoded(monkeypatch):
    from sqlalchemy.engine import make_url
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('DB_ENGINE','postgresql')
    for k,v in {'POSTGRES_USER':'user','POSTGRES_PASSWORD':'test:p@ss/#?','POSTGRES_DB':'db'}.items():monkeypatch.setenv(k,v)
    assert make_url(database_source()).password=='test:p@ss/#?'
    monkeypatch.setenv('DATABASE_URL','sqlite:///:memory:');assert database_source()=='sqlite:///:memory:'


def test_batch_api_ownership_bounds_atomic_failure_and_retention(monkeypatch):
    from main import app
    user,sid=fixture_session();client=TestClient(app);headers={'Authorization':'Bearer '+user['token']}
    payload={'poses':[{'timestamp':100,'exercise_type':'squat','rep_count':1,'form_score':80,'angles':{'knee':90},'landmarks':[{'x':.5}]}],'sensors':[{'timestamp':101,'heart_rate':75}]}
    url=f'/api/sessions/{sid}/telemetry/batch'
    assert client.post(url,json=payload).status_code==401
    import auth
    other={'Authorization':'Bearer '+auth.generate_token(user['user_id']+999,'other')}
    assert client.post(url,json=payload,headers=other).status_code==404
    assert client.post(url,json=payload,headers=headers).json()=={'poses':1,'sensors':1}
    assert client.post(url,json={'poses':[],'sensors':[]},headers=headers).status_code==400
    assert client.post(url,json={'poses':payload['poses']*1001},headers=headers).status_code==400
    conn=connect();assert conn.execute('SELECT count(*) FROM pose_data WHERE session_id=?',(sid,)).fetchone()[0]==1
    assert '"x": 0.5' in conn.execute('SELECT landmarks_json FROM pose_data WHERE session_id=?',(sid,)).fetchone()[0]
    # Force the second INSERT to fail, after pose executemany; no partial batch.
    from storage.telemetry import write_batch
    from main import PoseFrame,SensorReading
    sensor=SensorReading(timestamp=101);sensor.heart_rate='invalid-non-numeric-value'
    if os.environ.get('DATABASE_URL','').startswith('postgresql'):
        with pytest.raises(Exception):write_batch(sid,user['user_id'],[PoseFrame(**payload['poses'][0])],[sensor])
        assert conn.execute('SELECT count(*) FROM pose_data WHERE session_id=?',(sid,)).fetchone()[0]==1
    active=sid+'-active';conn.execute("INSERT INTO sessions(session_id,user_id,status) VALUES (?,?, 'active')",(active,str(user['user_id'])))
    conn.execute('INSERT INTO pose_data(session_id,timestamp) VALUES (?,?)',(active,100));conn.commit()
    dry=ops.retain_telemetry(200);assert dry['tables']['pose_data']['deleted']==0
    applied=ops.retain_telemetry(200,True,batch_size=1,max_batches=20)
    assert applied['tables']['pose_data']['deleted']>=1
    assert conn.execute('SELECT count(*) FROM pose_data WHERE session_id=?',(sid,)).fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM pose_data WHERE session_id=?',(active,)).fetchone()[0]==1
    conn.close()


def test_media_import_dry_run_and_local_to_cos(assets,monkeypatch,tmp_path):
    from test_storage_media import FakeSdk
    from storage.media import MediaService,CosStore
    uid,sid,data,_=assets;root=tmp_path/'source';key=f'rep_frames/{sid}/old/peak.jpg';path=root/key
    path.parent.mkdir(parents=True);path.write_bytes(data)
    assert ops.import_media(root)['owned']==1
    conn=connect();assert MediaService().row(conn,key) is None;conn.close()
    assert ops.import_media(root,True)['queued_or_ready']==1
    monkeypatch.setenv('MEDIA_BACKEND','cos');assert ops.import_media(root,True)['queued_or_ready']==1
    sdk=FakeSdk();service=MediaService(CosStore(sdk,'test'));assert service.retry_key(key) and key in sdk.objects
    service.delete_prefix(key.rsplit('/',1)[0],uid)
    assert ops.import_media(root,True)['tombstoned']==1


from test_storage_media import assets

def test_doctor_cli_schema_and_private_production_config(tmp_path,monkeypatch):
    import subprocess,sys,json
    from pathlib import Path
    env=os.environ.copy();env.pop('DATABASE_URL',None);env.pop('DB_ENGINE',None)
    env.update(DB_PATH=str(tmp_path/'doctor.db'),MEDIA_DATA_ROOT=str(tmp_path/'media'),MEDIA_BACKEND='local',ENV='production',JWT_SECRET='test-only-jwt-secret-which-is-long-enough')
    backend=Path(__file__).resolve().parent
    def run(*args):return subprocess.run([sys.executable,'-m','storage',*args],cwd=backend,env=env,capture_output=True,text=True,timeout=30)
    assert run('upgrade').returncode==0
    result=run('doctor');assert result.returncode==0 and json.loads(result.stdout)['ready']
    env.pop('JWT_SECRET');env.pop('MEDIA_SIGNING_SECRET',None)
    result=run('doctor');assert result.returncode==2 and not json.loads(result.stdout)['ready']
    assert 'secret-which' not in result.stdout

def test_legacy_migration_uses_actual_configured_sqlite(monkeypatch,tmp_path):
    from migrate_v3 import _resolve_db_path
    target=tmp_path/'url.db';monkeypatch.setenv('DATABASE_URL','sqlite:///'+target.as_posix())
    assert _resolve_db_path(None)==target
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://synthetic:synthetic@localhost/db')
    with pytest.raises(ValueError,match='storage upgrade'):_resolve_db_path(None)
    assert _resolve_db_path(str(target))==target

def test_media_import_skips_deleted_prefix_for_uncatalogued_frames(assets,tmp_path):
    from storage.media import MediaService
    uid,sid,data,_=assets;prefix=f'rep_clips/{sid}/previously-deleted';MediaService().delete_prefix(prefix,uid)
    source=tmp_path/'old-backup';path=source/prefix/'f999.jpg';path.parent.mkdir(parents=True);path.write_bytes(data)
    for apply in (False,True):
        report=ops.import_media(source,apply)
        assert report['tombstoned']==1 and report['owned']==0 and report['queued_or_ready']==0


def test_postgres_doctor_rejects_readonly_and_missing_sequence_permissions(monkeypatch,tmp_path):
    import sqlalchemy as sa
    from storage.database import database_url,engine_for
    from storage.__main__ import upgrade
    url=sa.engine.make_url(database_url())
    if url.get_backend_name()!='postgresql':pytest.skip('PostgreSQL role privileges')
    admin=sa.create_engine(url,isolation_level='AUTOCOMMIT',hide_parameters=True)
    with admin.connect() as conn:
        if not conn.exec_driver_sql('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').scalar():
            admin.dispose();pytest.skip('Disposable DB/role probe requires test superuser')
    role='probe_role_'+uuid.uuid4().hex[:12];database='doctor_probe_'+uuid.uuid4().hex[:12]
    target=url.set(database=database);role_url=target.update_query_dict({'options':'-c role='+role})
    target_text=target.render_as_string(hide_password=False);role_text=role_url.render_as_string(hide_password=False)
    created_role=created_db=False;engine=None
    monkeypatch.setenv('ENV','production');monkeypatch.setenv('JWT_SECRET','test-only-private-jwt-secret-more-than-32-characters');monkeypatch.setenv('MEDIA_BACKEND','local');monkeypatch.setenv('MEDIA_DATA_ROOT',str(tmp_path/'media'))
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql('CREATE ROLE '+role+' NOLOGIN');created_role=True
            conn.exec_driver_sql('CREATE DATABASE '+database);created_db=True
        monkeypatch.setenv('DATABASE_URL',target_text);upgrade()
        engine=sa.create_engine(target,hide_parameters=True)
        with engine.begin() as conn:
            conn.exec_driver_sql('GRANT CONNECT ON DATABASE '+database+' TO '+role)
            conn.exec_driver_sql('GRANT USAGE ON SCHEMA public TO '+role)
            conn.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA public TO '+role)
        monkeypatch.setenv('DATABASE_URL',role_text)
        report=ops.doctor();assert report['schema_current'] and not report['database_writable'] and not report['ready']
        with engine.begin() as conn:conn.exec_driver_sql('GRANT INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO '+role)
        assert not ops.doctor()['ready']  # Table grants alone do not allow SERIAL inserts.
        with engine.begin() as conn:conn.exec_driver_sql('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO '+role)
        assert ops.doctor()['ready']
        # The app must actually work with DML grants and no table ownership/DDL.
        from fitness_agent.history import add_agent_chat_message,get_agent_chat_history
        from fitness_agent.memory import add_layered_memory,build_memory_snapshot
        from fitness_agent.permissions import create_approval,list_pending_approvals
        import auth
        assert auth.register('role_business_'+uuid.uuid4().hex,'test-password')['ok']
        app_conn=connect()
        try:
            assert get_agent_chat_history(app_conn,1)==[]
            add_agent_chat_message(app_conn,1,'user','generated role test')
            assert len(get_agent_chat_history(app_conn,1))==1
            assert add_layered_memory(app_conn,1,'generated storage role memory')
            assert build_memory_snapshot(app_conn,1)
            assert create_approval(app_conn,1,'save_body_metrics',{},'generated test')
            assert len(list_pending_approvals(app_conn,1))==1
        finally:app_conn.close()
        with engine.connect() as conn:assert conn.exec_driver_sql('SELECT count(*) FROM media_objects').scalar()==0
        # A stamped but damaged schema must not trigger request-time DDL.
        with engine.begin() as conn:conn.exec_driver_sql('DROP INDEX idx_agent_chat_user_id_id')
        assert not ops.doctor()['ready']
        app_conn=connect()
        try:
            with pytest.raises(RuntimeError,match='index is missing'):get_agent_chat_history(app_conn,1)
        finally:app_conn.close()
    finally:
        engine_for(role_text).dispose();engine_for(target_text).dispose()
        if engine:engine.dispose()
        with admin.connect() as conn:
            if created_db:conn.exec_driver_sql('DROP DATABASE '+database+' WITH (FORCE)')
            if created_role:conn.exec_driver_sql('DROP ROLE '+role)
        admin.dispose()

def test_batch_rejects_all_nonfinite_numbers_without_partial_rows():
    import json,copy
    from main import app
    user,sid=fixture_session();client=TestClient(app);headers={'Authorization':'Bearer '+user['token'],'Content-Type':'application/json'}
    template={'poses':[{'timestamp':123,'exercise_type':'squat','rep_count':1,'form_score':80,'angles':{'knee':90},'landmarks':[{'x':.5}]}],'sensors':[{'timestamp':124,'heart_rate':75}]}
    cases=[('poses','form_score'),('poses','rep_count'),('poses','timestamp'),('poses','angles'),('poses','landmarks')]+[('sensors',name) for name in ('timestamp','heart_rate','hr_confidence','movement_intensity','body_angle','accel_x','accel_y','accel_z')]
    for value in (float('nan'),float('inf'),float('-inf')):
        for kind,field in cases:
            payload=copy.deepcopy(template)
            if field=='angles':payload[kind][0][field]['knee']=value
            elif field=='landmarks':payload[kind][0][field][0]['x']=value
            else:payload[kind][0][field]=value
            response=client.post(f'/api/sessions/{sid}/telemetry/batch',content=json.dumps(payload),headers=headers)
            assert response.status_code in (400,422),(kind,field,response.status_code)
    conn=connect()
    try:
        for table in ('pose_data','sensor_data'):assert conn.execute(f'SELECT count(*) FROM {table} WHERE session_id=?',(sid,)).fetchone()[0]==0
    finally:conn.close()
