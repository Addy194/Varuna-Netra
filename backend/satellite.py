import asyncio

import httpx

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTIONS = {
    "sentinel-1-grd": {"label": "Sentinel-1 GRD (SAR, C-band)", "provider": "sentinel-1", "kind": "sar"},
    "sentinel-2-l2a": {"label": "Sentinel-2 L2A (optical)", "provider": "sentinel-2", "kind": "optical"},
}


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
    return {"count": len(d.get("features", [])), "matched": (d.get("context") or {}).get("matched"), "scenes": [_normalize(f) for f in d.get("features", [])], "source": "Microsoft Planetary Computer STAC (open, no key)"}


async def get_item(collection: str, stac_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{STAC}/collections/{collection}/items/{stac_id}")
        r.raise_for_status()
        return _normalize(r.json())


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
                r = await c.get(url)
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


async def fetch_asset(href: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
        r = await c.get(href); r.raise_for_status(); return r.content, r.headers.get("content-type", "application/octet-stream")
