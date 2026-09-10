import asyncio, hashlib, io, json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, shape

from db import db, audit
from models import SpillObservationCreate
from satellite import access_href, fetch_preview, fetch_asset
from sar_segmentation_runtime import (
    DETECTOR_VERSION as SEG_DETECTOR_VERSION,
    MAX_PIXELS as SEG_MAX_PIXELS,
    IncompatibleSARInput,
    SegmentationUnavailable,
    infer_probability as infer_segmentation,
    model_info as segmentation_model_info,
    normalize_db as normalize_seg_db,
    runtime_status as segmentation_runtime_status,
    to_db as seg_to_db,
)
from storage import put_object, APP_NAME

BASELINE_DETECTOR_VERSION = "sar-ml-pixel-1.0.0"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "sar_spill_pixel_v1.json"
MIN_AREA_PX, MAX_AREA_FRAC, MIN_ELONGATION, MAX_SPOTS = 40, 0.02, 1.3, 5
_BASE_MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def _baseline_model_info():
    tr = _BASE_MODEL.get("training", {})
    return {
        "detector_version": BASELINE_DETECTOR_VERSION,
        "model_id": _BASE_MODEL["model_id"],
        "model_type": _BASE_MODEL["type"],
        "features": _BASE_MODEL["feature_names"],
        "validation": tr.get("validation"),
        "holdout_metrics": tr.get("holdout_metrics"),
        "operational_validation_required": True,
    }


def model_info():
    """Describe the preferred runtime model while preserving fallback metadata."""
    seg = segmentation_model_info()
    baseline = _baseline_model_info()
    active = seg if seg.get("available") else baseline
    return {**active, "preferred_model": "sar_spill_seg_v2", "segmentation_runtime": seg, "fallback_model": baseline}


def _affine(bbox, w, h):
    west, south, east, north = bbox
    return lambda x, y: (west + (x / w) * (east - west), north - (y / h) * (north - south))


def _features(img):
    x = img.astype(np.float32) / 255.0
    local = cv2.GaussianBlur(x, (0, 0), 2.0)
    gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    smooth = cv2.GaussianBlur(grad, (0, 0), 1.2)
    coh = np.maximum(cv2.GaussianBlur(x * x, (0, 0), 2) - local * local, 0)
    return np.stack([1.0 - x, np.abs(x - local), smooth, grad, coh], axis=-1)


def _predict_baseline(img):
    f = _features(img)
    mu = np.asarray(_BASE_MODEL["mean"], np.float32)
    sd = np.asarray(_BASE_MODEL["scale"], np.float32)
    w = np.asarray(_BASE_MODEL["weights"], np.float32)
    b = float(_BASE_MODEL["bias"])
    z = b + ((f - mu) / sd) @ w
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def _synthetic_demo(seed_text):
    digest = hashlib.sha256(seed_text.encode()).digest()
    seed = int.from_bytes(digest[:8], "big") & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    h = w = 512
    base = np.clip(rng.normal(142, 28, (h, w)), 20, 230).astype(np.float32)
    yy, xx = np.mgrid[:h, :w]
    for _ in range(3):
        cx, cy = rng.uniform(80, w - 80), rng.uniform(80, h - 80)
        ang = rng.uniform(0, np.pi)
        major, minor = rng.uniform(45, 105), rng.uniform(10, 28)
        xr = (xx - cx) * np.cos(ang) + (yy - cy) * np.sin(ang)
        yr = -(xx - cx) * np.sin(ang) + (yy - cy) * np.cos(ang)
        mask = np.exp(-0.5 * ((xr / major) ** 2 + (yr / minor) ** 2))
        base -= mask * rng.uniform(35, 65)
    base += cv2.GaussianBlur(rng.normal(0, 14, (h, w)).astype(np.float32), (0, 0), 5)
    base = np.clip(base, 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(base, "L").save(out, "PNG")
    return out.getvalue()


def _decode_bytes(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as im:
            return np.array(im.convert("L"))
    except Exception:
        return None


def _asset_to_gray(data: bytes):
    arr = _decode_bytes(data)
    if arr is not None:
        return arr
    try:
        import rasterio
        with rasterio.MemoryFile(data) as mf:
            with mf.open() as ds:
                arr = ds.read(1, out_shape=(1, min(ds.height, 2048), min(ds.width, 2048)))
        arr = np.nan_to_num(arr, nan=0, posinf=0, neginf=0).astype(np.float32)
        lo, hi = np.percentile(arr, [2, 98])
        return np.clip((arr - lo) * 255 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)
    except Exception:
        return None


def _read_remote_band(url: str):
    """Read a manageable native-resolution COG without first downloading the whole asset."""
    import rasterio
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIRANGE="YES"):
        with rasterio.open(url) as ds:
            if ds.height * ds.width > SEG_MAX_PIXELS:
                raise IncompatibleSARInput(
                    f"scene_too_large_for_runtime:{ds.height}x{ds.width}; crop/prepare an AOI before v2 inference"
                )
            arr = ds.read(1).astype(np.float32)
            return arr, str(ds.crs), tuple(ds.transform)


def _read_remote_pair(vv_url: str, vh_url: str):
    vv, vv_crs, vv_transform = _read_remote_band(vv_url)
    vh, vh_crs, vh_transform = _read_remote_band(vh_url)
    if vv.shape != vh.shape:
        raise IncompatibleSARInput(f"vv_vh_shape_mismatch:{vv.shape}:{vh.shape}")
    if vv_crs != vh_crs or vv_transform != vh_transform:
        raise IncompatibleSARInput("vv_vh_grid_mismatch")
    return vv, vh


async def _baseline_imagery(scene):
    md = scene.get("metadata") or {}
    for key in ("vv_href", "vh_href"):
        href = md.get(key)
        if href:
            try:
                data, _ = await fetch_asset(href)
                arr = _asset_to_gray(data)
                if arr is not None and arr.size > 1000:
                    return arr, False, key
            except Exception:
                pass
    href = md.get("preview_href")
    if href:
        png, _ = await fetch_preview(href, md.get("thumbnail_href"))
        arr = _asset_to_gray(png)
        if arr is not None:
            return arr, False, "rendered_preview"
    arr = _asset_to_gray(_synthetic_demo(scene.get("id") or scene.get("provider_scene_id") or "demo"))
    return arr, True, "synthetic_demo"


def _segmentation_domain(scene):
    md = scene.get("metadata") or {}
    explicit = md.get("sar_value_domain")
    if explicit in {"sigma0_db", "sigma0_linear", "gamma0_linear_rtc", "gamma0_db_rtc"}:
        return explicit
    if md.get("stac_collection") == "sentinel-1-rtc":
        return "gamma0_linear_rtc"
    return None


def _contours(
    prob,
    img,
    bbox,
    footprint,
    threshold=0.40,
    max_area_frac=MAX_AREA_FRAC,
    min_elongation=MIN_ELONGATION,
    score_as_confidence=False,
):
    prob = cv2.GaussianBlur(prob, (0, 0), 2.0)
    valid = (prob >= threshold).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(valid, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = img.shape
    to_geo, fp = _affine(bbox, w, h), shape(footprint)
    spots = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_AREA_PX or area > max_area_frac * w * h or len(c) < 5:
            continue
        (cx, cy), (ma, mi), ang = cv2.fitEllipse(c)
        elong = max(ma, mi) / max(min(ma, mi), 1e-3)
        if elong < min_elongation:
            continue
        pix = np.zeros_like(mask)
        cv2.drawContours(pix, [c], -1, 255, -1)
        vals = prob[pix > 0]
        score = float(np.percentile(vals, 75))
        darkness = float(1 - np.mean(img[pix > 0]) / 255.0)
        pts = cv2.approxPolyDP(c, 2.0, True).reshape(-1, 2)
        ring = [list(to_geo(float(x), float(y))) for x, y in pts]
        ring.append(ring[0])
        poly = Polygon(ring)
        if not poly.is_valid or poly.is_empty or not poly.intersects(fp):
            continue
        confidence = score if score_as_confidence else min(0.99, 0.45 + 0.6 * (score - 0.5))
        spots.append({
            "geometry": {"type": "Polygon", "coordinates": [[[round(x, 5), round(y, 5)] for x, y in ring]]},
            "area_px": float(area),
            "elongation": round(float(elong), 2),
            "model_score": round(score, 3),
            "darkness": round(darkness, 3),
            "confidence": round(float(confidence), 3),
            "pixel_bbox": [int(v) for v in cv2.boundingRect(c)],
            "centroid_px": [float(cx), float(cy)],
            "angle_deg": round(float(ang), 1),
        })
    spots.sort(key=lambda s: -s["confidence"])
    return spots[:MAX_SPOTS], mask


async def _try_segmentation(scene, bbox, footprint):
    status = segmentation_runtime_status()
    if not status.get("available"):
        return None, status.get("reason") or "segmentation_runtime_unavailable"

    md = scene.get("metadata") or {}
    vv_href, vh_href = md.get("vv_href"), md.get("vh_href")
    if not vv_href or not vh_href:
        return None, "dual_polarization_vv_vh_required"

    domain = _segmentation_domain(scene)
    if not domain:
        collection = md.get("stac_collection") or "unknown"
        return None, f"incompatible_radiometry:{collection}; v2 expects prepared Sigma0 or Sentinel-1 RTC"

    try:
        vv_url, vh_url = await asyncio.gather(access_href(vv_href), access_href(vh_href))
        vv, vh = await asyncio.to_thread(_read_remote_pair, vv_url, vh_url)
        prob, inference = await asyncio.to_thread(infer_segmentation, vv, vh, domain)
    except (IncompatibleSARInput, SegmentationUnavailable) as exc:
        if status.get("mode") == "force":
            raise
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        if status.get("mode") == "force":
            raise
        return None, f"segmentation_input_or_inference_failed:{type(exc).__name__}:{str(exc)[:140]}"

    vv_db = seg_to_db(vv, domain)
    display = (normalize_seg_db(vv_db, inference["db_min"], inference["db_max"]) * 255).astype(np.uint8)
    spots, _ = _contours(
        prob,
        display,
        bbox,
        footprint,
        threshold=inference["threshold"],
        max_area_frac=0.35,
        min_elongation=1.0,
        score_as_confidence=True,
    )
    info = segmentation_model_info()
    info["inference"] = inference
    note = "Real-data VV/VH semantic-segmentation candidate detection. Analyst review required."
    if inference.get("domain_shift"):
        note += " Runtime input is RTC Gamma0 converted to dB while training data are Sigma0 dB; domain shift is explicitly flagged."
    return {
        "spots": spots,
        "width": int(display.shape[1]),
        "height": int(display.shape[0]),
        "candidates_total": len(spots),
        "synthetic": False,
        "input_source": "vv+vh",
        "model": info,
        "detector_version": SEG_DETECTOR_VERSION,
        "domain_shift": bool(inference.get("domain_shift")),
        "display": display,
        "note": note,
    }, None


def analyze_array(img, bbox, footprint, synthetic=False, source="sar"):
    if img is None or img.size < 1000:
        return {"spots": [], "note": "insufficient valid pixels", "synthetic": synthetic, "input_source": source}
    prob = _predict_baseline(img)
    spots, _ = _contours(prob, img, bbox, footprint)
    note = "Bundled ML demo model; real Sentinel-1 labelled validation required before operational use."
    return {
        "spots": spots,
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
        "candidates_total": len(spots),
        "synthetic": synthetic,
        "input_source": source,
        "model": _baseline_model_info(),
        "detector_version": BASELINE_DETECTOR_VERSION,
        "note": note,
    }


def thumbnail_webp(png_or_arr: bytes | np.ndarray, pixel_bbox: list, pad: int = 40) -> bytes:
    if isinstance(png_or_arr, np.ndarray):
        img = Image.fromarray(png_or_arr).convert("RGB")
    else:
        img = Image.open(io.BytesIO(png_or_arr)).convert("RGB")
    x, y, bw, bh = pixel_bbox
    box = (max(0, x - pad), max(0, y - pad), min(img.width, x + bw + pad), min(img.height, y + bh + pad))
    crop = img.crop(box)
    crop.thumbnail((480, 480))
    out = io.BytesIO()
    crop.save(out, "WEBP", quality=70)
    return out.getvalue()


async def get_quicklook(scene):
    from storage import get_object
    if scene.get("quicklook_path"):
        try:
            data, _ = await get_object(scene["quicklook_path"])
            return data
        except Exception:
            pass
    md = scene.get("metadata") or {}
    if md.get("preview_href"):
        png, _ = await fetch_preview(md["preview_href"], md.get("thumbnail_href"))
    else:
        png = _synthetic_demo(scene.get("id") or scene.get("provider_scene_id") or "demo")
    path = f'{APP_NAME}/quicklooks/{scene["provider_scene_id"]}.png'
    try:
        res = await put_object(path, png, "image/png")
        await db.scenes.update_one({"id": scene["id"]}, {"$set": {"quicklook_path": res["path"], "quicklook_bytes": len(png)}})
    except Exception:
        pass
    return png


async def detect_scene(scene, actor="system"):
    from services import create_spill_observation

    bbox = (scene.get("metadata") or {}).get("bbox") or list(shape(scene["footprint"]).bounds)
    res, fallback_reason = await _try_segmentation(scene, bbox, scene["footprint"])
    if res is not None:
        arr = res.pop("display")
    else:
        arr, synthetic, source = await _baseline_imagery(scene)
        res = analyze_array(arr, bbox, scene["footprint"], synthetic, source)
        if fallback_reason:
            res["segmentation_fallback_reason"] = fallback_reason
            res["note"] += f" sar_spill_seg_v2 fallback: {fallback_reason}."

    model = res.get("model") or _baseline_model_info()
    detector_version = res.get("detector_version") or model.get("detector_version") or BASELINE_DETECTOR_VERSION
    model_id = model.get("model_id", _BASE_MODEL["model_id"])
    using_v2 = model_id == "sar_spill_seg_v2"
    synthetic = bool(res.get("synthetic"))

    cases = []
    now = datetime.now(timezone.utc)
    for s in res["spots"]:
        conf = s["confidence"]
        flags = ["ml_candidate", "analyst_review_required"]
        if using_v2:
            flags += ["vv_vh_dual_polarization", "real_data_segmentation_model", "uncalibrated_model_score"]
            if res.get("domain_shift"):
                flags.append("sar_domain_shift_gamma0_vs_sigma0")
        if conf < 0.68:
            flags.append("low_model_confidence")
        if s["darkness"] < 0.22:
            flags.append("weak_backscatter_contrast")
        if synthetic:
            flags.append("synthetic_demo_input")

        notes = res.get("note") or "ML SAR candidate detection. Analyst review required."
        payload = SpillObservationCreate(
            scene_id=scene["id"],
            geometry=s["geometry"],
            acquisition_time=scene["acquisition_time"],
            source="sar_ml_detector",
            detection_confidence=conf,
            quality_flags=flags,
            processing_version=detector_version,
            notes=notes,
        )
        spill, case = await create_spill_observation(payload, actor)
        case["detector_model"] = model
        case["synthetic_input"] = synthetic
        case["segmentation_fallback_reason"] = res.get("segmentation_fallback_reason")
        cases.append(case)
        try:
            thumb = thumbnail_webp(arr, s["pixel_bbox"])
            from db import db as _db
            aid = f'{spill["id"]}-detector'
            await _db.attachments.insert_one({
                "id": aid,
                "case_id": case["id"],
                "filename": f'{case["case_number"]}-candidate.webp',
                "content_type": "image/webp",
                "size": len(thumb),
                "storage_path": f"{APP_NAME}/attachments/{aid}.webp",
                "created_at": now,
                "is_deleted": False,
            })
            await put_object(f"{APP_NAME}/attachments/{aid}.webp", thumb, "image/webp")
            await _db.cases.update_one({"id": case["id"]}, {"$set": {"thumbnail_attachment_id": aid}})
        except Exception:
            pass

    summary = {k: v for k, v in res.items() if k != "spots"} | {"spots": len(res["spots"]), "at": now}
    await db.scenes.update_one({"id": scene["id"]}, {"$set": {
        "status": "detected",
        "detector_version": detector_version,
        "detector_summary": summary,
    }})
    await audit("scene", scene["id"], "scene.detected", {
        "detector": detector_version,
        "model": model_id,
        "spots": len(res["spots"]),
        "cases": [c["case_number"] for c in cases],
        "synthetic_input": synthetic,
        "segmentation_fallback_reason": res.get("segmentation_fallback_reason"),
        "domain_shift": bool(res.get("domain_shift")),
    }, actor)
    return {
        "detector": detector_version,
        "model": model,
        "operational_validation_required": bool(model.get("operational_validation_required", True)),
        "synthetic_input": synthetic,
        "spots": len(res["spots"]),
        "cases": cases,
        "input_source": res.get("input_source"),
        "segmentation_fallback_reason": res.get("segmentation_fallback_reason"),
        "note": res.get("note"),
    }
