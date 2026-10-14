"""Small resumable outbox pump; upload state and retry budget live in the DB."""
import asyncio
import logging
import os
import threading
from .media import MediaService

log=logging.getLogger('storage.media')
_task=None
_stop=None
_thread_stop=None

async def _loop(stop,thread_stop):
    interval=max(1,min(3600,int(os.getenv('MEDIA_RETRY_INTERVAL_SECONDS','3'))))
    while not stop.is_set():
        try:
            result=await asyncio.to_thread(MediaService().retry_pending,10,thread_stop)
            if result['checked']==10 and result['ready']==10:continue
        except Exception as exc:log.error('media retry pump failed: %s',type(exc).__name__)
        try:await asyncio.wait_for(stop.wait(),timeout=interval)
        except asyncio.TimeoutError:pass

def start():
    global _task,_stop,_thread_stop
    if os.getenv('MEDIA_RETRY_ENABLED','true').lower() in ('false','0','no'):return
    if _task and not _task.done():return
    _stop=asyncio.Event();_thread_stop=threading.Event();_task=asyncio.create_task(_loop(_stop,_thread_stop))

async def stop():
    global _task,_stop,_thread_stop
    if _stop:_stop.set()
    if _thread_stop:_thread_stop.set()
    if _task:
        try:await asyncio.wait_for(asyncio.shield(_task),timeout=35)
        except asyncio.TimeoutError:_task.cancel()
    _task=None;_stop=None;_thread_stop=None
