import asyncio
import os

import httpx
import planetary_computer as pc

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTIONS = {
    "sentinel-1-grd": {"label": "Sentinel-1 GRD (SAR, C-band)", "provider": "sentinel-1", "kind": "sar", "ml_ready": False},
    "sentinel-1-rtc": {"label": "Sentinel-1 RTC (calibrated SAR; PC account required)", "provider": "sentinel-1", "kind": "sar", "ml_ready": True},
    "sentinel-2-l2a": {"label": "Sentinel-2 L2A (optical)", "provider": "sentinel-2", "kind": "optical", "ml_ready": False},
}
MAX_ASSET_BYTES = int(float(os.getenv("SATELLITE_MAX_ASSET_MB", "64")) * 1024 * 1024)


class SatelliteAssetTooLarge(RuntimeError):
    pass


def _normalize(item: dict) -> dict:
    p, a = item["properties"], item.get("assets", {})
    col = item.get("collection")
    return {
        "stac_id": item["id"], "collection": col, "provider": COLLECTIONS.get(col, {}).get("provider", col), "kind": COLLECTIONS.get(col, {}).get("kind"),
        "datetime": p.get("datetime"), "platform": p.get("platform"), "instrument_mode": p.get("sar:instrument_mode"),
        "polarizations": p.get("sar:polarizations"), "orbit_state": p.get("sat:orbit_state"), "relative_orbit": p.get("sat:relative_orbit"),
        "cloud_cover": p.get("eo:cloud_cover"), "product_type": p.get("sar:product_type") or p.get("s2:product_type"),
        "footprint": item.get("geometry"), "bbox": item.get("bbox"),
        "preview_href": (a.get("rendered_preview") or {}).get("href"), "thumbnail_href": (a.get("thumbnail") or {}).get("href"),
        "vv_href": (a.get("vv") or {}).get("href") or (a.get("VV") or {}).get("href"), "vh_href": (a.get("vh") or {}).get("href") or (a.get("VH") or {}).get("href"),
        "assets": {k: v.get("href") for k,v in a.items() if isinstance(v, dict) and v.get("href")},
        "stac_href": f"{STAC}/collections/{col}/items/{item['id']}",
    }


async def search_scenes(bbox: list, start: str, end: str, collection: str = "sentinel-1-grd", limit: int = 25, max_cloud: int | None = None) -> dict:
    body = {"collections": [collection], "bbox": bbox, "datetime": f"{start}/{end}", "limit": min(limit, 100), "sortby": [{"field": "datetime", "direction": "desc"}]}
    if collection == "sentinel-2-l2a" and max_cloud is not None:
        body["query"] = {"eo:cloud_cover": {"lt": max_cloud}}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{STAC}/search", json=body)
        r.raise_for_status()
        d = r.json()
    return {"count": len(d.get("features", [])), "matched": (d.get("context") or {}).get("matched"), "scenes": [_normalize(f) for f in d.get("features", [])], "source": "Microsoft Planetary Computer STAC (assets signed at access time)"}


async def get_item(collection: str, stac_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{STAC}/collections/{collection}/items/{stac_id}")
        r.raise_for_status()
        return _normalize(r.json())


async def access_href(href: str) -> str:
    """Return an access-ready URL.

    Microsoft Planetary Computer STAC metadata can expose unsigned Azure Blob
    HREFs. The SDK obtains a short-lived SAS token for protected assets. Some
    collections, notably Sentinel-1 RTC, require PC_SDK_SUBSCRIPTION_KEY.
    Public/non-Blob URLs are returned unchanged by ``pc.sign``.
    """
    try:
        return await asyncio.to_thread(pc.sign, href)
    except Exception:
        # Keep public previews usable if signing is unnecessary/unavailable;
        # protected assets will still fail clearly when opened or fetched.
        return href


# Backward-compatible private alias for older imports.
_access_href = access_href

_preview_cache: dict = {}


async def fetch_preview(href: str, fallback: str | None = None) -> tuple[bytes, str]:
    if href in _preview_cache:
        return _preview_cache[href]
    last = None
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        for url in [href, href, fallback]:
            if not url:
                continue
            try:
                r = await c.get(await access_href(url))
                r.raise_for_status()
                out = (r.content, r.headers.get("content-type", "image/png"))
                if len(_preview_cache) > 300:
                    _preview_cache.clear()
                _preview_cache[href] = out
                return out
            except Exception as e:  # noqa: BLE001
                last = e
                await asyncio.sleep(1.5)
    raise last


async def fetch_asset(href: str, max_bytes: int | None = None) -> tuple[bytes, str]:
    """Fetch a bounded raw asset into memory.

    Full Sentinel SAR products can be hundreds of MB or more. Runtime ML uses
    rasterio windowed COG reads instead. This helper is intentionally bounded so
    fallback/utility paths cannot accidentally download a complete swath into RAM.
    """
    limit = MAX_ASSET_BYTES if max_bytes is None else max_bytes
    signed_href = await access_href(href)
    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
        async with c.stream("GET", signed_href) as r:
            r.raise_for_status()
            content_length = r.headers.get("content-length")
            if limit and content_length:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = 0
                if declared > limit:
                    raise SatelliteAssetTooLarge(
                        f"asset_too_large:{declared} bytes exceeds {limit} byte in-memory limit; "
                        "use an analysis_bbox/windowed raster read"
                    )
            async for chunk in r.aiter_bytes(1024 * 1024):
                total += len(chunk)
                if limit and total > limit:
                    raise SatelliteAssetTooLarge(
                        f"asset_too_large:download exceeded {limit} byte in-memory limit; "
                        "use an analysis_bbox/windowed raster read"
                    )
                chunks.append(chunk)
            return b"".join(chunks), r.headers.get("content-type", "application/octet-stream")
