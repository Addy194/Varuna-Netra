from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import get_current_user, require_role
from db import db, clean, audit
from models import SceneCreate
from satellite import COLLECTIONS, STAC, search_scenes, get_item, fetch_preview
from services import create_scene

router = APIRouter()


class SceneSearch(BaseModel):
    bbox: List[float] = Field(min_length=4, max_length=4)
    start: str
    end: str
    collection: str = "sentinel-1-grd"
    limit: int = Field(default=25, ge=1, le=100)
    max_cloud: Optional[int] = Field(default=None, ge=0, le=100)


@router.get("/satellite/collections")
async def collections(user=Depends(get_current_user)):
    return {"collections": [{"id": k, **v} for k, v in COLLECTIONS.items()], "stac": STAC,
            "basemaps": [{"id": "VIIRS_SNPP_CorrectedReflectance_TrueColor", "label": "VIIRS SNPP true colour (daily)", "matrix": "GoogleMapsCompatible_Level9", "ext": "jpg"},
                         {"id": "MODIS_Terra_CorrectedReflectance_TrueColor", "label": "MODIS Terra true colour (daily)", "matrix": "GoogleMapsCompatible_Level9", "ext": "jpg"},
                         {"id": "VIIRS_SNPP_DayNightBand_ENCC", "label": "VIIRS day/night band (night lights)", "matrix": "GoogleMapsCompatible_Level8", "ext": "png"}],
            "gibs_template": "https://gibs-{s}.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{time}/{matrix}/{z}/{y}/{x}.{ext}"}


@router.post("/satellite/search")
async def search(body: SceneSearch, user=Depends(get_current_user)):
    if body.collection not in COLLECTIONS:
        raise HTTPException(400, f"collection must be one of {list(COLLECTIONS)}")
    w, s, e, n = body.bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(400, "bbox must be [west, south, east, north]")
    if (e - w) * (n - s) > 3600:
        raise HTTPException(400, "search area too large — zoom in (keep the box under ~60°×60°)")
    try:
        res = await search_scenes(body.bbox, body.start, body.end, body.collection, body.limit, body.max_cloud)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"STAC search failed: {str(ex)[:200]}")
    registered = {s["provider_scene_id"] for s in await db.scenes.find({"provider_scene_id": {"$in": [x["stac_id"] for x in res["scenes"]]}}, {"provider_scene_id": 1, "id": 1}).to_list(200)}
    ids = {s["provider_scene_id"]: s["id"] for s in await db.scenes.find({"provider_scene_id": {"$in": list(registered)}}, {"provider_scene_id": 1, "id": 1}).to_list(200)}
    for sc in res["scenes"]:
        sc["registered_scene_id"] = ids.get(sc["stac_id"])
    await audit("satellite", body.collection, "satellite.searched", {"bbox": body.bbox, "start": body.start, "end": body.end, "count": res["count"]}, user["email"])
    return res


class RegisterRequest(BaseModel):
    collection: str
    stac_id: str
    detect: bool = False


@router.post("/satellite/register", status_code=201)
async def register(body: RegisterRequest, user=Depends(require_role("analyst"))):
    if body.collection not in COLLECTIONS:
        raise HTTPException(400, "unknown collection")
    existing = await db.scenes.find_one({"provider_scene_id": body.stac_id}, {"_id": 0})
    if existing:
        return clean({"scene": existing, "already_registered": True})
    try:
        it = await get_item(body.collection, body.stac_id)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"STAC item fetch failed: {str(ex)[:200]}")
    payload = SceneCreate(provider=it["provider"], provider_scene_id=it["stac_id"], sensor_mode=it.get("instrument_mode") or it.get("product_type"),
                          polarization="+".join(it["polarizations"]) if it.get("polarizations") else None, acquisition_time=it["datetime"],
                          footprint=it["footprint"], storage_ref=it["stac_href"],
                          metadata={"stac_collection": it["collection"], "platform": it.get("platform"), "orbit_state": it.get("orbit_state"), "relative_orbit": it.get("relative_orbit"), "bbox": it.get("bbox"),
                                    "cloud_cover": it.get("cloud_cover"), "preview_href": it.get("preview_href"), "thumbnail_href": it.get("thumbnail_href"), "vv_href": it.get("vv_href"), "vh_href": it.get("vh_href"), "assets": it.get("assets"), "source": "Microsoft Planetary Computer STAC"})
    try:
        scene = await create_scene(payload, user["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = {"scene": scene, "already_registered": False}
    if body.detect:
        from detector import detect_scene
        try:
            det = await detect_scene(scene, user["email"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"detector failed: {str(e)[:200]}")
        first = det["cases"][0] if det.get("cases") else None
        case = await db.cases.find_one({"id": first["case_id"]}, {"_id": 0}) if first else None
        out.update({"detection": det, "case": case, "detector_note": det["note"]})
    return clean(out)


@router.get("/satellite/preview")
async def preview(collection: str, stac_id: str, user=Depends(get_current_user)):
    if collection not in COLLECTIONS:
        raise HTTPException(400, "unknown collection")
    scene = await db.scenes.find_one({"provider_scene_id": stac_id}, {"_id": 0, "metadata": 1})
    md = (scene or {}).get("metadata", {})
    href, thumb = md.get("preview_href"), md.get("thumbnail_href")
    if not href:
        try:
            it = await get_item(collection, stac_id)
            href, thumb = it.get("preview_href"), it.get("thumbnail_href")
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(502, f"STAC item fetch failed: {str(ex)[:200]}")
    if not href:
        raise HTTPException(404, "no preview available for this scene")
    try:
        data, ct = await fetch_preview(href, thumb)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"preview fetch failed: {str(ex)[:200]}")
    return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
