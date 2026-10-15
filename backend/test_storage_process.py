"""Real HTTP process/crash/restart check, using only generated test records."""
import json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
import pytest


def test_real_http_process_crash_restart(tmp_path):
    backend=Path(__file__).resolve().parent
    env=os.environ.copy();env.update(MQTT_ENABLED='false',POSE_WARMUP_ENABLED='false',MEDIA_RETRY_ENABLED='false',AI_AGENT_BACKGROUND_ENABLED='false',ENV='production',JWT_SECRET='process-test-only-private-secret-at-least-32-characters',PYTHONUTF8='1')
    if not env.get('DATABASE_URL'):env['DB_PATH']=str(tmp_path/'restart.db')
    env['MEDIA_DATA_ROOT']=str(tmp_path/'media')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}';token=None
    def request(path,data=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        with urlopen(Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers),timeout=10) as response:return json.load(response)
    def start(log):
        process=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=backend,env=env,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(150):
            if process.poll() is not None:raise AssertionError('Backend exited; inspect process log')
            try:
                if request('/health')['status']=='healthy':return process
            except Exception:time.sleep(.2)
        process.kill();process.wait();raise AssertionError('Backend did not become healthy')
    with (tmp_path/'process.log').open('w',encoding='utf-8') as log:
        process=start(log)
        try:
            username='process_'+uuid.uuid4().hex;password='test-password'
            registered=request('/api/auth/register',{'username':username,'password':password});assert registered['ok'];token=registered['token']
            device='restart-'+uuid.uuid4().hex;assert request('/api/v2/devices/register',{'device_id':device})['ok']
            session=request('/api/v2/training/start',{'device_id':device,'exercise':'squat'});assert session['ok'];sid=session['session_id']
            assert request(f'/api/sessions/{sid}/telemetry/batch',{'poses':[{'timestamp':time.time(),'exercise_type':'squat','rep_count':1,'form_score':81,'angles':{}}]})['poses']==1
            assert request('/api/v2/training/stop',{'device_id':device})['stopped']
            process.kill();process.wait(timeout=20)  # Interrupt the actual writer process.
            process=start(log)
            assert request('/api/auth/login',{'username':username,'password':password})['ok']
            history=request('/api/v2/sessions/history')
            assert sid in json.dumps(history)
            again=request('/api/v2/training/start',{'device_id':device,'exercise':'squat'});assert again['ok'] and again['session_id']!=sid
            assert request('/api/v2/training/stop',{'device_id':device})['stopped']
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=45)
