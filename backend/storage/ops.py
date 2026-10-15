"""Bounded operational commands; never switch a running production service."""
import math
import os
import time
from pathlib import Path
from .database import connect,database_url,engine_for
import sqlalchemy as sa
from .media import MediaService,resource_key,media_root,MAX_BYTES,CosStore


def status():
    conn=connect()
    try:
        rows=conn.execute('SELECT status,count(*) AS count FROM media_objects GROUP BY status').fetchall()
        return {'states':{row[0]:int(row[1]) for row in rows}}
    finally:conn.close()


def database_writable(engine):
    from .schema import current_spec
    import uuid
    conn=engine.connect();transaction=conn.begin()
    try:
        if engine.dialect.name=='postgresql':
            for name,spec in current_spec().items():
                for permission in ('SELECT','INSERT','UPDATE','DELETE'):
                    if not conn.execute(sa.text('SELECT has_table_privilege(:table,:permission)'),{'table':name,'permission':permission}).scalar():return False
                if spec['autoincrement']:
                    pk=next(c['name'] for c in spec['columns'] if c['pk'])
                    for permission in ('USAGE','SELECT'):
                        if not conn.execute(sa.text('SELECT has_sequence_privilege(pg_get_serial_sequence(:table,:pk),:permission)'),{'table':name,'pk':pk,'permission':permission}).scalar():return False
        key='monitor/doctor-'+uuid.uuid4().hex+'/probe.bin'
        conn.execute(sa.text("INSERT INTO media_objects(resource_key,user_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (:key,0,'local','staging',0,:sha,0,0,0)"),{'key':key,'sha':'0'*64})
        if conn.execute(sa.text("UPDATE media_objects SET status='pending' WHERE resource_key=:key"),{'key':key}).rowcount!=1:raise RuntimeError('Write probe update failed')
        if conn.execute(sa.text('DELETE FROM media_objects WHERE resource_key=:key'),{'key':key}).rowcount!=1:raise RuntimeError('Write probe delete failed')
        return True
    except Exception:return False
    finally:transaction.rollback();conn.close()


def doctor(probe_cos=False):
    engine=engine_for(database_url());service=MediaService()
    with engine.connect() as conn:
        conn.exec_driver_sql('SELECT 1')
        version=conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() if sa.inspect(conn).has_table('alembic_version') else None
    from alembic.script import ScriptDirectory
    expected=ScriptDirectory(str(Path(__file__).with_name('migrations'))).get_current_head()
    root=media_root();root.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.TemporaryFile(dir=root) as stream:stream.write(b'storage-write-probe');stream.flush();os.fsync(stream.fileno())
    try:service.signing_key();signing_ready=True
    except ValueError:signing_ready=False
    configured=service.backend=='local' or all(os.getenv(name) for name in ('COS_BUCKET','COS_REGION','COS_SECRET_ID','COS_SECRET_KEY'))
    result={'database':engine.dialect.name,'schema':version,'schema_current':version==expected,'signing_ready':signing_ready,'media_backend':service.backend,'media_writable':True,'media_configured':configured,'cos_live_probe':'not_requested'}
    if probe_cos:
        import uuid
        store=CosStore();key='monitor/smoke-'+uuid.uuid4().hex+'/probe.bin';data=b'smart-fitness-storage-probe'
        try:
            store.put(key,data)
            if store.get(key)!=data:raise ValueError('COS read-after-write mismatch')
            store.url(key)
            result['cos_live_probe']='passed'
        finally:store.client.delete_object(Bucket=store.bucket,Key=key)
    jwt=os.getenv('JWT_SECRET','')
    result['auth_ready']=os.getenv('ENV','').lower()!='production' or (len(jwt)>=32 and jwt!='smart-fitness-dev-secret-change-in-prod')
    result['database_writable']=database_writable(engine) if result['schema_current'] else False
    result['ready']=result['schema_current'] and result['database_writable'] and configured and signing_ready and result['auth_ready']
    return result


def import_media(source_root,apply=False,max_files=10000):
    root=Path(source_root).resolve()
    if not root.is_dir():raise ValueError('Media source directory does not exist')
    service=MediaService();conn=connect();report={'mode':'apply' if apply else 'dry_run','examined':0,'owned':0,'unowned':0,'tombstoned':0,'queued_or_ready':0}
    try:
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in ('.jpg','.jpeg','.png'):continue
            if not path.resolve().is_relative_to(root):raise ValueError('Source media symlink escapes source directory')
            report['examined']+=1
            if report['examined']>max_files:raise ValueError('Media import file bound exceeded')
            key=resource_key(path.relative_to(root).as_posix());owner=service.owner(conn,key);row=service.row(conn,key)
            if service.prefix_deleted(conn,key) or (row and row['status'] in ('deleted','delete_pending')):report['tombstoned']+=1;continue
            if owner is None:report['unowned']+=1;continue
            report['owned']+=1
            if apply:
                if path.stat().st_size>MAX_BYTES:raise ValueError('Source media object exceeds size bound')
                session_id=key.split('/')[1] if key.startswith(('rep_frames/','rep_clips/')) else None
                service.publish(key,path.read_bytes(),owner,session_id);report['queued_or_ready']+=1
    finally:conn.close()
    return report


def retain_telemetry(before,apply=False,batch_size=5000,max_batches=20):
    before=float(before)
    if not math.isfinite(before) or not 0<before<=time.time():raise ValueError('Retention cutoff must be a positive past Unix timestamp')
    if not 1<=batch_size<=5000 or not 1<=max_batches<=20:raise ValueError('Retention batch bounds exceeded')
    conn=connect();report={'mode':'apply' if apply else 'dry_run','tables':{}}
    try:
        for name in ('pose_data','sensor_data'):
            where="timestamp<? AND session_id IN (SELECT session_id FROM sessions WHERE status='completed')"
            eligible=conn.execute(f'SELECT count(*) FROM {name} WHERE '+where,(before,)).fetchone()[0]
            deleted=0
            if apply:
                for _ in range(max_batches):
                    cur=conn.execute(f'DELETE FROM {name} WHERE id IN (SELECT id FROM {name} WHERE {where} ORDER BY id LIMIT ?)',(before,batch_size))
                    conn.commit();deleted+=max(0,cur.rowcount)
                    if cur.rowcount<batch_size:break
            report['tables'][name]={'eligible':int(eligible),'deleted':deleted}
        return report
    except Exception:conn.rollback();raise
    finally:conn.close()
