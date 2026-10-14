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
            cfg=CosConfig(Region=os.environ['COS_REGION'],SecretId=os.environ['COS_SECRET_ID'],SecretKey=os.environ['COS_SECRET_KEY'],Token=os.getenv('COS_SESSION_TOKEN'),Scheme='https',Timeout=10,MaxRetry=0)
            client=CosS3Client(cfg)
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
    def authorize(self,conn,key,user_id):
        if self.owner(conn,key)!=int(user_id):raise PermissionError('Media does not belong to current user')
        row=self.row(conn,key)
        if row and row['status'] in ('delete_pending','deleted'):raise PermissionError('Media was deleted')
    def publish(self,key,data,user_id,session_id=None):
        key=resource_key(key)
        if len(data)>MAX_BYTES:raise ValueError('Media object exceeds size limit')
        digest=hashlib.sha256(data).hexdigest();now=time.time()
        conn=connect()
        try:
            if session_id:
                session=conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(session_id,)).fetchone()
                if not session or str(session[0])!=str(user_id):raise PermissionError('Media session owner mismatch')
            # Claim immutable content before touching a shared local path.
            conn.execute('INSERT INTO media_objects(resource_key,user_id,session_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_key) DO NOTHING',
                (key,int(user_id),session_id,self.backend,'pending',0,digest,len(data),now,now))
            row=self.row(conn,key)
            if row['user_id']!=int(user_id) or row['sha256']!=digest:raise ValueError('Media key already belongs to different immutable content')
            if row['status'] in ('deleted','delete_pending'):raise ValueError('Deleted media keys cannot be reused')
            conn.commit()
        except Exception:
            conn.rollback();raise
        finally:conn.close()
        atomic_write(key,data)
        # Camera ingestion waits for durable local bytes, never for cloud I/O.
        if row['backend']=='local':self.retry_key(key)
        return key
    def retry_key(self,key):
        key=resource_key(key);conn=connect()
        try:
            row=self.row(conn,key)
            if not row:raise ValueError('Media object is not catalogued')
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
            if not path.is_file() or path.stat().st_size>MAX_BYTES:
                conn.execute("UPDATE media_objects SET status='failed',attempts=9,error_type='cache_missing',updated_at=? WHERE resource_key=? AND status!='ready'",(time.time(),key));conn.commit();return False
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:
                conn.execute("UPDATE media_objects SET status='failed',attempts=9,error_type='cache_checksum',updated_at=? WHERE resource_key=?",(time.time(),key));conn.commit();return False
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
                    conn.execute("UPDATE media_objects SET status='failed',error_type=?,updated_at=? WHERE resource_key=? AND status!='ready'",(type(exc).__name__,time.time(),key));conn.commit()
            return False
        finally:conn.close()
    def retry_pending(self,limit=25,stop_event=None):
        conn=connect()
        try:keys=[r[0] for r in conn.execute("SELECT resource_key FROM media_objects WHERE status IN ('pending','failed','delete_pending') AND attempts<9 ORDER BY updated_at LIMIT ?",(min(100,max(1,int(limit))),)).fetchall()]
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
                path=self.materialize(key,user_id,conn);data=path.read_bytes();now=time.time()
                conn.execute("INSERT INTO media_objects(resource_key,user_id,backend,status,attempts,sha256,byte_size,created_at,updated_at) VALUES (?,?,'local','delete_pending',0,?,?,?,?) ON CONFLICT(resource_key) DO NOTHING",(key,int(user_id),hashlib.sha256(data).hexdigest(),len(data),now,now))
            if not already_pending:
                conn.execute("UPDATE media_objects SET status='delete_pending',attempts=0,updated_at=? WHERE resource_key=?",(time.time(),key));conn.commit()
        finally:
            if owned:conn.close()
        return self.retry_key(key)
    def keys(self,prefix,user_id,conn=None):
        prefix=resource_key(prefix).rstrip('/');owned=conn is None;conn=conn or connect()
        try:
            self.authorize(conn,prefix+'/placeholder.jpg',user_id)
            paths=set()
            directory=local_path(prefix)
            if directory.is_dir():paths.update(prefix+'/'+p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png'))
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
        for key in keys:self.materialize(key,user_id,conn)
        return local_path(prefix)
    def signing_key(self):
        secret=os.getenv('MEDIA_SIGNING_SECRET') or os.getenv('JWT_SECRET') or 'smart-fitness-local-development-only'
        if os.getenv('ENV')=='production' and len(secret)<32:raise ValueError('Production media signing secret must have at least 32 characters')
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
