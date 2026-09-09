import asyncio, json, logging, os
from datetime import datetime, timezone, timedelta
import websockets
from db import db
from models import AISPositionIn
logger=logging.getLogger('ais_live')
WS_URL='wss://stream.aisstream.io/v0/stream'; DEFAULT_BBOXES=[[[50.0,-5.0],[62.0,12.0]]]
MODE=os.environ.get('AIS_MODE','demo'); _task=None
state={'connected':False,'mode':MODE,'messages':0,'positions':0,'inserted':0,'vessels':set(),'last_message_at':None,'connected_at':None,'error':None,'restarts':0}

async def get_config():
    s=await db.settings.find_one({'key':'ais_live'},{'_id':0}) or {}; key=s.get('api_key') or os.environ.get('AISSTREAM_API_KEY') or ''; mode=s.get('mode') or MODE
    if mode=='live' and not key: mode='demo'
    return {'api_key':key,'enabled':s.get('enabled',True),'mode':mode,'bboxes':s.get('bboxes') or DEFAULT_BBOXES,'source':'settings' if s.get('api_key') else ('env' if os.environ.get('AISSTREAM_API_KEY') else 'demo-replay'),'updated_at':s.get('updated_at'),'updated_by':s.get('updated_by')}

def status(): return {**{k:v for k,v in state.items() if k!='vessels'},'vessels':len(state['vessels']),'running':bool(_task and not _task.done())}

def _position(mmsi,name,lat,lon,ts,sog=10,cog=90): return AISPositionIn(mmsi=mmsi,vessel_name=name,timestamp=ts,lat=lat,lon=lon,sog_kn=sog,cog_deg=cog,heading_deg=cog,source='demo-replay')

async def _demo_cycle():
    from services import ingest_ais
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0); positions=[]
    vessels=[('211100001','DEMO TANKER 01',56.1,3.2,85),('211100002','DEMO CARGO 02',55.6,4.5,110),('211100003','DEMO TUG 03',57.0,5.4,250)]
    for m,n,lat,lon,cog in vessels:
        for j in range(12):
            ts=now-timedelta(minutes=(11-j)*10); lat2=lat+0.02*j; lon2=lon+0.035*j
            positions.append(_position(m,n,lat2,lon2,ts,9+j%3,cog))
    res=await ingest_ais(positions,source_batch_id=f'demo-ais-{now:%Y%m%dT%H%M}',actor='demo-replay'); state['messages']+=len(positions); state['positions']+=len(positions); state['inserted']+=res['inserted']; state['vessels'].update(p.mmsi for p in positions); state['last_message_at']=now; state['connected_at']=now; state['connected']=True

async def _live_run(cfg):
    from services import ingest_ais
    buffer=[]
    async with websockets.connect(WS_URL,ping_interval=20,close_timeout=5) as ws:
        await ws.send(json.dumps({'APIKey':cfg['api_key'],'BoundingBoxes':cfg['bboxes'],'FilterMessageTypes':['PositionReport']})); state.update({'connected':True,'connected_at':datetime.now(timezone.utc),'error':None})
        while True:
            raw=await asyncio.wait_for(ws.recv(),timeout=15); msg=json.loads(raw); state['messages']+=1
            pr=(msg.get('Message') or {}).get('PositionReport'); md=msg.get('MetaData') or {}
            if not pr: continue
            p=AISPositionIn(mmsi=str(pr.get('UserID') or md.get('MMSI')),vessel_name=(md.get('ShipName') or '').strip() or None,timestamp=datetime.now(timezone.utc),lat=pr['Latitude'],lon=pr['Longitude'],sog_kn=pr.get('Sog'),cog_deg=pr.get('Cog'),heading_deg=pr.get('TrueHeading'),source='aisstream.io'); buffer.append(p); state['positions']+=1; state['vessels'].add(p.mmsi)
            if len(buffer)>=200: res=await ingest_ais(buffer,source_batch_id=f'aisstream-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}',actor='aisstream.io'); state['inserted']+=res['inserted']; buffer=[]

async def _run():
    while True:
        cfg=await get_config(); state['mode']=cfg['mode'];
        if not cfg['enabled']:
            state['connected']=False; await asyncio.sleep(5); continue
        if cfg['mode']=='demo':
            try: await _demo_cycle()
            except Exception as e: state.update({'connected':False,'error':str(e)[:300]})
            await asyncio.sleep(30)
        else:
            try: await _live_run(cfg)
            except Exception as e: state.update({'connected':False,'error':str(e)[:300],'restarts':state['restarts']+1}); await asyncio.sleep(10)
            finally: state['connected']=False

def start():
    global _task
    if _task is None or _task.done(): _task=asyncio.create_task(_run())
