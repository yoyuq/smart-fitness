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
