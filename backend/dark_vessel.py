"""EXPERIMENTAL dark-vessel detection: CFAR-style bright-target search on the Sentinel-1 quicklook, cross-checked against AIS.
Targets without any AIS fix within DARK_RADIUS_KM around the pass time become 'dark vessel candidates' with a dead-reckoned escape trajectory."""
import io
from datetime import datetime, timedelta, timezone
from typing import List

from lazy_libs import cv2
import numpy as np
from PIL import Image
from shapely.geometry import shape

from db import db, audit
from detector import _affine, get_quicklook
from geo import haversine_km, destination, bearing_deg, major_axis_bearing
from models import new_id

DARK_VESSEL_VERSION = "cfar-bright-target-0.1.0-experimental"
DARK_RADIUS_KM, TIME_WINDOW_MIN, ASSUMED_SPEED_KN, MAX_TARGETS = 3.0, 30, 12.0, 40
GUARD, WINDOW, K_SIGMA, MIN_PX, MAX_PX = 3, 15, 4.0, 3, 400


def detect_bright_targets(png: bytes, bbox: list) -> dict:
    """Cell-averaging CFAR: pixel > local mean + K·σ (window minus guard) → compact bright blob."""
    arr = np.array(Image.open(io.BytesIO(png)).convert("RGBA"))
    h, w = arr.shape[:2]
    vv = arr[:, :, 0].astype(np.float32)
    valid = (arr[:, :, 3] > 0) & (arr[:, :, :3].sum(axis=2) > 6)
    big = cv2.boxFilter(vv, -1, (WINDOW, WINDOW), normalize=True)
    big_sq = cv2.boxFilter(vv * vv, -1, (WINDOW, WINDOW), normalize=True)
    small = cv2.boxFilter(vv, -1, (GUARD, GUARD), normalize=True)
    n_big, n_small = WINDOW * WINDOW, GUARD * GUARD
    mean_bg = (big * n_big - small * n_small) / (n_big - n_small)
    var_bg = np.maximum((big_sq * n_big - cv2.boxFilter(vv * vv, -1, (GUARD, GUARD), normalize=True) * n_small) / (n_big - n_small) - mean_bg ** 2, 1.0)
    thr = mean_bg + K_SIGMA * np.sqrt(var_bg)
    mask = ((vv > thr) & (vv > 90) & valid).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    to_geo = _affine(bbox, w, h)
    targets = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < MIN_PX or area > MAX_PX or max(bw, bh) / max(min(bw, bh), 1) > 6:
            continue
        cx, cy = cents[i]
        lon, lat = to_geo(float(cx), float(cy))
        snr = float((vv[labels == i].mean() - mean_bg[int(cy), int(cx)]) / np.sqrt(var_bg[int(cy), int(cx)]))
        w_lon, n_lat = to_geo(float(x), float(y))
        e_lon, s_lat = to_geo(float(x + bw), float(y + bh))
        targets.append({"lat": round(lat, 5), "lon": round(lon, 5), "area_px": int(area), "snr": round(snr, 1), "pixel_bbox": [int(x), int(y), int(bw), int(bh)],
                        "bbox": [round(w_lon, 5), round(s_lat, 5), round(e_lon, 5), round(n_lat, 5)], "est_length_m": int(max(bw, bh) * ((bbox[2] - bbox[0]) * 111000 / w))})
    targets.sort(key=lambda t: -t["snr"])
    return {"targets": targets[:MAX_TARGETS], "total": len(targets), "width": w, "height": h}


def _trajectory(lat: float, lon: float, heading: float, hours=(1, 2, 3, 6)) -> List[dict]:
    return [{"h": h, "lat": round(destination(lat, lon, heading, ASSUMED_SPEED_KN * 1.852 * h)[0], 5), "lon": round(destination(lat, lon, heading, ASSUMED_SPEED_KN * 1.852 * h)[1], 5)} for h in hours]


def _escape_heading(axis: float, c_lat: float, c_lon: float, t: dict) -> float:
    """Along the slick major axis, in the sense pointing away from the spill centroid."""
    toward = abs(((bearing_deg(c_lat, c_lon, t["lat"], t["lon"]) - axis + 180) % 360) - 180) < 90
    return axis if toward else (axis + 180) % 360


def _classify_target(t: dict, fixes: List[dict], axis: float, c_lat: float, c_lon: float) -> dict:
    d, f = min(((haversine_km(t["lat"], t["lon"], x["lat"], x["lon"]), x) for x in fixes), key=lambda p: p[0], default=(None, None))
    dark = d is None or d > DARK_RADIUS_KM
    rec = {"id": new_id(), **t, "distance_to_spill_km": round(haversine_km(t["lat"], t["lon"], c_lat, c_lon), 2), "nearest_ais_km": round(d, 2) if d is not None else None,
           "matched_mmsi": None if dark else f["mmsi"], "matched_name": None if dark else f.get("vessel_name"), "dark_candidate": dark}
    if dark:
        away = _escape_heading(axis, c_lat, c_lon, t)
        rec.update({"escape_heading_deg": round(away, 0), "assumed_speed_kn": ASSUMED_SPEED_KN, "trajectory": _trajectory(t["lat"], t["lon"], away),
                    "trajectory_note": "Dead reckoning along the slick's major axis away from the spill at an assumed 12 kn — a search cue, not a track."})
    return rec


async def _scene_with_imagery(case: dict) -> dict:
    scene = await db.scenes.find_one({"id": case.get("scene_id")}, {"_id": 0}) if case.get("scene_id") else None
    if not scene or not (scene.get("quicklook_path") or (scene.get("metadata") or {}).get("preview_href") or scene.get("stac_href")):
        raise ValueError("no Sentinel-1 quicklook is attached to this case's scene — dark-vessel scan needs real SAR imagery")
    return scene


async def _ais_around(t0: datetime, c_lat: float, c_lon: float, radius_km: float) -> List[dict]:
    q = {"timestamp": {"$gte": t0 - timedelta(minutes=TIME_WINDOW_MIN), "$lte": t0 + timedelta(minutes=TIME_WINDOW_MIN)},
         "location": {"$geoWithin": {"$centerSphere": [[c_lon, c_lat], (radius_km + DARK_RADIUS_KM) / 6371.0088]}}}
    return await db.ais_positions.find(q, {"_id": 0, "mmsi": 1, "vessel_name": 1, "lat": 1, "lon": 1, "timestamp": 1}).to_list(20000)


async def scan_case(case_id: str, actor: str = "system", radius_km: float = 40.0) -> dict:
    case = await db.cases.find_one({"id": case_id}, {"_id": 0})
    if not case:
        raise ValueError("case not found")
    scene = await _scene_with_imagery(case)
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0})
    t0 = spill["acquisition_time"].replace(tzinfo=timezone.utc) if spill["acquisition_time"].tzinfo is None else spill["acquisition_time"]
    c_lon, c_lat = spill["centroid"]["coordinates"]
    bbox = (scene.get("metadata") or {}).get("bbox") or list(shape(scene["footprint"]).bounds)
    det = detect_bright_targets(await get_quicklook(scene), bbox)
    near = [t for t in det["targets"] if haversine_km(t["lat"], t["lon"], c_lat, c_lon) <= radius_km]
    fixes = await _ais_around(t0, c_lat, c_lon, radius_km)
    axis = major_axis_bearing(shape(spill["geometry"]))
    out = [_classify_target(t, fixes, axis, c_lat, c_lon) for t in near]
    now = datetime.now(timezone.utc)
    scan = {"id": new_id(), "case_id": case_id, "scene_id": scene["id"], "version": DARK_VESSEL_VERSION, "acquisition_time": t0, "radius_km": radius_km, "dark_radius_km": DARK_RADIUS_KM,
            "time_window_min": TIME_WINDOW_MIN, "targets": out, "bright_targets_total": det["total"], "ais_fixes_checked": len(fixes), "dark_count": sum(1 for r in out if r["dark_candidate"]),
            "actor": actor, "created_at": now, "experimental": True,
            "disclaimer": "EXPERIMENTAL CFAR bright-target heuristic on a rendered quicklook (not full-resolution SAR, no ML). Platforms, buoys, islands, azimuth ambiguities and ship wakes cause false alarms; AIS gaps ≠ intent. Requires analyst review."}
    await db.dark_vessel_scans.insert_one(dict(scan))
    await db.cases.update_one({"id": case_id}, {"$set": {"dark_vessels": {"scan_id": scan["id"], "dark_count": scan["dark_count"], "targets": len(out), "at": now}}})
    await audit("case", case_id, "dark_vessel.scanned", {"dark_count": scan["dark_count"], "targets": len(out), "bright_total": det["total"], "version": DARK_VESSEL_VERSION}, actor)
    scan.pop("_id", None)
    return scan
