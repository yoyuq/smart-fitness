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
