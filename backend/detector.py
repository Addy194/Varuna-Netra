import hashlib, io, json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, shape

from db import db, audit
from models import SpillObservationCreate
from satellite import fetch_preview, fetch_asset
from storage import put_object, APP_NAME

DETECTOR_VERSION = "sar-ml-pixel-1.0.0"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "sar_spill_pixel_v1.json"
MIN_AREA_PX, MAX_AREA_FRAC, MIN_ELONGATION, MAX_SPOTS = 40, 0.02, 1.3, 5
_MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def model_info():
    tr=_MODEL.get("training",{})
    return {"detector_version":DETECTOR_VERSION,"model_id":_MODEL["model_id"],"model_type":_MODEL["type"],"features":_MODEL["feature_names"],"validation":tr.get("validation"),"holdout_metrics":tr.get("holdout_metrics"),"operational_validation_required":True}


def _affine(bbox,w,h):
    west,south,east,north=bbox
    return lambda x,y:(west+(x/w)*(east-west), north-(y/h)*(north-south))


def _features(img):
    x=img.astype(np.float32)/255.0
    local=cv2.GaussianBlur(x,(0,0),2.0)
    gx=cv2.Sobel(x,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(x,cv2.CV_32F,0,1,ksize=3)
    grad=np.sqrt(gx*gx+gy*gy); smooth=cv2.GaussianBlur(grad,(0,0),1.2)
    coh=np.maximum(cv2.GaussianBlur(x*x,(0,0),2)-local*local,0)
    return np.stack([1.0-x,np.abs(x-local),smooth,grad,coh],axis=-1)


def _predict(img):
    f=_features(img)
    mu=np.asarray(_MODEL["mean"],np.float32); sd=np.asarray(_MODEL["scale"],np.float32)
    w=np.asarray(_MODEL["weights"],np.float32); b=float(_MODEL["bias"])
    z=b+((f-mu)/sd)@w
    return 1/(1+np.exp(-np.clip(z,-30,30)))


def _synthetic_demo(seed_text):
    digest=hashlib.sha256(seed_text.encode()).digest(); seed=int.from_bytes(digest[:8],'big') & 0xffffffff
    rng=np.random.default_rng(seed); h=w=512
    base=np.clip(rng.normal(142,28,(h,w)),20,230).astype(np.float32)
    yy,xx=np.mgrid[:h,:w]
    for _ in range(3):
        cx,cy=rng.uniform(80,w-80),rng.uniform(80,h-80); ang=rng.uniform(0,np.pi); major=rng.uniform(45,105); minor=rng.uniform(10,28)
        xr=(xx-cx)*np.cos(ang)+(yy-cy)*np.sin(ang); yr=-(xx-cx)*np.sin(ang)+(yy-cy)*np.cos(ang)
        mask=np.exp(-0.5*((xr/major)**2+(yr/minor)**2)); base-=mask*rng.uniform(35,65)
    base += cv2.GaussianBlur(rng.normal(0,14,(h,w)).astype(np.float32),(0,0),5)
    base=np.clip(base,0,255).astype(np.uint8)
    out=io.BytesIO(); Image.fromarray(base,'L').save(out,'PNG'); return out.getvalue()


def _decode_bytes(data:bytes):
    try:
        with Image.open(io.BytesIO(data)) as im:
            return np.array(im.convert('L'))
    except Exception:
        return None


def _asset_to_gray(data:bytes):
    # Rasterio is optional. PIL handles many GeoTIFF quicklook-like assets.
    arr=_decode_bytes(data)
    if arr is not None: return arr
    try:
        import rasterio
        with rasterio.MemoryFile(data) as mf:
            with mf.open() as ds: arr=ds.read(1, out_shape=(1,min(ds.height,2048),min(ds.width,2048)))
        arr=np.nan_to_num(arr,nan=0,posinf=0,neginf=0).astype(np.float32)
        lo,hi=np.percentile(arr,[2,98]); return np.clip((arr-lo)*255/max(hi-lo,1e-6),0,255).astype(np.uint8)
    except Exception: return None


async def _imagery(scene):
    md=scene.get('metadata') or {}
    # Prefer real SAR assets when registered by STAC.
    for key in ('vv_href','vh_href'):
        href=md.get(key)
        if href:
            try:
                data, _ = await fetch_asset(href)
                arr=_asset_to_gray(data)
                if arr is not None and arr.size>1000: return arr, False, key
            except Exception:
                pass
    href=md.get('preview_href')
    if href:
        png,_=await fetch_preview(href,md.get('thumbnail_href')); arr=_asset_to_gray(png)
        if arr is not None: return arr, False, 'rendered_preview'
    arr=_asset_to_gray(_synthetic_demo(scene.get('id') or scene.get('provider_scene_id') or 'demo'))
    return arr, True, 'synthetic_demo'


def _contours(prob, img, bbox, footprint):
    prob=cv2.GaussianBlur(prob,(0,0),2.0)
    valid=(prob>=0.40).astype(np.uint8)*255
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)); mask=cv2.morphologyEx(valid,cv2.MORPH_OPEN,k); mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,k,iterations=2)
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    h,w=img.shape; to_geo,fp=_affine(bbox,w,h),shape(footprint); spots=[]
    for c in contours:
        area=cv2.contourArea(c)
        if area<MIN_AREA_PX or area>MAX_AREA_FRAC*w*h or len(c)<5: continue
        (cx,cy),(ma,mi),ang=cv2.fitEllipse(c); elong=max(ma,mi)/max(min(ma,mi),1e-3)
        if elong<MIN_ELONGATION: continue
        pix=np.zeros_like(mask); cv2.drawContours(pix,[c],-1,255,-1); vals=prob[pix>0]
        score=float(np.percentile(vals,75)); darkness=float(1-np.mean(img[pix>0])/255.0)
        pts=cv2.approxPolyDP(c,2.0,True).reshape(-1,2); ring=[list(to_geo(float(x),float(y))) for x,y in pts]; ring.append(ring[0]); poly=Polygon(ring)
        if not poly.is_valid or poly.is_empty or not poly.intersects(fp): continue
        spots.append({"geometry":{"type":"Polygon","coordinates":[[[round(x,5),round(y,5)] for x,y in ring]]},"area_px":float(area),"elongation":round(float(elong),2),"model_score":round(score,3),"darkness":round(darkness,3),"confidence":round(min(0.99,0.45+0.6*(score-0.5)),3),"pixel_bbox":[int(v) for v in cv2.boundingRect(c)],"centroid_px":[float(cx),float(cy)],"angle_deg":round(float(ang),1)})
    spots.sort(key=lambda s:-s['confidence']); return spots[:MAX_SPOTS], mask


def analyze_array(img,bbox,footprint,synthetic=False,source='sar'):
    if img is None or img.size<1000: return {"spots":[],"note":"insufficient valid pixels","synthetic":synthetic,"input_source":source}
    prob=_predict(img); spots,_=_contours(prob,img,bbox,footprint)
    note='Bundled ML demo model; real Sentinel-1 labelled validation required before operational use.'
    return {"spots":spots,"width":int(img.shape[1]),"height":int(img.shape[0]),"candidates_total":len(spots),"synthetic":synthetic,"input_source":source,"model":model_info(),"note":note}


def thumbnail_webp(png_or_arr: bytes|np.ndarray, pixel_bbox:list, pad:int=40)->bytes:
    if isinstance(png_or_arr,np.ndarray): img=Image.fromarray(png_or_arr).convert('RGB')
    else: img=Image.open(io.BytesIO(png_or_arr)).convert('RGB')
    x,y,bw,bh=pixel_bbox; box=(max(0,x-pad),max(0,y-pad),min(img.width,x+bw+pad),min(img.height,y+bh+pad)); crop=img.crop(box); crop.thumbnail((480,480)); out=io.BytesIO(); crop.save(out,'WEBP',quality=70); return out.getvalue()


async def get_quicklook(scene):
    from storage import get_object
    if scene.get('quicklook_path'):
        try: data,_=await get_object(scene['quicklook_path']); return data
        except Exception: pass
    md=scene.get('metadata') or {}
    if md.get('preview_href'):
        png,_=await fetch_preview(md['preview_href'],md.get('thumbnail_href'))
    else: png=_synthetic_demo(scene.get('id') or scene.get('provider_scene_id') or 'demo')
    path=f'{APP_NAME}/quicklooks/{scene["provider_scene_id"]}.png'
    try:
        res=await put_object(path,png,'image/png'); await db.scenes.update_one({'id':scene['id']},{'$set':{'quicklook_path':res['path'],'quicklook_bytes':len(png)}})
    except Exception: pass
    return png


async def detect_scene(scene, actor='system'):
    from services import create_spill_observation
    bbox=(scene.get('metadata') or {}).get('bbox') or list(shape(scene['footprint']).bounds)
    arr,synthetic,source=await _imagery(scene); res=analyze_array(arr,bbox,scene['footprint'],synthetic,source)
    cases=[]; now=datetime.now(timezone.utc)
    for s in res['spots']:
        conf=s['confidence']; flags=['ml_candidate','analyst_review_required']
        if conf<0.68: flags.append('low_model_confidence')
        if s['darkness']<0.22: flags.append('weak_backscatter_contrast')
        if synthetic: flags.append('synthetic_demo_input')
        payload=SpillObservationCreate(scene_id=scene['id'],geometry=s['geometry'],acquisition_time=scene['acquisition_time'],source='sar_ml_detector',detection_confidence=conf,quality_flags=flags,processing_version=DETECTOR_VERSION,notes='ML SAR candidate detection. Analyst review required. Bundled model is synthetic-demo-trained and not field-validated.')
        spill,case=await create_spill_observation(payload,actor); case['detector_model']=model_info(); case['synthetic_input']=synthetic; cases.append(case)
        try:
            thumb=thumbnail_webp(arr,s['pixel_bbox']); from db import db as _db
            aid=f'{spill["id"]}-detector'; await _db.attachments.insert_one({'id':aid,'case_id':case['id'],'filename':f'{case["case_number"]}-candidate.webp','content_type':'image/webp','size':len(thumb),'storage_path':f'{APP_NAME}/attachments/{aid}.webp','created_at':now,'is_deleted':False}); await put_object(f'{APP_NAME}/attachments/{aid}.webp',thumb,'image/webp'); await _db.cases.update_one({'id':case['id']},{'$set':{'thumbnail_attachment_id':aid}})
        except Exception: pass
    await db.scenes.update_one({'id':scene['id']},{'$set':{'status':'detected','detector_version':DETECTOR_VERSION,'detector_summary':{k:v for k,v in res.items() if k!='spots'}|{'spots':len(res['spots']),'at':now}}})
    await audit('scene',scene['id'],'scene.detected',{'detector':DETECTOR_VERSION,'model':_MODEL['model_id'],'spots':len(res['spots']),'cases':[c['case_number'] for c in cases],'synthetic_input':synthetic},actor)
    return {'detector':DETECTOR_VERSION,'model':model_info(),'operational_validation_required':True,'synthetic_input':synthetic,'spots':len(res['spots']),'cases':cases,'input_source':source,'note':res.get('note')}
