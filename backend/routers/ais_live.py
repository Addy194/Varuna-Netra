from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import ais_live
from auth import get_current_user, require_role
from db import db, clean, audit

router = APIRouter()


class Coverage(BaseModel):
    south: float
    west: float
    north: float
    east: float
    name: Optional[str] = None


@router.get("/ais/live/status")
@router.get("/ais/status")
async def live_status(user=Depends(get_current_user)):
    cov = await ais_live.get_coverage()
    st = ais_live.status()
    return clean({**st, "coverage_mode": cov["mode"], "coverage_name": cov["name"], "coverage_bbox": cov["bboxes"], "coverage_bbox_format": "[S,W,N,E]",
                  "aisstream_bounding_boxes": ais_live.to_aisstream_boxes(cov["bboxes"]), "coverage_ref": cov.get("ref"), "regions": ais_live.REGIONS,
                  "note": None if st["connected"] else "Satellite analysis still operational; vessel attribution unavailable until AIS coverage is restored."})


@router.post("/ais/coverage")
async def set_manual_coverage(body: Coverage, user=Depends(require_role("supervisor"))):
    try:
        bbox = ais_live.validate_bbox(body.south, body.west, body.north, body.east)
    except ValueError as e:
        raise HTTPException(400, str(e))
    doc = await ais_live.set_coverage([bbox], "manual", body.name or "custom AOI", user["email"])
    await audit("settings", "ais_coverage", "ais.coverage_set", {"mode": "manual", "bbox": bbox}, user["email"])
    return clean(doc)


@router.post("/ais/coverage/region/{region}")
async def set_region_coverage(region: str, user=Depends(require_role("supervisor"))):
    if region == "default":
        await db.settings.delete_one({"key": "ais_coverage"})
        ais_live._reconnect_event.set()
        return clean(await ais_live.get_coverage())
    r = ais_live.REGIONS.get(region)
    if not r:
        raise HTTPException(404, f"unknown region; choose one of {list(ais_live.REGIONS)} or default")
    doc = await ais_live.set_coverage([r["bbox"]], "manual", r["name"], user["email"], region)
    await audit("settings", "ais_coverage", "ais.coverage_set", {"mode": "region", "region": region}, user["email"])
    return clean(doc)


@router.get("/ais/vessels")
async def vessels(user=Depends(get_current_user)):
    """Canonical AIS vessel endpoint. `vessels` = live AISStream active cache (genuine messages only);
    `indexed` = per-MMSI summary of stored ais_positions history (AISStream + CSV/batch uploads, each with its source)."""
    st = ais_live.status()
    live = sorted(ais_live.state["active"].values(), key=lambda v: v["received_at"], reverse=True)
    pipeline = [
        {"$match": {"timestamp": {"$gte": datetime.now(timezone.utc) - timedelta(days=90)}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$mmsi", "vessel_name": {"$first": "$vessel_name"}, "imo": {"$first": "$imo"}, "vessel_type": {"$first": "$vessel_type"},
                    "fixes": {"$sum": 1}, "first_seen": {"$min": "$timestamp"}, "last_seen": {"$max": "$timestamp"},
                    "sources": {"$addToSet": "$source"}, "flags": {"$addToSet": "$quality_flags"}}},
        {"$sort": {"last_seen": -1}},
    ]
    rows = await db.ais_positions.aggregate(pipeline).to_list(1000)
    indexed = [{"mmsi": r["_id"], "vessel_name": r["vessel_name"], "imo": r["imo"], "vessel_type": r["vessel_type"], "fixes": r["fixes"],
                "first_seen": r["first_seen"], "last_seen": r["last_seen"], "sources": sorted(s for s in r["sources"] if s),
                "quality_flags": sorted({f for fl in r["flags"] for f in (fl or [])})} for r in rows]
    return clean({"source": ais_live.SOURCE, "mode": "live", "state": st["state"], "configured": st["configured"], "connected": st["connected"],
                  "count": len(live), "stale_after_min": ais_live.STALE_MIN, "vessels": live, "indexed_window_days": 90, "indexed_count": len(indexed), "indexed": indexed})


@router.get("/ais/tracks/{mmsi}")
async def track(mmsi: str, hours: int = Query(24, ge=1, le=168), user=Depends(get_current_user)):
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = await db.ais_positions.find({"mmsi": mmsi, "timestamp": {"$gte": since}}, {"_id": 0, "location": 0, "dedup_hash": 0}).sort("timestamp", 1).to_list(5000)
    return clean({"mmsi": mmsi, "count": len(rows), "positions": rows})


@router.post("/ais/test-connection")
async def test_connection(user=Depends(require_role("admin"))):
    return clean(await ais_live.test_connection())


@router.get("/ais/debug/aoi")
async def debug_aoi(user=Depends(require_role("supervisor"))):
    cov = await ais_live.get_coverage()
    scene = await db.scenes.find_one({"provider_scene_id": {"$regex": "^S1"}}, {"_id": 0, "provider_scene_id": 1, "metadata.bbox": 1}, sort=[("created_at", -1)])
    return clean({"investigation_aoi": cov, "aisstream_bounding_boxes": ais_live.to_aisstream_boxes(cov["bboxes"]), "latest_sentinel_scene": scene})
