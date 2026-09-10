"""Verify sar_spill_seg_v2 against a real Sentinel-1 RTC scene via the running API.

This is a post-training integration check, not an accuracy benchmark. It confirms
that the deployed API has loaded the evaluated v2 checkpoint, can query/register
a real Sentinel-1 RTC scene, window the requested investigation AOI, and execute
VV+VH inference without falling back to the baseline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
import sys

import httpx


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--collection", default="sentinel-1-rtc")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                   default=[72.45, 18.70, 72.65, 18.90],
                   help="Small WGS84 investigation AOI; default is an offshore Mumbai demo box")
    p.add_argument("--days", type=int, default=60, help="Search this many days back from now")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--email", default=os.getenv("DEMO_ANALYST_EMAIL", "analyst@varunanetra.local"))
    p.add_argument("--password", default=os.getenv("DEMO_ANALYST_PASSWORD"),
                   help="Prefer DEMO_ANALYST_PASSWORD env; omitted value is prompted securely")
    return p.parse_args()


def fail(message: str, details=None, code: int = 2):
    print(f"VERIFY FAILED: {message}", file=sys.stderr)
    if details is not None:
        print(json.dumps(details, indent=2, default=str), file=sys.stderr)
    raise SystemExit(code)


def main():
    args = parse_args()
    west, south, east, north = args.bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        fail("--bbox must be WEST SOUTH EAST NORTH")
    if args.days < 1:
        fail("--days must be >= 1")

    password = args.password or getpass.getpass(f"Password for {args.email}: ")
    base = args.base_url.rstrip("/")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=args.days)

    with httpx.Client(base_url=base, timeout=180.0, follow_redirects=True) as client:
        try:
            health = client.get("/api/")
            health.raise_for_status()
        except Exception as exc:
            fail(f"Varuna-Netra API is not reachable at {base}: {exc}")

        service = health.json()
        seg = service.get("sar_segmentation") or {}
        print("Runtime status:")
        print(json.dumps(seg, indent=2))
        if not seg.get("loaded"):
            fail(
                "sar_spill_seg_v2 is not loaded. Train/promote the checkpoint and start the backend with the ML runtime enabled.",
                seg,
            )

        login = client.post("/api/auth/login", json={"email": args.email, "password": password})
        if login.status_code >= 400:
            fail(f"Analyst login failed ({login.status_code})", login.json() if login.content else None)
        token = login.json().get("access_token")
        if not token:
            fail("Login succeeded but no access token was returned")
        headers = {"Authorization": f"Bearer {token}"}

        search_payload = {
            "bbox": args.bbox,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": now.isoformat().replace("+00:00", "Z"),
            "collection": args.collection,
            "limit": args.limit,
            "max_cloud": None,
        }
        search = client.post("/api/satellite/search", json=search_payload, headers=headers)
        if search.status_code >= 400:
            fail(f"Sentinel search failed ({search.status_code})", search.json() if search.content else None)
        data = search.json()
        scenes = [s for s in data.get("scenes", []) if s.get("vv_href") and s.get("vh_href")]
        if not scenes:
            fail(
                f"No dual-polarization {args.collection} scenes found in the requested AOI/date range. Try a larger --days value or another small offshore --bbox.",
                {"count": data.get("count"), "bbox": args.bbox, "days": args.days},
            )

        selected = scenes[0]
        print(f"Selected scene: {selected['stac_id']}")
        print(f"Acquisition    : {selected.get('datetime')}")
        print(f"Polarizations  : {selected.get('polarizations')}")
        print(f"Analysis AOI   : {args.bbox}")

        register_payload = {
            "collection": selected["collection"],
            "stac_id": selected["stac_id"],
            "detect": True,
            "analysis_bbox": args.bbox,
        }
        register = client.post("/api/satellite/register", json=register_payload, headers=headers)
        if register.status_code >= 400:
            fail(f"Scene register/detect failed ({register.status_code})", register.json() if register.content else None)

        result = register.json()
        detection = result.get("detection") or {}
        model_id = (detection.get("model") or {}).get("model_id")
        summary = {
            "scene_id": (result.get("scene") or {}).get("id"),
            "stac_id": selected["stac_id"],
            "model_id": model_id,
            "detector": detection.get("detector"),
            "spots": detection.get("spots"),
            "input_source": detection.get("input_source"),
            "analysis_bbox_requested": detection.get("analysis_bbox_requested"),
            "analysis_bbox_used": detection.get("analysis_bbox_used"),
            "fallback_reason": detection.get("segmentation_fallback_reason"),
            "case_id": (result.get("case") or {}).get("id"),
        }
        print("\nDetection result:")
        print(json.dumps(summary, indent=2, default=str))

        if model_id != "sar_spill_seg_v2":
            fail(
                "Real scene request did not use sar_spill_seg_v2; inspect fallback_reason and deployment configuration.",
                summary,
            )
        if detection.get("segmentation_fallback_reason"):
            fail("v2 reported a fallback reason despite model selection", summary)

        print("\nVERIFY PASSED: real Sentinel-1 RTC VV+VH data reached sar_spill_seg_v2 through the API.")
        print("A zero-candidate result is still a valid integration pass; use Part III metrics for accuracy claims.")


if __name__ == "__main__":
    main()
