"""Sentinel-1 asset resolution: QUICKLOOK (visualization) vs SAR ASSET (analysis input).
Stable identifiers only are persisted (scene_id, collection, asset_key); signed URLs are resolved just-in-time and never stored."""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from db import db, audit, to_utc
from satellite import get_item_raw, search_scenes

logger = logging.getLogger("sentinel_assets")
DATA_API = "https://planetarycomputer.microsoft.com/api/data/v1"
SAS_API = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
RESCALE = {"vv": "0,600", "vh": "0,270", "hh": "0,600", "hv": "0,270"}
_sas_cache: dict = {}


class SceneAssetError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def summarize_assets(item: dict) -> dict:
    """Inspect the real STAC asset dictionary instead of assuming key names."""
    assets = item.get("assets") or {}
    listing = [{"key": k, "type": (v or {}).get("type"), "roles": (v or {}).get("roles") or []} for k, v in assets.items()]
    sar = [a["key"] for a in listing if "data" in a["roles"] and "tiff" in str(a["type"]).lower()]
    analysis = next((k for k in ("vv", "hh", "vh", "hv") if k in sar), sar[0] if sar else None)
    preview = next((k for k in ("rendered_preview", "thumbnail") if k in assets), None)
    return {"available_assets": [a["key"] for a in listing], "assets": listing, "sar_assets": sar, "analysis_asset": analysis,
            "native_preview_asset": preview, "preview_href": (assets.get("rendered_preview") or {}).get("href"), "thumbnail_href": (assets.get("thumbnail") or {}).get("href")}


async def sas_token(collection: str) -> str:
    hit = _sas_cache.get(collection)
    if hit and hit[0] - time.time() > 300:
        return hit[1]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SAS_API}/{collection}")
        r.raise_for_status()
        d = r.json()
    exp = datetime.fromisoformat(d["msft:expiry"].replace("Z", "+00:00")).timestamp()
    _sas_cache[collection] = (exp, d["token"])
    return d["token"]


async def signed_href(collection: str, href: str) -> str:
    """Planetary Computer blob assets need a short-lived SAS token; regenerated on demand (never persisted)."""
    if "blob.core.windows.net" not in href:
        return href
    return f"{href}?{await sas_token(collection)}"


async def sar_asset_accessible(collection: str, href: str) -> tuple[bool, Optional[str]]:
    try:
        url = await signed_href(collection, href)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.head(url)
            if r.status_code == 403:  # expired/invalid token → regenerate once
                _sas_cache.pop(collection, None)
                r = await c.head(await signed_href(collection, href))
        return r.status_code == 200, None if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:160]


async def sar_bbox_png(collection: str, stac_id: str, asset: str, bbox_wsen: list, size: int = 1024) -> bytes:
    """Render an AOI window of the REAL SAR raster (COG) through the PC data API — analysis input, not a thumbnail."""
    w, s, e, n = bbox_wsen
    url = f"{DATA_API}/item/bbox/{w},{s},{e},{n}/{size}x{size}.png"
    params = {"collection": collection, "item": stac_id, "assets": asset, "rescale": RESCALE.get(asset, "0,600")}
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        raise SceneAssetError("raster_download_failed", f"Raster download failed for {asset} window: HTTP {r.status_code}")
    return r.content


def _stac_collection(scene: dict) -> Optional[str]:
    return (scene.get("metadata") or {}).get("stac_collection")


async def resolve_scene_assets(scene: dict, check_access: bool = False) -> dict:
    """Authoritative scene status. Caches the stable asset inventory on the scene document."""
    md = scene.get("metadata") or {}
    col = _stac_collection(scene)
    base = {"scene_id": scene["id"], "provider_scene_id": scene["provider_scene_id"], "collection": col, "provider": scene["provider"], "acquisition_time": scene["acquisition_time"],
            "bbox": md.get("bbox"), "quicklook_available": bool(scene.get("quicklook_path") or md.get("preview_href") or md.get("thumbnail_href")),
            "quicklook_kind": scene.get("quicklook_kind") or ("native" if md.get("preview_href") or md.get("thumbnail_href") else None),
            "quicklook_status": scene.get("quicklook_status") or ("ready" if scene.get("quicklook_path") else ("available" if md.get("preview_href") else "none")),
            "real_data": bool(col), "provider_source": md.get("source")}
    if not col:
        return {**base, "available_assets": [], "sar_assets": [], "analysis_asset": None, "sar_available": False, "state": "SAR_UNAVAILABLE",
                "reason": "SAR asset missing — scene was registered manually without a Sentinel STAC item"}
    inv = scene.get("assets")
    if not inv:
        try:
            inv = summarize_assets(await get_item_raw(col, scene["provider_scene_id"]))
        except Exception as e:  # noqa: BLE001
            return {**base, "available_assets": [], "sar_assets": [], "analysis_asset": None, "sar_available": False, "state": "SAR_UNAVAILABLE",
                    "reason": f"Sentinel scene metadata unavailable: {str(e)[:120]}"}
        await db.scenes.update_one({"id": scene["id"]}, {"$set": {"assets": inv, "metadata.preview_href": md.get("preview_href") or inv["preview_href"], "metadata.thumbnail_href": md.get("thumbnail_href") or inv["thumbnail_href"]}})
        scene["assets"] = inv
    out = {**base, "available_assets": inv["available_assets"], "sar_assets": inv["sar_assets"], "analysis_asset": inv["analysis_asset"], "sar_available": bool(inv["analysis_asset"]),
           "native_preview_asset": inv.get("native_preview_asset"), "quicklook_available": base["quicklook_available"] or bool(inv.get("preview_href"))}
    if not out["sar_available"]:
        out.update({"state": "SAR_UNAVAILABLE", "reason": f"SAR asset missing — STAC item exposes no GeoTIFF data asset (found {inv['available_assets']})"})
    elif out["quicklook_status"] == "generating":
        out.update({"state": "QUICKLOOK_GENERATING", "reason": "SAR available; preview still being generated"})
    else:
        out.update({"state": "SAR_READY", "reason": None})
    if check_access and out["sar_available"]:
        try:
            item = await get_item_raw(col, scene["provider_scene_id"])
            ok, err = await sar_asset_accessible(col, item["assets"][out["analysis_asset"]]["href"])
        except Exception as e:  # noqa: BLE001
            ok, err = False, str(e)[:160]
        out["sar_asset_accessible"] = ok
        if not ok:
            out.update({"state": "SAR_UNAVAILABLE", "reason": f"Asset signing/access failed: {err}"})
    return out


async def generate_quicklook(scene: dict) -> bytes:
    """No native preview → render the whole footprint from the real SAR raster (visualization only)."""
    st = await resolve_scene_assets(scene)
    if not st["sar_available"]:
        raise SceneAssetError("sar_missing", st["reason"])
    await db.scenes.update_one({"id": scene["id"]}, {"$set": {"quicklook_status": "generating"}})
    try:
        from shapely.geometry import shape
        bbox = st["bbox"] or list(shape(scene["footprint"]).bounds)
        png = await sar_bbox_png(st["collection"], scene["provider_scene_id"], st["analysis_asset"], bbox)
    except Exception as e:
        await db.scenes.update_one({"id": scene["id"]}, {"$set": {"quicklook_status": "failed", "quicklook_error": str(e)[:200]}})
        raise SceneAssetError("quicklook_generation_failed", f"Quicklook generation failed: {str(e)[:160]}")
    await db.scenes.update_one({"id": scene["id"]}, {"$set": {"quicklook_status": "ready", "quicklook_kind": "generated", "quicklook_error": None}})
    return png


def scene_status_summary(scene: Optional[dict], case: dict) -> dict:
    """Cheap, network-free summary for GET /cases/{id}; derived from persisted scene state only."""
    if not case.get("scene_id"):
        return {"scene_id": None, "state": "SAR_UNAVAILABLE", "sar_available": False, "quicklook_available": False, "analysis_asset": None, "reason": "No Sentinel scene attached to this case"}
    if not scene:
        return {"scene_id": case["scene_id"], "state": "SAR_UNAVAILABLE", "sar_available": False, "quicklook_available": False, "analysis_asset": None, "reason": "Sentinel scene metadata unavailable (scene record missing)"}
    md, inv = scene.get("metadata") or {}, scene.get("assets") or {}
    sar = bool(_stac_collection(scene)) and (inv.get("analysis_asset") is not None if inv else True)
    ql = bool(scene.get("quicklook_path") or md.get("preview_href") or md.get("thumbnail_href"))
    state = "SAR_UNAVAILABLE" if not sar else ("QUICKLOOK_GENERATING" if scene.get("quicklook_status") == "generating" else "SAR_READY")
    return {"scene_id": scene["id"], "provider_scene_id": scene["provider_scene_id"], "collection": _stac_collection(scene), "acquisition_time": scene["acquisition_time"],
            "sar_available": sar, "quicklook_available": ql, "quicklook_kind": scene.get("quicklook_kind") or ("native" if ql else None), "quicklook_status": scene.get("quicklook_status") or ("ready" if scene.get("quicklook_path") else "available" if ql else "none"),
            "analysis_asset": inv.get("analysis_asset") or ("vv" if sar else None), "available_assets": inv.get("available_assets"), "state": state,
            "reason": None if sar else "SAR asset missing — scene was registered manually without Sentinel-1 STAC imagery"}


async def auto_attach_scene(case: dict, actor: str, window_h: int = 6) -> dict:
    """Find the real Sentinel-1 GRD scene covering the spill centroid nearest to acquisition time; register + attach it."""
    from services import create_scene
    from models import SceneCreate
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "centroid": 1, "acquisition_time": 1})
    lon, lat = spill["centroid"]["coordinates"]
    t = to_utc(spill["acquisition_time"])
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        res = await search_scenes([lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01], (t - timedelta(hours=window_h)).strftime(fmt), (t + timedelta(hours=window_h)).strftime(fmt), "sentinel-1-grd", 10)
    except Exception as e:  # noqa: BLE001
        raise SceneAssetError("stac_unavailable", f"Sentinel scene metadata unavailable: STAC search failed ({str(e)[:100]})")
    if not res["scenes"]:
        raise SceneAssetError("no_scene", f"No Sentinel scene selected and no Sentinel-1 GRD acquisition covers the spill within ±{window_h} h of {t.strftime(fmt)}")
    it = min(res["scenes"], key=lambda s: abs((datetime.fromisoformat(s["datetime"].replace("Z", "+00:00")) - t).total_seconds()))
    existing = await db.scenes.find_one({"provider_scene_id": it["stac_id"]}, {"_id": 0})
    if not existing:
        payload = SceneCreate(provider=it["provider"], provider_scene_id=it["stac_id"], sensor_mode=it.get("instrument_mode"), polarization="+".join(it["polarizations"]) if it.get("polarizations") else None,
                              acquisition_time=it["datetime"], footprint=it["footprint"], storage_ref=it["stac_href"],
                              metadata={"stac_collection": it["collection"], "platform": it.get("platform"), "orbit_state": it.get("orbit_state"), "relative_orbit": it.get("relative_orbit"), "bbox": it.get("bbox"),
                                        "preview_href": it.get("preview_href"), "thumbnail_href": it.get("thumbnail_href"), "source": "Microsoft Planetary Computer STAC"})
        existing = await create_scene(payload, actor)
    await attach_scene(case, existing, actor, auto=True)
    return existing


async def attach_scene(case: dict, scene: dict, actor: str, auto: bool = False) -> None:
    now = datetime.now(timezone.utc)
    await db.cases.update_one({"id": case["id"]}, {"$set": {"scene_id": scene["id"], "scene_attachment": {"scene_id": scene["id"], "provider_scene_id": scene["provider_scene_id"], "collection": _stac_collection(scene),
                                                                                                         "attached_at": now, "attached_by": actor, "auto": auto}}})
    await db.spill_observations.update_one({"id": case["spill_observation_id"]}, {"$set": {"scene_id": scene["id"]}})
    await audit("case", case["id"], "case.scene_attached", {"scene_id": scene["id"], "provider_scene_id": scene["provider_scene_id"], "auto": auto}, actor)
