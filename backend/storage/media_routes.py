from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from .media import MediaService

router=APIRouter()

@router.get('/api/media/url')
def media_url(request:Request,key:str):
    import auth
    user=auth.require_auth(request.headers.get('Authorization'))
    if not user:raise HTTPException(401,'Authentication required')
    try:return {'ok':True,'url':MediaService().url(key,user['user_id'])}
    except PermissionError:raise HTTPException(404,'Media unavailable')
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Media unavailable')

@router.get('/api/media/file')
def media_file(key:str,user_id:int,expires:int,signature:str):
    try:path=MediaService().signed_file(key,user_id,expires,signature)
    except PermissionError:raise HTTPException(403,'Expired or invalid media link')
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Media unavailable')
    return FileResponse(path,headers={'Cache-Control':'private, no-store'})

@router.get('/repdata/{key:path}')
def legacy_private_media(request:Request,key:str):
    import auth
    user=auth.require_auth(request.headers.get('Authorization'))
    if not user:raise HTTPException(401,'Authentication required')
    try:path=MediaService().materialize(key,user['user_id'])
    except (PermissionError,ValueError,FileNotFoundError):raise HTTPException(404,'Media unavailable')
    return FileResponse(path,headers={'Cache-Control':'private, no-store'})
