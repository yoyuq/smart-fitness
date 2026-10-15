import io
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlencode
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from storage.database import connect,initialize
from storage.media import MediaService,CosStore,resource_key,local_path,atomic_write


@pytest.fixture
def assets(monkeypatch,tmp_path):
    import auth
    monkeypatch.setenv('MEDIA_DATA_ROOT',str(tmp_path/'media'))
    monkeypatch.setenv('MEDIA_BACKEND','local')
    monkeypatch.setenv('MEDIA_RETRY_ENABLED','false')
    initialize()
    user=auth.register('media_'+uuid.uuid4().hex,'test-password')
    assert user['ok']
    uid=user['user_id'];sid='media-session-'+uuid.uuid4().hex
    conn=connect()
    conn.execute('INSERT INTO sessions(session_id,user_id,exercise_type,status) VALUES (?,?,?,?)',(sid,str(uid),'squat','active'));conn.commit();conn.close()
    stream=io.BytesIO();Image.new('RGB',(24,24),(80,120,160)).save(stream,format='JPEG')
    return uid,sid,stream.getvalue(),{'Authorization':'Bearer '+user['token']}


class FakeSdk:
    def __init__(self,failures=0):self.objects={};self.failures=failures;self.calls=0;self.last_put=None
    def put_object(self,**kwargs):
        self.calls+=1;self.last_put=kwargs
        if self.calls<=self.failures:raise TimeoutError('simulated transport outage')
        self.objects[kwargs['Key']]=kwargs['Body'];return {'ETag':'test-etag'}
    def get_object(self,**kwargs):
        data=self.objects[kwargs['Key']]
        class Body:
            def get_raw_stream(self):return io.BytesIO(data)
        return {'Body':Body()}
    def get_presigned_url(self,**kwargs):return 'https://example.test/private/'+kwargs['Key']+'?expires='+str(kwargs['Expired'])
    def delete_object(self,**kwargs):self.objects.pop(kwargs['Key'],None)


def test_local_private_links_and_authorization(assets):
    uid,sid,data,headers=assets
    from main import app
    client=TestClient(app);service=MediaService();key=f'rep_frames/{sid}/one/peak.jpg'
    assert service.publish(key,data,uid,sid)==key
    assert client.get('/api/media/url',params={'key':key}).status_code==401
    assert client.get('/repdata/'+key).status_code==401
    assert client.get('/api/media/url',params={'key':key},headers=headers).status_code==200
    url=service.url(key,uid)
    response=client.get(url)
    assert response.status_code==200 and response.content==data
    import auth
    other={'Authorization':'Bearer '+auth.generate_token(uid+10000,'other')}
    assert client.get('/api/media/url',params={'key':key},headers=other).status_code==404
    args={k:v[0] for k,v in parse_qs(urlparse(url).query).items()}
    args['user_id']=str(uid+1)
    assert client.get('/api/media/file?'+urlencode(args)).status_code==403
    with pytest.raises(PermissionError):service.signed_file(key,uid,int(time.time())-1,service.signature(key,uid,int(time.time())-1))


@pytest.mark.parametrize('key',['../private.pem','rep_frames/../../private.pem','rep_frames/a/%2e%2e/file.jpg','C:/private.pem','/etc/passwd','data/rep_frames/a/../b.jpg'])
def test_path_escape_rejected(key):
    with pytest.raises(ValueError):resource_key(key)


def test_immutable_key_and_owner_guard(assets):
    uid,sid,data,_=assets;service=MediaService();key=f'rep_frames/{sid}/immutable.jpg'
    service.publish(key,data,uid,sid)
    with pytest.raises(ValueError):service.publish(key,b'different',uid,sid)
    with pytest.raises(PermissionError):service.publish(key,data,uid+1,sid)
    assert service.materialize(key,uid).read_bytes()==data


def test_cos_queue_retry_restart_download_and_signature(assets,monkeypatch):
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    sdk=FakeSdk(failures=3);store=CosStore(sdk,'private-test-bucket');service=MediaService(store)
    key=f'rep_frames/{sid}/cos/peak.jpg'
    service.publish(key,data,uid,sid)
    assert sdk.calls==0  # Camera ingestion never waits for cloud I/O.
    assert not service.retry_key(key) and sdk.calls==3
    conn=connect();assert service.row(conn,key)['status']=='failed';conn.close()
    # A fresh process/service consumes durable pending state and cache.
    restarted=MediaService(store)
    assert restarted.retry_pending()['ready']>=1
    assert sdk.last_put['EnableMD5'] and sdk.last_put['ACL']=='private'
    local_path(key).unlink()
    assert restarted.materialize(key,uid).read_bytes()==data
    assert restarted.url(key,uid).startswith('https://example.test/private/')
    assert restarted.delete(key,uid)
    with pytest.raises(PermissionError):restarted.materialize(key,uid)
    assert key not in sdk.objects


def test_retry_budget_and_corrupt_cache_are_terminal(assets,monkeypatch):
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    sdk=FakeSdk(failures=100);service=MediaService(CosStore(sdk,'private-test-bucket'));key=f'rep_frames/{sid}/fail.jpg'
    service.publish(key,data,uid,sid)
    for _ in range(5):service.retry_key(key)
    assert sdk.calls==9
    conn=connect();assert service.row(conn,key)['attempts']==9;conn.close()
    corrupt=f'rep_frames/{sid}/corrupt.jpg';service.publish(corrupt,data,uid,sid);local_path(corrupt).write_bytes(b'corrupt')
    assert not service.retry_key(corrupt)
    conn=connect();assert service.row(conn,corrupt)['error_type']=='cache_checksum';conn.close()


def test_late_upload_cannot_resurrect_deleted_media(assets,monkeypatch):
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    class RacingSdk(FakeSdk):
        def put_object(self,**kwargs):
            super().put_object(**kwargs)
            service.delete(kwargs['Key'],uid)
            self.objects[kwargs['Key']]=kwargs['Body']  # PUT completes after DELETE.
    sdk=RacingSdk();service=MediaService(CosStore(sdk,'private-test-bucket'));key=f'rep_frames/{sid}/race.jpg'
    service.publish(key,data,uid,sid)
    assert not service.retry_key(key)
    with pytest.raises(PermissionError):service.url(key,uid)
    assert service.retry_key(key) and key not in sdk.objects
    assert service.delete(key,uid)  # Deletion is idempotent and never resets a live retry budget.
    with pytest.raises(ValueError):service.publish(key,data,uid,sid)


def test_real_training_keyframes_clip_playback_and_ai_reads(assets):
    uid,_,data,headers=assets
    from main import app
    import main_v2_routes as routes
    client=TestClient(app);device='media-device-'+uuid.uuid4().hex
    registered=client.post('/api/v2/devices/register',headers=headers,json={'device_id':device}).json()
    assert registered['ok']
    assert client.get('/api/v2/devices',headers=headers).status_code==200
    result=client.post('/api/v2/training/start',headers=headers,json={'device_id':device,'user_id':uid+10000,'exercise':'squat'}).json()
    assert result['ok'];sid=result['session_id']
    conn=connect();assert conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(sid,)).fetchone()[0]==str(uid);conn.close()
    for index in range(5):routes._buffer_frame(device,100+index,data,session_id=sid)
    rep={'rep_index':1,'clip_id':'rep-one','ts':104,'start_ts':100,'peak_ts':102,'end_ts':104}
    keys=routes._save_rep_keyframes(device,sid,rep);prefix=routes._save_rep_clip(device,sid,rep)
    assert all(keys.values()) and prefix
    conn=connect()
    cursor=conn.execute('INSERT INTO rep_scores(session_id,rep_index,exercise,total,peak_frame,start_frame,end_frame,clip_dir,ts) VALUES (?,?,?,?,?,?,?,?,?)',(sid,1,'squat',86,keys['peak_frame'],keys['start_frame'],keys['end_frame'],prefix,104))
    rep_id=cursor.lastrowid;conn.commit()
    from fitness_agent.toolkit.vision_tools import _resolve_rep_image,_resolve_session_reps
    from fitness_agent.vision_pipeline import sample_rep_frames
    from fitness_agent.vision import _load_image_data_uri
    resolved=_resolve_rep_image(conn,uid,rep_id)
    assert resolved['ok'] and _load_image_data_uri(resolved['image_path'])['ok']
    sampled=sample_rep_frames(resolved['clip_dir'],k=3)
    assert sampled['ok'] and len(sampled['frames'])==3
    assert _resolve_session_reps(conn,uid,sid)['count']==1
    conn.close()
    clip=client.get('/api/v2/monitor/clip/'+str(rep_id),headers=headers)
    assert clip.status_code==200 and len(clip.json()['frames'])==5
    assert client.get(clip.json()['frames'][0]).content==data
    stop=client.post('/api/v2/training/stop',headers=headers,json={'device_id':device})
    assert stop.status_code==200 and stop.json()['stopped']
    assert client.get('/api/v2/sessions/history',headers=headers).status_code==200
    removed=client.post('/api/v2/monitor/rep_delete',headers=headers,json={'rep_id':rep_id})
    assert removed.status_code==200 and removed.json()['ok']
    assert client.get(clip.json()['frames'][0]).status_code in (403,404)

def test_publish_staging_excludes_inflight_writer_and_recovers_crash(assets,monkeypatch):
    import threading
    import storage.media as media
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    sdk=FakeSdk();service=MediaService(CosStore(sdk,'test'));key=f'rep_frames/{sid}/staging.jpg'
    entered=threading.Event();release=threading.Event();real=media.atomic_write;errors=[]
    def paused(key,data):
        entered.set();assert release.wait(10);return real(key,data)
    def writer():
        try:service.publish(key,data,uid,sid)
        except Exception as exc:errors.append(exc)
    monkeypatch.setattr(media,'atomic_write',paused)
    thread=threading.Thread(target=writer);thread.start();assert entered.wait(10)
    assert not service.retry_key(key) and sdk.calls==0
    release.set();thread.join(10);assert not thread.is_alive() and not errors
    assert service.retry_key(key) and sdk.calls==1
    # Persisted complete bytes with staging metadata after an interrupted writer.
    conn=connect();conn.execute("UPDATE media_objects SET status='staging',attempts=0 WHERE resource_key=?",(key,));conn.commit();conn.close()
    assert MediaService(CosStore(sdk,'test')).retry_key(key)


def test_timeout_after_delete_cannot_resurrect(assets,monkeypatch):
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    class RacingSdk(FakeSdk):
        def put_object(self,**kwargs):
            self.calls+=1;service.delete(kwargs['Key'],uid)
            self.objects[kwargs['Key']]=kwargs['Body']
            raise TimeoutError('ambiguous late PUT')
    sdk=RacingSdk();service=MediaService(CosStore(sdk,'test'));key=f'rep_frames/{sid}/timeout.jpg'
    service.publish(key,data,uid,sid);assert not service.retry_key(key) and sdk.calls==1
    with pytest.raises(PermissionError):service.url(key,uid)
    assert service.retry_key(key) and key not in sdk.objects


def test_production_requires_private_signing_secret(monkeypatch):
    monkeypatch.setenv('ENV','production');monkeypatch.delenv('MEDIA_SIGNING_SECRET',raising=False);monkeypatch.delenv('JWT_SECRET',raising=False)
    with pytest.raises(ValueError):MediaService().signing_key()
    monkeypatch.setenv('JWT_SECRET','smart-fitness-local-development-only')
    with pytest.raises(ValueError):MediaService().signing_key()
    monkeypatch.setenv('MEDIA_SIGNING_SECRET','test-only-secret-which-is-at-least-32-characters')
    assert MediaService().signing_key()


def test_real_cos_sdk_constructor_no_network(monkeypatch):
    for name,value in {'COS_BUCKET':'test-1234','COS_REGION':'ap-beijing','COS_SECRET_ID':'synthetic-id','COS_SECRET_KEY':'synthetic-key'}.items():monkeypatch.setenv(name,value)
    store=CosStore();assert store.client is not None


def test_prefix_delete_blocks_frames_not_yet_published(assets,monkeypatch):
    uid,sid,data,_=assets;service=MediaService();prefix=f'rep_clips/{sid}/late'
    service.publish(prefix+'/f000.jpg',data,uid,sid)
    assert service.delete_prefix(prefix,uid)==0
    with pytest.raises(ValueError):service.publish(prefix+'/f001.jpg',data,uid,sid)
    with pytest.raises(PermissionError):service.clip(prefix,uid)


def test_legacy_clip_root_materializes_frames(assets):
    from storage.database import BACKEND_ROOT
    uid,sid,data,_=assets;prefix=f'rep_clips/{sid}/legacy';old=BACKEND_ROOT/'data'/prefix
    old.mkdir(parents=True,exist_ok=True)
    try:
        (old/'f000.jpg').write_bytes(data);(old/'f001.jpg').write_bytes(data)
        service=MediaService();assert len(service.keys(prefix,uid))==2
        clip=service.clip(prefix,uid);assert (clip/'f000.jpg').read_bytes()==data
    finally:
        for path in old.glob('*.jpg'):path.unlink()
        old.rmdir();old.parent.rmdir()


def test_inference_autorevive_persists_owner_before_frames(assets):
    from main import app
    import main_v2_routes as routes
    uid,_,data,headers=assets;device='revive-'+uuid.uuid4().hex
    response=TestClient(app).post('/api/v2/vision/infer/full',headers=headers,json={'device_id':device,'user_id':uid+99,'exercise':'squat'})
    assert response.status_code==200
    sid=routes.active_trainings[device]['session_id'];conn=connect()
    assert conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(sid,)).fetchone()[0]==str(uid);conn.close()
    routes._buffer_frame(device,100,data,session_id=sid)
    assert routes._save_rep_keyframes(device,sid,{'rep_index':1,'start_ts':100})['start_frame']

def test_stop_refuses_to_certify_live_thread(monkeypatch):
    import asyncio
    import threading
    import storage.media_jobs as jobs
    async def scenario():
        release=threading.Event();entered=threading.Event()
        def blocked():entered.set();release.wait(10)
        task=asyncio.create_task(asyncio.to_thread(blocked))
        while not entered.is_set():await asyncio.sleep(.001)
        jobs._task=task;jobs._stop=asyncio.Event();jobs._thread_stop=threading.Event()
        real=asyncio.wait_for
        async def short_wait(awaitable,timeout):return await real(awaitable,timeout=.01)
        with monkeypatch.context() as patch:
            patch.setattr(jobs.asyncio,'wait_for',short_wait)
            with pytest.raises(RuntimeError,match='still active'):await jobs.stop()
        assert jobs._task is task and not task.done()
        release.set();await jobs.stop();assert jobs._task is None
    asyncio.run(scenario())

def test_real_clip_writer_cancelled_by_rep_delete(assets,monkeypatch):
    import threading
    import main_v2_routes as routes
    from main import app
    uid,sid,data,headers=assets;device='clip-race-'+uuid.uuid4().hex
    for i in range(3):routes._buffer_frame(device,100+i,data,session_id=sid)
    prefix=f'rep_clips/{sid}/racing';conn=connect()
    cur=conn.execute('INSERT INTO rep_scores(session_id,rep_index,clip_dir,ts) VALUES (?,?,?,?)',(sid,1,prefix,102));rep_id=cur.lastrowid;conn.commit();conn.close()
    entered=threading.Event();release=threading.Event();real=MediaService.publish;result=[]
    def paused(self,key,*args,**kwargs):
        if key.endswith('f001.jpg'):entered.set();assert release.wait(10)
        return real(self,key,*args,**kwargs)
    monkeypatch.setattr(MediaService,'publish',paused)
    thread=threading.Thread(target=lambda:result.append(routes._save_rep_clip(device,sid,{'clip_id':'racing','rep_index':1,'start_ts':100,'end_ts':102})))
    thread.start();assert entered.wait(10)
    try:
        response=TestClient(app).post('/api/v2/monitor/rep_delete',headers=headers,json={'rep_id':rep_id})
        assert response.status_code==200 and response.json()['ok']
    finally:release.set();thread.join(10)
    assert not thread.is_alive() and result==[None]
    with pytest.raises(PermissionError):MediaService().keys(prefix,uid)
    assert not local_path(prefix+'/f001.jpg').exists()

def test_prefix_delete_intents_survive_crash_before_first_delete(assets,monkeypatch):
    uid,sid,data,_=assets;monkeypatch.setenv('MEDIA_BACKEND','cos')
    sdk=FakeSdk();service=MediaService(CosStore(sdk,'test'));prefix=f'rep_clips/{sid}/delete-crash'
    for i in range(2):
        key=f'{prefix}/f{i:03d}.jpg';service.publish(key,data,uid,sid);assert service.retry_key(key)
    with monkeypatch.context() as patch:
        def interrupted(*args,**kwargs):raise RuntimeError('simulated process interruption')
        patch.setattr(service,'delete',interrupted)
        with pytest.raises(RuntimeError):service.delete_prefix(prefix,uid)
    restarted=MediaService(CosStore(sdk,'test'));assert restarted.retry_pending()['ready']>=2
    assert not any(k.startswith(prefix+'/') for k in sdk.objects)
