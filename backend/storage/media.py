"""Private media, durable upload retries and verified local materialization."""
import hashlib
import hmac
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode
from .database import BACKEND_ROOT, connect, table_exists

MAX_BYTES=16*1024*1024


def media_root():
    return Path(os.getenv('MEDIA_DATA_ROOT') or BACKEND_ROOT/'data').expanduser().resolve()


def resource_key(value):
    value=str(value).replace('\\','/')
    if Path(value).is_absolute() or re.match(r'^[A-Za-z]:',value):
        path=Path(value).resolve()
        for root in (media_root(),(BACKEND_ROOT/'data').resolve()):
            try:value=path.relative_to(root).as_posix();break
            except ValueError:continue
        else:raise ValueError('Media path outside configured roots')
    elif value.startswith('data/'):
        value=value[5:]
    parts=value.split('/')
    if not value or parts[0] not in ('rep_frames','rep_clips','collages','monitor') or any(p in ('','.','..') for p in parts) or any(ord(c)<32 for c in value) or any(c in value for c in ':?#%'):
        raise ValueError('Invalid media resource key')
    return value


def local_path(key):
    path=(media_root()/resource_key(key)).resolve()
    if not path.is_relative_to(media_root()):
        raise ValueError('Media path escapes configured root')
    return path


def atomic_write(key,data):
    if len(data)>MAX_BYTES:raise ValueError('Media object exceeds size limit')
    path=local_path(key);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
        temp=Path(stream.name)
        try:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        except Exception:
            temp.unlink(missing_ok=True);raise
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    return path


class CosStore:
    def __init__(self,client=None,bucket=None):
        self.bucket=bucket or os.getenv('COS_BUCKET')
        if not self.bucket:raise ValueError('COS_BUCKET is required')
        if client is None:
            from qcloud_cos import CosConfig,CosS3Client
            required=('COS_REGION','COS_SECRET_ID','COS_SECRET_KEY')
            if any(not os.getenv(k) for k in required):raise ValueError('COS region and credentials are not configured')
            cfg=CosConfig(Region=os.environ['COS_REGION'],SecretId=os.environ['COS_SECRET_ID'],SecretKey=os.environ['COS_SECRET_KEY'],Token=os.getenv('COS_SESSION_TOKEN'),Scheme='https',Timeout=10)
            client=CosS3Client(cfg,retry=0)
        self.client=client
    def put(self,key,data):
        self.client.put_object(Bucket=self.bucket,Key=resource_key(key),Body=data,ContentType=mimetypes.guess_type(key)[0] or 'application/octet-stream',EnableMD5=True,ACL='private')
    def get(self,key):
        response=self.client.get_object(Bucket=self.bucket,Key=resource_key(key))
        stream=response['Body'].get_raw_stream()
        try:
            data=stream.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise ValueError('Remote object exceeds size limit')
            return data
        finally:stream.close()
    def url(self,key,ttl=300):
        url=self.client.get_presigned_url(Bucket=self.bucket,Key=resource_key(key),Method='GET',Expired=min(300,max(30,int(ttl))))
        if not url.startswith('https://'):raise ValueError('COS media links must use HTTPS')
        return url


class MediaService:
    def __init__(self,cos=None):
        self.backend=os.getenv('MEDIA_BACKEND','local').lower()
        if self.backend not in ('local','cos'):raise ValueError('MEDIA_BACKEND must be local or cos')
        self._cos=cos
    @property
    def cos(self):
        if self._cos is None:self._cos=CosStore()
        return self._cos
    def row(self,conn,key):
        if not table_exists(conn,'media_objects'):return None
        row=conn.execute('SELECT * FROM media_objects WHERE resource_key=?',(resource_key(key),)).fetchone()
        return dict(row) if row else None
    def owner(self,conn,key):
        key=resource_key(key);row=self.row(conn,key)
        if row:return int(row['user_id'])
        parts=key.split('/')
        if parts[0] not in ('rep_frames','rep_clips') or len(parts)<3:return None
        row=conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(parts[1],)).fetchone()
        try:return int(row[0]) if row and row[0] is not None else None
        except (ValueError,TypeError):return None
    def prefix_deleted(self,conn,key):
        parts=resource_key(key).split('/')
        for end in range(2,len(parts)):
            marker='/'.join(parts[:end])+'/_prefix_tombstone'
            row=self.row(conn,marker)
            if row and row['status']=='deleted':return True
        return False
    def authorize(self,conn,key,user_id):
        if self.owner(conn,key)!=int(user_id):raise PermissionError('Media does not belong to current user')
        row=self.row(conn,key)
        if self.prefix_deleted(conn,key) or (row and row['status'] in ('delete_pending','deleted')):raise PermissionError('Media was deleted')
    def publish(self,key,data,user_id,session_id=None):
        key=resource_key(key)
        if len(data)>MAX_BYTES:raise ValueError('Media object exceeds size limit')
        if '_prefix_tombstone' in key.split('/'):raise ValueError('Reserved media key')
        digest=hashlib.sha256(data).hexdigest();now=time.time()
        conn=connect()
        try:
            if session_id:
                session=conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(session_id,)).fetchone()
                if not session or str(session[0])!=str(user_id):raise PermissionError('Media session owner mismatch')
            if self.prefix_deleted(conn,key):raise ValueError('Deleted media prefix cannot be reused')
            # Staging is not uploadable until durable bytes exist.
            conn.execute('INSERT INTO media_objects(resource_key,user_id,session_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_key) DO NOTHING',
                (key,int(user_id),session_id,self.backend,'staging',0,digest,len(data),now,now))
            row=self.row(conn,key)
            if row['user_id']!=int(user_id) or row['sha256']!=digest:raise ValueError('Media key already belongs to different immutable content')
            if row['status'] in ('deleted','delete_pending'):raise ValueError('Deleted media keys cannot be reused')
            if row['backend']!=self.backend:
                conn.execute("UPDATE media_objects SET backend=?,status='staging',attempts=0,error_type=NULL,updated_at=? WHERE resource_key=? AND status NOT IN ('deleted','delete_pending')",(self.backend,now,key))
                row['backend']=self.backend
            conn.commit()
        except Exception:
            conn.rollback();raise
        finally:conn.close()
        atomic_write(key,data)
        conn=connect()
        try:
            if self.prefix_deleted(conn,key):
                conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,updated_at=? WHERE resource_key=? AND status NOT IN ('deleted','delete_pending')",(time.time(),key))
            else:
                conn.execute("UPDATE media_objects SET status='pending',attempts=CASE WHEN error_type='staging_incomplete' THEN 0 ELSE attempts END,error_type=NULL,updated_at=? WHERE resource_key=? AND (status='staging' OR (status='failed' AND error_type='staging_incomplete'))",(time.time(),key))
            conn.commit()
            deleted=self.row(conn,key)['status'] in ('deleted','delete_pending')
        finally:conn.close()
        if deleted:
            self.retry_key(key)
            local_path(key).unlink(missing_ok=True)
            raise ValueError('Media was deleted while publishing')
        # Camera ingestion waits for durable local bytes, never for cloud I/O.
        if row['backend']=='local':self.retry_key(key)
        return key
    def retry_key(self,key):
        key=resource_key(key);conn=connect()
        try:
            row=self.row(conn,key)
            if not row:raise ValueError('Media object is not catalogued')
            if self.prefix_deleted(conn,key) and row['status'] not in ('deleted','delete_pending'):
                conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,updated_at=? WHERE resource_key=? AND status NOT IN ('deleted','delete_pending')",(time.time(),key));conn.commit();row=self.row(conn,key)
            if row['status'] in ('ready','deleted'):return True
            if row['status']=='delete_pending':
                for _ in range(3):
                    changed=conn.execute("UPDATE media_objects SET attempts=attempts+1,updated_at=? WHERE resource_key=? AND attempts<9 AND status='delete_pending'",(time.time(),key));conn.commit()
                    if changed.rowcount<=0:return False
                    try:
                        if row['backend']=='cos':self.cos.client.delete_object(Bucket=self.cos.bucket,Key=key)
                        local_path(key).unlink(missing_ok=True)
                        conn.execute("UPDATE media_objects SET status='deleted',error_type=NULL,updated_at=? WHERE resource_key=?",(time.time(),key));conn.commit();return True
                    except Exception as exc:
                        conn.execute('UPDATE media_objects SET error_type=?,updated_at=? WHERE resource_key=?',(type(exc).__name__,time.time(),key));conn.commit()
                return False
            path=local_path(key)
            if row['status']=='staging':
                # Recover a crash after atomic rename, before catalog publication.
                if not path.is_file():
                    if time.time()-row['updated_at']>60:
                        conn.execute("UPDATE media_objects SET status='failed',attempts=9,error_type='staging_incomplete',updated_at=? WHERE resource_key=? AND status='staging'",(time.time(),key));conn.commit()
                    return False
                if path.stat().st_size<=MAX_BYTES and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:
                    conn.execute("UPDATE media_objects SET status='pending',updated_at=? WHERE resource_key=? AND status='staging'",(time.time(),key));conn.commit();row=self.row(conn,key)
            if not path.is_file() or path.stat().st_size>MAX_BYTES:
                conn.execute("UPDATE media_objects SET status='failed',attempts=9,error_type='cache_missing',updated_at=? WHERE resource_key=? AND status IN ('pending','failed','staging')",(time.time(),key));conn.commit();return False
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:
                conn.execute("UPDATE media_objects SET status='failed',attempts=9,error_type='cache_checksum',updated_at=? WHERE resource_key=? AND status IN ('pending','failed','staging')",(time.time(),key));conn.commit();return False
            for _ in range(3):
                if row['attempts']>=9:break
                # Durable total bound survives restarts; no hidden SDK retries.
                changed=conn.execute("UPDATE media_objects SET attempts=attempts+1,updated_at=? WHERE resource_key=? AND attempts<9 AND status NOT IN ('ready','deleted','delete_pending')",(time.time(),key));conn.commit()
                if changed.rowcount<=0:break
                row=self.row(conn,key)
                try:
                    if row['backend']=='cos':self.cos.put(key,data)
                    changed=conn.execute("UPDATE media_objects SET status='ready',error_type=NULL,updated_at=? WHERE resource_key=? AND status IN ('pending','failed')",(time.time(),key));conn.commit()
                    if changed.rowcount<=0:
                        current=self.row(conn,key)
                        if current['status']=='deleted':
                            conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,error_type='late_upload_cleanup',updated_at=? WHERE resource_key=? AND status='deleted'",(time.time(),key));conn.commit()
                        return current['status']=='ready'
                    return True
                except Exception as exc:
                    conn.execute("UPDATE media_objects SET status='failed',error_type=?,updated_at=? WHERE resource_key=? AND status IN ('pending','failed')",(type(exc).__name__,time.time(),key));conn.commit()
                    current=self.row(conn,key)
                    if current['status'] in ('deleted','delete_pending'):
                        # Timeout may mean PUT succeeded remotely after DELETE.
                        conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,error_type='late_upload_cleanup',updated_at=? WHERE resource_key=? AND status='deleted'",(time.time(),key));conn.commit()
                        return False
            return False
        finally:conn.close()
    def retry_pending(self,limit=25,stop_event=None):
        conn=connect()
        try:keys=[r[0] for r in conn.execute("SELECT resource_key FROM media_objects WHERE status IN ('staging','pending','failed','delete_pending') AND attempts<9 ORDER BY updated_at LIMIT ?",(min(100,max(1,int(limit))),)).fetchall()]
        finally:conn.close()
        checked=ready=0
        for key in keys:
            if stop_event is not None and stop_event.is_set():break
            checked+=1;ready+=self.retry_key(key)
        return {'checked':checked,'ready':ready}
    def materialize(self,key,user_id,conn=None):
        key=resource_key(key);owned=conn is None;conn=conn or connect()
        try:
            self.authorize(conn,key,user_id);row=self.row(conn,key);path=local_path(key)
            if not row and not path.exists():
                legacy=(BACKEND_ROOT/'data'/key).resolve()
                if legacy.is_relative_to((BACKEND_ROOT/'data').resolve()) and legacy.is_file():path=legacy
            if path.is_file() and path.stat().st_size<=MAX_BYTES:
                if not row or hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:return path
            if row and row['backend']=='cos' and row['status']=='ready':
                data=self.cos.get(key)
                if len(data)!=row['byte_size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Remote object checksum mismatch')
                return atomic_write(key,data)
            raise FileNotFoundError('Media object is unavailable')
        finally:
            if owned:conn.close()
    def delete(self,key,user_id,conn=None):
        key=resource_key(key);owned=conn is None;conn=conn or connect()
        try:
            row=self.row(conn,key)
            if self.owner(conn,key)!=int(user_id):raise PermissionError('Media does not belong to current user')
            if row and row['status']=='deleted':return True
            already_pending=row and row['status']=='delete_pending'
            if not row:
                path=local_path(key)
                if not path.is_file():
                    legacy=(BACKEND_ROOT/'data'/key).resolve()
                    if legacy.is_relative_to((BACKEND_ROOT/'data').resolve()):path=legacy
                data=path.read_bytes();now=time.time()
                conn.execute("INSERT INTO media_objects(resource_key,user_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (?,?,'local','delete_pending',0,?,?,?,?) ON CONFLICT(resource_key) DO NOTHING",(key,int(user_id),hashlib.sha256(data).hexdigest(),len(data),now,now))
            if not already_pending:
                conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,updated_at=? WHERE resource_key=?",(time.time(),key));conn.commit()
        finally:
            if owned:conn.close()
        return self.retry_key(key)
    def delete_prefix(self,prefix,user_id,conn=None):
        prefix=resource_key(prefix).rstrip('/');owned=conn is None;conn=conn or connect()
        try:
            if self.owner(conn,prefix+'/placeholder.jpg')!=int(user_id):raise PermissionError('Media prefix owner mismatch')
            now=time.time();marker=prefix+'/_prefix_tombstone'
            conn.execute("INSERT INTO media_objects(resource_key,user_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (?,?,'local','deleted',0,?,0,?,?) ON CONFLICT(resource_key) DO NOTHING",(marker,int(user_id),hashlib.sha256(b'').hexdigest(),now,now))
            escaped=prefix.replace('!','!!').replace('%','!%').replace('_','!_')+'/%'
            # Commit every known cleanup intent with the prefix barrier. A crash
            # before the first external DELETE must remain resumable.
            conn.execute("UPDATE media_objects SET status='delete_pending',attempts=CASE WHEN status='delete_pending' THEN attempts ELSE 0 END,updated_at=? WHERE user_id=? AND resource_key LIKE ? ESCAPE '!' AND status!='deleted'",(now,int(user_id),escaped));conn.commit()
            keys=[r[0] for r in conn.execute("SELECT resource_key FROM media_objects WHERE user_id=? AND resource_key LIKE ? ESCAPE '!' AND status!='deleted'",(int(user_id),escaped)).fetchall()]
            directory=local_path(prefix)
            if directory.is_dir():keys+= [prefix+'/'+p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png')]
            pending=0
            for key in set(keys):
                try:pending+=not self.delete(key,user_id,conn)
                except FileNotFoundError:pass
            return pending
        finally:
            if owned:conn.close()
    def keys(self,prefix,user_id,conn=None):
        prefix=resource_key(prefix).rstrip('/');owned=conn is None;conn=conn or connect()
        try:
            self.authorize(conn,prefix+'/placeholder.jpg',user_id)
            paths=set()
            directory=local_path(prefix)
            directories=(directory,(BACKEND_ROOT/'data'/prefix).resolve())
            for candidate in directories:
                if candidate.is_relative_to(media_root()) or candidate.is_relative_to((BACKEND_ROOT/'data').resolve()):
                    if candidate.is_dir():paths.update(prefix+'/'+p.name for p in candidate.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png'))
            if table_exists(conn,'media_objects'):
                escaped=prefix.replace('!','!!').replace('%','!%').replace('_','!_')+'/%'
                rows=conn.execute("SELECT resource_key FROM media_objects WHERE user_id=? AND status NOT IN ('deleted','delete_pending') AND resource_key LIKE ? ESCAPE '!' ORDER BY resource_key LIMIT 1001",(int(user_id),escaped)).fetchall()
                paths.update(r[0] for r in rows)
            if len(paths)>1000:raise ValueError('Media prefix exceeds bounded frame count')
            visible=[]
            for key in sorted(paths):
                try:self.authorize(conn,key,user_id);visible.append(key)
                except (PermissionError,ValueError):pass
            return visible
        finally:
            if owned:conn.close()
    def clip(self,prefix,user_id,conn=None):
        keys=self.keys(prefix,user_id,conn)
        for key in keys:
            path=self.materialize(key,user_id,conn)
            if path!=local_path(key):atomic_write(key,path.read_bytes())
        return local_path(prefix)
    def signing_key(self):
        configured=os.getenv('MEDIA_SIGNING_SECRET') or os.getenv('JWT_SECRET')
        secret=configured or 'smart-fitness-local-development-only'
        if os.getenv('ENV','').lower()=='production' and (not configured or secret=='smart-fitness-local-development-only' or len(secret)<32):raise ValueError('Production media signing secret must have at least 32 characters')
        return secret.encode()
    def signature(self,key,user_id,expires):
        value=f'{int(user_id)}\n{int(expires)}\n{resource_key(key)}'.encode()
        return hmac.new(self.signing_key(),value,hashlib.sha256).hexdigest()
    def url(self,key,user_id,conn=None):
        key=resource_key(key);owned=conn is None;conn=conn or connect()
        try:
            self.authorize(conn,key,user_id);row=self.row(conn,key)
            if row and row['backend']=='cos' and row['status']=='ready':
                try:return self.cos.url(key)
                except Exception:pass  # Verified private cache remains usable during signing/config outages.
            self.materialize(key,user_id,conn)
            expires=int(time.time())+300
            return '/api/media/file?'+urlencode({'key':key,'user_id':int(user_id),'expires':expires,'signature':self.signature(key,user_id,expires)})
        finally:
            if owned:conn.close()
    def signed_file(self,key,user_id,expires,signature):
        now=int(time.time())
        if expires<now or expires>now+300 or not hmac.compare_digest(signature,self.signature(key,user_id,expires)):raise PermissionError('Expired or invalid media link')
        return self.materialize(key,user_id)


def rep_urls(row,user_id,include_frames=True,conn=None):
    service=MediaService();keyframes=[];frames=[]
    for col in ('start_frame','peak_frame','end_frame'):
        if row.get(col):
            try:keyframes.append(service.url(row[col],user_id,conn))
            except (FileNotFoundError,ValueError,PermissionError):pass
    prefix=row.get('clip_dir')
    try:keys=service.keys(prefix,user_id,conn) if prefix else []
    except (FileNotFoundError,ValueError,PermissionError):keys=[]
    if include_frames:
        for key in keys:
            try:frames.append(service.url(key,user_id,conn))
            except (FileNotFoundError,ValueError,PermissionError):pass
    return dict(keyframes=keyframes,frames=frames,frame_count=len(keys),has_images=bool(keyframes or keys))
