import asyncio, logging, traceback
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional
from db import db
from models import new_id
from events import publish

logger=logging.getLogger("jobs")
HANDLERS={}
WORKER_COUNT=int(__import__('os').environ.get('JOB_WORKERS','2'))
MAX_ATTEMPTS=int(__import__('os').environ.get('JOB_MAX_ATTEMPTS','3'))
LEASE_SECONDS=int(__import__('os').environ.get('JOB_LEASE_SECONDS','900'))
_tasks=[]


def handler(job_type):
    def deco(fn): HANDLERS[job_type]=fn; return fn
    return deco


async def job_log(job_id,msg,level='info'):
    await db.jobs.update_one({'id':job_id},{'$push':{'logs':{'t':datetime.now(timezone.utc).isoformat(),'level':level,'msg':msg}}})


async def enqueue(job_type,payload,actor='system',inline=False):
    if job_type not in HANDLERS: raise ValueError(f'unknown job type: {job_type}')
    now=datetime.now(timezone.utc); job={'id':new_id(),'type':job_type,'status':'queued','payload':payload,'result':None,'error':None,'attempts':0,'logs':[{'t':now.isoformat(),'level':'info','msg':f'queued {job_type}'}],'actor':actor,'created_at':now,'updated_at':now}
    await db.jobs.insert_one(dict(job))
    if inline: return await process(job['id'])
    job.pop('_id',None); return job


async def _claim_next(worker_id):
    now=datetime.now(timezone.utc); stale=now-timedelta(seconds=LEASE_SECONDS)
    await db.jobs.update_many({'status':'running','lease_until':{'$lt':now}},{'$set':{'status':'queued','updated_at':now},'$unset':{'worker_id':'','lease_until':''}})
    return await db.jobs.find_one_and_update({'status':'queued','attempts':{'$lt':MAX_ATTEMPTS}},{'$set':{'status':'running','worker_id':worker_id,'lease_until':now+timedelta(seconds=LEASE_SECONDS),'started_at':now,'updated_at':now},'$inc':{'attempts':1}},{'return_document':1,'projection':{'_id':0}})


async def process(job_id):
    job=await db.jobs.find_one({'id':job_id},{'_id':0})
    if not job or job['status']=='succeeded': return job
    fn=HANDLERS.get(job['type']);
    if not fn: raise ValueError(f'unknown job type: {job["type"]}')
    attempts=job.get('attempts',0)+1; now=datetime.now(timezone.utc)
    await db.jobs.update_one({'id':job_id},{'$set':{'status':'running','attempts':attempts,'started_at':now,'lease_until':now+timedelta(seconds=LEASE_SECONDS)}})
    await job_log(job_id,f'attempt {attempts} started')
    try:
        result=await fn(job)
        await db.jobs.update_one({'id':job_id},{'$set':{'status':'succeeded','result':result,'finished_at':datetime.now(timezone.utc),'updated_at':datetime.now(timezone.utc)},'$unset':{'lease_until':'','worker_id':''}})
        await job_log(job_id,'completed'); publish('job',{'job_id':job_id,'type':job['type'],'status':'succeeded','case_id':(job.get('payload') or {}).get('case_id'),'result':result})
    except Exception as e:
        logger.error('job %s failed: %s\n%s',job_id,e,traceback.format_exc())
        status='queued' if attempts<MAX_ATTEMPTS else 'failed'
        fields={'status':status,'error':str(e),'updated_at':datetime.now(timezone.utc)}
        if status=='failed': fields['finished_at']=datetime.now(timezone.utc)
        await db.jobs.update_one({'id':job_id},{'$set':fields,'$unset':{'lease_until':'','worker_id':''}})
        await job_log(job_id,f'failed: {e}'+(' — retrying' if status=='queued' else ' — exhausted'),'error')
    return await db.jobs.find_one({'id':job_id},{'_id':0})


async def worker_loop(worker_id):
    while True:
        job=await _claim_next(worker_id)
        if not job:
            await asyncio.sleep(0.5); continue
        try: await process(job['id'])
        except Exception: logger.exception('worker error')


def start():
    global _tasks
    if _tasks: return
    _tasks=[asyncio.create_task(worker_loop(f'worker-{i+1}')) for i in range(max(1,WORKER_COUNT))]


def stop():
    global _tasks
    for t in _tasks: t.cancel()
    _tasks=[]
