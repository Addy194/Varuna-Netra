from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from auth import get_current_user, require_role
from db import db, clean, audit, to_utc
from geo import circle_polygon
from models import CorrelateRequest, ReviewCreate, OverrideRequest, REASON_CODES, new_id
from jobs import enqueue, process
from services import apply_live_environment
from storage import get_object

router = APIRouter()


async def _case(case_id):
    case = await db.cases.find_one({"id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(404, "case not found")
    return case


async def _result(case_id, version: Optional[int]):
    q = {"case_id": case_id}
    if version:
        q["version"] = version
    return await db.correlation_results.find_one(q, {"_id": 0}, sort=[("version", -1)])


@router.get("/cases")
async def list_cases(status: Optional[str] = None, attribution_status: Optional[str] = None, limit: int = Query(200, le=1000), user=Depends(get_current_user)):
    q = {}
    if status:
        q["status"] = status
    if attribution_status:
        q["attribution_status"] = attribution_status
    return clean(await db.cases.find(q, {"_id": 0}).sort("acquisition_time", -1).to_list(limit))


@router.get("/cases/{case_id}")
async def get_case(case_id: str, user=Depends(get_current_user)):
    case = await _case(case_id)
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "raw_input": 0})
    scene = await db.scenes.find_one({"id": case["scene_id"]}, {"_id": 0}) if case.get("scene_id") else None
    versions = await db.correlation_results.find({"case_id": case_id}, {"_id": 0, "version": 1, "created_at": 1, "overall_status": 1, "algorithm_version": 1, "input_hash": 1, "degraded": 1}).sort("version", 1).to_list(100)
    return clean({**case, "spill_observation": spill, "scene": scene, "result_versions": versions})


@router.post("/cases/{case_id}/correlate", status_code=202)
async def correlate_case(case_id: str, body: CorrelateRequest = CorrelateRequest(), user=Depends(require_role("analyst"))):
    await _case(case_id)
    payload = {"case_id": case_id, "params": body.params.model_dump() if body.params else None, "fetch_environment": body.fetch_environment}
    job = await enqueue("correlate", payload, user["email"], inline=body.sync)
    await audit("case", case_id, "case.correlation_requested", {"job_id": job["id"], "sync": body.sync, "fetch_environment": body.fetch_environment}, user["email"])
    if body.sync:
        job = await process(job["id"])
    return clean(job)


@router.post("/cases/{case_id}/environment/fetch")
async def fetch_case_environment(case_id: str, user=Depends(require_role("analyst"))):
    case = await _case(case_id)
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "raw_input": 0})
    env = await apply_live_environment(spill, user["email"])
    return clean({"environment": env, "wind": spill.get("wind"), "current": spill.get("current")})


@router.get("/cases/{case_id}/candidates")
async def get_candidates(case_id: str, version: Optional[int] = None, include_tracks: bool = False, user=Depends(get_current_user)):
    case = await _case(case_id)
    result = await _result(case_id, version)
    if not result:
        return {"case_id": case_id, "version": 0, "candidates": [], "overall_status": case["attribution_status"], "message": "no correlation run yet"}
    if not include_tracks:
        for c in result["candidates"]:
            c.pop("track", None)
    mmsis = [c["mmsi"] for c in result["candidates"]]
    watch = {w["mmsi"]: w for w in await db.watchlist.find({"active": True, "mmsi": {"$in": mmsis}}, {"_id": 0, "mmsi": 1, "reason": 1, "severity": 1, "id": 1}).to_list(len(mmsis) or 1)}
    for c in result["candidates"]:
        c["watchlist"] = watch.get(c["mmsi"])
    result.pop("processing_log", None)
    return clean({"case_id": case_id, **result, "attribution_status": case["attribution_status"], "review_state": case["review_state"],
                  "disclaimer": "Ranked candidates are decision-support output from AIS/satellite correlation, not a legal determination of responsibility."})


@router.post("/cases/{case_id}/review", status_code=201)
async def review_case(case_id: str, body: ReviewCreate, user=Depends(require_role("analyst"))):
    case = await _case(case_id)
    bad = [c for c in body.reason_codes if c not in REASON_CODES]
    if bad:
        raise HTTPException(400, f"unknown reason codes: {bad}")
    result = await _result(case_id, body.result_version)
    if body.decision == "confirm":
        if not body.vessel_mmsi:
            raise HTTPException(400, "vessel_mmsi is required to confirm")
        if not result or body.vessel_mmsi not in {c["mmsi"] for c in result["candidates"]}:
            raise HTTPException(400, "vessel_mmsi must be a ranked candidate in the referenced result version")
    now = datetime.now(timezone.utc)
    review = {"id": new_id(), "case_id": case_id, "result_version": result["version"] if result else None, "decision": body.decision,
              "vessel_mmsi": body.vessel_mmsi, "reason_codes": body.reason_codes, "notes": body.notes,
              "analyst": user["email"], "analyst_name": user.get("name"), "analyst_role": user["role"], "analyst_id": user["id"],
              "previous_attribution_status": case["attribution_status"], "created_at": now}
    await db.reviews.insert_one(dict(review))
    update = {"updated_at": now}
    if body.decision == "confirm":
        update.update({"attribution_status": "analyst_confirmed", "review_state": "confirmed", "confirmed_vessel_mmsi": body.vessel_mmsi, "status": "closed"})
    elif body.decision == "reject":
        update.update({"attribution_status": "insufficient_evidence", "review_state": "rejected", "confirmed_vessel_mmsi": None, "status": "closed"})
    else:
        update.update({"review_state": "needs_more_data", "status": "under_review"})
    await db.cases.update_one({"id": case_id}, {"$set": update})
    await audit("case", case_id, f"review.{body.decision}", {"review_id": review["id"], "vessel_mmsi": body.vessel_mmsi, "reason_codes": body.reason_codes,
                                                          "result_version": review["result_version"], "from": case["attribution_status"],
                                                          "to": update.get("attribution_status", case["attribution_status"]), "role": user["role"]}, user["email"])
    review.pop("_id", None)
    return clean(review)


@router.post("/cases/{case_id}/override", status_code=201)
async def override_case(case_id: str, body: OverrideRequest, user=Depends(require_role("supervisor"))):
    case = await _case(case_id)
    now = datetime.now(timezone.utc)
    result = await _result(case_id, None)
    review = {"id": new_id(), "case_id": case_id, "result_version": result["version"] if result else None, "decision": "supervisor_override",
              "vessel_mmsi": body.vessel_mmsi, "reason_codes": [], "notes": body.notes, "analyst": user["email"], "analyst_name": user.get("name"),
              "analyst_role": user["role"], "analyst_id": user["id"], "previous_attribution_status": case["attribution_status"],
              "override_to": body.attribution_status, "created_at": now}
    await db.reviews.insert_one(dict(review))
    update = {"attribution_status": body.attribution_status, "review_state": "supervisor_override", "updated_at": now,
              "confirmed_vessel_mmsi": body.vessel_mmsi if body.attribution_status == "analyst_confirmed" else case.get("confirmed_vessel_mmsi")}
    if body.close_case:
        update["status"] = "closed"
    await db.cases.update_one({"id": case_id}, {"$set": update})
    await audit("case", case_id, "review.supervisor_override", {"review_id": review["id"], "from": case["attribution_status"], "to": body.attribution_status,
                                                              "vessel_mmsi": body.vessel_mmsi, "closed": body.close_case, "role": user["role"]}, user["email"])
    review.pop("_id", None)
    return clean(review)


@router.get("/cases/{case_id}/reviews")
async def list_reviews(case_id: str, user=Depends(get_current_user)):
    await _case(case_id)
    return clean(await db.reviews.find({"case_id": case_id}, {"_id": 0}).sort("created_at", 1).to_list(500))


def _geojson(case, spill, result):
    features = [{"type": "Feature", "geometry": spill["geometry"], "properties": {"layer": "spill", "id": spill["id"], "case_number": case["case_number"],
                 "acquisition_time": spill["acquisition_time"], "detection_confidence": spill["detection_confidence"], "quality_flags": spill["quality_flags"],
                 "estimated_area_km2": spill["estimated_area_km2"], "source": spill["source"]}}]
    if result:
        lon, lat = spill["centroid"]["coordinates"]
        features.append({"type": "Feature", "geometry": circle_polygon(lat, lon, result["params"]["corridor_km"] + spill.get("extent_km", 0)),
                         "properties": {"layer": "corridor", "radius_km": result["params"]["corridor_km"] + spill.get("extent_km", 0)}})
        dm = result.get("drift_model")
        if dm:
            features.append({"type": "Feature", "geometry": dm["envelope"], "properties": {"layer": "drift_envelope", "hours": dm["hours"], "k_sigma": dm["k_sigma"], "version": dm["version"]}})
            features.append({"type": "Feature", "geometry": dm["likely_envelope"], "properties": {"layer": "drift_likely", "window_hours": dm["likely_window_hours"]}})
            features.append({"type": "Feature", "geometry": dm["path"], "properties": {"layer": "drift_path", "hours": dm["path_hours"], "sigma_km": dm["sigma_km"], "speed_ms": dm["drift_speed_ms"]}})
        for c in result["candidates"]:
            props = {"layer": "track", "mmsi": c["mmsi"], "vessel_name": c.get("vessel_name"), "rank": c["rank"], "score": c["score"], "status": c["status"]}
            track = c.get("track", [])
            if len(track) >= 2:
                # split into solid (real) and dashed (interpolated) segments
                segs, cur, cur_interp = [], [track[0]], bool(track[0].get("interpolated"))
                for p in track[1:]:
                    interp = bool(p.get("interpolated"))
                    if interp != cur_interp:
                        cur.append(p)
                        segs.append((cur_interp, cur))
                        cur, cur_interp = [p], interp
                    else:
                        cur.append(p)
                segs.append((cur_interp, cur))
                for i, (interp, pts) in enumerate(segs):
                    if len(pts) < 2:
                        continue
                    features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in pts]},
                                     "properties": {**props, "segment": i, "interpolated": interp, "timestamps": [p["timestamp"] for p in pts], "sog": [p.get("sog_kn") for p in pts], "cog": [p.get("cog_deg") for p in pts]}})
            cf = c["evidence"]["closest_fix"]
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [cf["lon"], cf["lat"]]},
                             "properties": {**props, "layer": "closest_fix", "timestamp": cf["timestamp"], "distance_km": c["evidence"]["distance_km"],
                                            "time_gap_hours": c["evidence"]["time_gap_hours"], "sog_kn": cf.get("sog_kn"), "cog_deg": cf.get("cog_deg")}})
            if c["evidence"].get("backprojected_centroid"):
                features.append({"type": "Feature", "geometry": c["evidence"]["backprojected_centroid"],
                                 "properties": {**props, "layer": "backprojected_centroid"}})
    return {"type": "FeatureCollection", "features": features}


@router.get("/cases/compare/{a}/{b}")
async def compare_cases(a: str, b: str, user=Depends(get_current_user)):
    sides = {}
    for key, cid in (("a", a), ("b", b)):
        case = await _case(cid)
        spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "raw_input": 0})
        result = await _result(cid, None)
        cands = [{k: v for k, v in c.items() if k != "track"} for c in (result or {}).get("candidates", [])]
        sides[key] = {"case": case, "geojson": _geojson(case, spill, result), "candidates": cands, "version": (result or {}).get("version", 0)}
    ma = {c["mmsi"]: c for c in sides["a"]["candidates"]}
    mb = {c["mmsi"]: c for c in sides["b"]["candidates"]}
    shared = [{"mmsi": m, "vessel_name": ma[m].get("vessel_name") or mb[m].get("vessel_name"), "vessel_type": ma[m].get("vessel_type"),
               "a": {"rank": ma[m]["rank"], "score": ma[m]["score"], "status": ma[m]["status"]}, "b": {"rank": mb[m]["rank"], "score": mb[m]["score"], "status": mb[m]["status"]}}
              for m in ma if m in mb]
    shared.sort(key=lambda s: s["a"]["rank"] + s["b"]["rank"])
    return clean({**sides, "shared_vessels": shared, "disclaimer": "Repeat appearance across cases is an investigative lead, not evidence of responsibility."})


@router.get("/cases/{case_id}/geojson")
async def case_geojson(case_id: str, version: Optional[int] = None, user=Depends(get_current_user)):
    case = await _case(case_id)
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "raw_input": 0})
    result = await _result(case_id, version)
    return _geojson(case, spill, result)


@router.get("/cases/{case_id}/evidence")
async def evidence_bundle(case_id: str, user=Depends(get_current_user)):
    case = await _case(case_id)
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "raw_input": 0})
    result = await _result(case_id, None)
    reviews = await db.reviews.find({"case_id": case_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
    bundle = {"case": case, "spill_observation": spill, "correlation_result": result, "reviews": reviews}
    try:
        bundle["playbook"] = await db.playbooks.find_one({"case_id": case_id}, {"_id": 0})
    except Exception:  # noqa: BLE001
        bundle["playbook"] = None
    try:
        from vulnerability import assess
        bundle["vulnerability"] = await assess(case_id)
    except Exception:  # noqa: BLE001
        bundle["vulnerability"] = None
    return clean(bundle)


@router.get("/cases/{case_id}/evidence.pdf")
async def evidence_pdf(case_id: str, user=Depends(get_current_user)):
    bundle = await evidence_bundle(case_id, user)
    # Keep the PDF stack out of normal API startup. This import is intentionally lazy.
    from report import build_pdf
    pdf = build_pdf(bundle)
    await audit("case", case_id, "evidence.exported", {"format": "pdf", "version": bundle["case"].get("latest_result_version"), "bytes": len(pdf)}, user["email"])
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{bundle["case"]["case_number"]}-evidence.pdf"'})
