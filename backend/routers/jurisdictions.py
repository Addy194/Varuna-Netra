from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import get_current_user, require_role
from db import db, clean, audit
from geo import validate_polygon
from jurisdiction import ZONE_TYPES, apply_to_case
from models import GeoJSONGeometry, new_id

router = APIRouter()


class ZoneCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2)
    authority: str = Field(min_length=2)
    country: Optional[str] = None
    zone_type: str = "eez"
    geometry: GeoJSONGeometry
    contact: Optional[str] = None
    active: bool = True


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    country: Optional[str] = None
    zone_type: Optional[str] = None
    geometry: Optional[GeoJSONGeometry] = None
    contact: Optional[str] = None
    active: Optional[bool] = None


@router.get("/jurisdictions")
async def list_zones(user=Depends(get_current_user)):
    return clean(await db.jurisdictions.find({}, {"_id": 0}).sort("code", 1).to_list(500))


@router.get("/jurisdictions/geojson")
async def zones_geojson(user=Depends(get_current_user)):
    zones = await db.jurisdictions.find({"active": True}, {"_id": 0}).to_list(500)
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": z["geometry"],
            "properties": {k: z[k] for k in ("id", "code", "name", "authority", "country", "zone_type") if k in z}} for z in zones]}


@router.post("/jurisdictions", status_code=201)
async def create_zone(body: ZoneCreate, user=Depends(require_role("admin"))):
    if body.zone_type not in ZONE_TYPES:
        raise HTTPException(400, f"zone_type must be one of {ZONE_TYPES}")
    try:
        validate_polygon(body.geometry.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if await db.jurisdictions.find_one({"code": body.code}):
        raise HTTPException(400, "zone code already exists")
    now = datetime.now(timezone.utc)
    doc = {**body.model_dump(), "id": new_id(), "source": f"uploaded by {user['email']}", "created_at": now, "updated_at": now}
    await db.jurisdictions.insert_one(dict(doc))
    await audit("jurisdiction", doc["id"], "jurisdiction.created", {"code": body.code, "zone_type": body.zone_type}, user["email"])
    return clean(doc)


@router.put("/jurisdictions/{zone_id}")
async def update_zone(zone_id: str, body: ZoneUpdate, user=Depends(require_role("admin"))):
    update = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "zone_type" in update and update["zone_type"] not in ZONE_TYPES:
        raise HTTPException(400, f"zone_type must be one of {ZONE_TYPES}")
    if "geometry" in update:
        try:
            validate_polygon(update["geometry"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    update["updated_at"] = datetime.now(timezone.utc)
    res = await db.jurisdictions.find_one_and_update({"id": zone_id}, {"$set": update}, projection={"_id": 0}, return_document=True)
    if not res:
        raise HTTPException(404, "zone not found")
    await audit("jurisdiction", zone_id, "jurisdiction.updated", {k: (v if k != "geometry" else "geometry") for k, v in update.items() if k != "updated_at"}, user["email"])
    return clean(res)


@router.delete("/jurisdictions/{zone_id}")
async def delete_zone(zone_id: str, user=Depends(require_role("admin"))):
    res = await db.jurisdictions.delete_one({"id": zone_id})
    if not res.deleted_count:
        raise HTTPException(404, "zone not found")
    await audit("jurisdiction", zone_id, "jurisdiction.deleted", {}, user["email"])
    return {"ok": True}


@router.post("/jurisdictions/resolve-all")
async def resolve_all(user=Depends(require_role("admin"))):
    ids = [c["id"] async for c in db.cases.find({}, {"id": 1})]
    summary = {}
    for cid in ids:
        _, primary = await apply_to_case(cid, user["email"])
        summary[primary["code"] if primary else "unassigned"] = summary.get(primary["code"] if primary else "unassigned", 0) + 1
    return {"cases": len(ids), "by_primary": summary}


@router.post("/cases/{case_id}/jurisdiction/resolve")
async def resolve_case(case_id: str, user=Depends(require_role("analyst"))):
    if not await db.cases.find_one({"id": case_id}):
        raise HTTPException(404, "case not found")
    zones, primary = await apply_to_case(case_id, user["email"])
    return clean({"jurisdictions": zones, "primary_jurisdiction": primary})
