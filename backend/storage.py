import asyncio, os
from pathlib import Path
import requests
STORAGE_MODE=os.environ.get('STORAGE_MODE','local')
LOCAL_STORAGE_DIR=Path(os.environ.get('LOCAL_STORAGE_DIR',str(Path(__file__).resolve().parents[1]/'data'/'storage')))
STORAGE_URL=os.environ.get('EMERGENT_STORAGE_URL','').rstrip('/') + '/objstore/api/v1/storage'
APP_NAME='varunanetra'; _storage_key=None

def _safe(path):
    p=Path(path); clean=Path(*[x for x in p.parts if x not in ('','.')])
    if '..' in clean.parts: raise ValueError('invalid storage path')
    return LOCAL_STORAGE_DIR/clean

def init_storage(force=False): return None

def storage_available(): return STORAGE_MODE=='local' or bool(os.environ.get('EMERGENT_LLM_KEY'))

def _put_local(path,data,content_type):
    p=_safe(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data); return {'path':path,'content_type':content_type,'bytes':len(data)}
def _get_local(path):
    p=_safe(path); return p.read_bytes(),'application/octet-stream'

def _put_remote(path,data,content_type):
    raise RuntimeError('remote Emergent storage is opt-in; use STORAGE_MODE=local or configure a remote adapter')

def _get_remote(path): raise RuntimeError('remote Emergent storage is opt-in')
async def put_object(path,data,content_type): return await asyncio.to_thread(_put_local if STORAGE_MODE=='local' else _put_remote,path,data,content_type)
async def get_object(path): return await asyncio.to_thread(_get_local if STORAGE_MODE=='local' else _get_remote,path)
