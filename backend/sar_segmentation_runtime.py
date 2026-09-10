"""Optional runtime inference for the real-data Sentinel-1 segmentation model.

The FastAPI service must remain usable without PyTorch or a trained checkpoint.
This module therefore imports PyTorch lazily and reports a structured reason when
sar_spill_seg_v2 cannot be used. Only compatible dual-polarization radiometry is
accepted; raw GRD amplitude is deliberately rejected instead of silently feeding
an out-of-domain tensor to the model.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

MODEL_ID = "sar_spill_seg_v2"
DETECTOR_VERSION = "sar-unet-vv-vh-2.0.0"
DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[1] / "checkpoints" / "sar_spill_seg_v2.pt"
CHECKPOINT = Path(os.getenv("SAR_SEG_CHECKPOINT", str(DEFAULT_CHECKPOINT))).expanduser()
MODE = os.getenv("SAR_SEG_ENABLE", "auto").strip().lower()  # auto | off | force
MAX_PIXELS = int(os.getenv("SAR_SEG_MAX_PIXELS", str(4096 * 4096)))
DEFAULT_THRESHOLD = float(os.getenv("SAR_SEG_THRESHOLD", "0.5"))
DEFAULT_OVERLAP = int(os.getenv("SAR_SEG_OVERLAP", "64"))

_MODEL = None
_CHECKPOINT_DATA: dict[str, Any] | None = None
_DEVICE = None
_LOAD_ERROR: str | None = None


class SegmentationUnavailable(RuntimeError):
    pass


class IncompatibleSARInput(ValueError):
    pass


def runtime_status() -> dict[str, Any]:
    exists = CHECKPOINT.is_file()
    enabled = MODE != "off"
    torch_available = False
    torch_error = None
    if enabled and exists:
        try:
            import torch  # noqa: F401
            torch_available = True
        except Exception as exc:  # noqa: BLE001
            torch_error = f"{type(exc).__name__}: {exc}"
    available = enabled and exists and torch_available and _LOAD_ERROR is None
    reason = None
    if MODE == "off":
        reason = "SAR_SEG_ENABLE=off"
    elif not exists:
        reason = f"checkpoint_not_found:{CHECKPOINT}"
    elif not torch_available:
        reason = f"pytorch_unavailable:{torch_error}"
    elif _LOAD_ERROR:
        reason = f"checkpoint_load_failed:{_LOAD_ERROR}"
    return {
        "model_id": MODEL_ID,
        "detector_version": DETECTOR_VERSION,
        "mode": MODE,
        "checkpoint": str(CHECKPOINT),
        "checkpoint_exists": exists,
        "pytorch_available": torch_available,
        "available": available,
        "loaded": _MODEL is not None,
        "reason": reason,
    }


def _load_model():
    global _MODEL, _CHECKPOINT_DATA, _DEVICE, _LOAD_ERROR
    if _MODEL is not None:
        return _MODEL, _CHECKPOINT_DATA, _DEVICE
    status = runtime_status()
    if not status["available"]:
        raise SegmentationUnavailable(status["reason"] or "segmentation runtime unavailable")
    try:
        import torch
        from training.sar_unet import UNetSmall

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        try:
            checkpoint = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
        except TypeError:
            checkpoint = torch.load(CHECKPOINT, map_location="cpu")
        if checkpoint.get("model_id") != MODEL_ID:
            raise ValueError(f"unexpected model_id={checkpoint.get('model_id')!r}")
        cfg = checkpoint.get("model_config") or checkpoint.get("architecture") or {}
        in_channels = int(cfg.get("in_channels", 2))
        out_channels = int(cfg.get("out_channels", 1))
        base = int(cfg.get("base", cfg.get("base_channels", 32)))
        if in_channels != 2 or out_channels != 1:
            raise ValueError(f"unsupported model_config={cfg!r}")
        model = UNetSmall(in_channels=in_channels, out_channels=out_channels, base=base)
        model.load_state_dict(checkpoint["state_dict"])
        model.to(device).eval()
        _MODEL, _CHECKPOINT_DATA, _DEVICE = model, checkpoint, device
        return _MODEL, _CHECKPOINT_DATA, _DEVICE
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        if MODE == "force":
            raise
        raise SegmentationUnavailable(_LOAD_ERROR) from exc


def warmup() -> dict[str, Any]:
    """Validate and load v2 at service startup when it is configured and available."""
    status = runtime_status()
    if not status["available"]:
        if MODE == "force":
            raise SegmentationUnavailable(status["reason"] or "segmentation runtime unavailable")
        return status
    _load_model()
    return runtime_status()


def model_info() -> dict[str, Any]:
    status = runtime_status()
    info: dict[str, Any] = {
        **status,
        "model_type": "unet_semantic_segmentation",
        "input_channels": ["VV", "VH"],
        "expected_training_representation": "Sentinel-1 Sigma0 dB",
        "operational_validation_required": True,
    }
    if _CHECKPOINT_DATA:
        info["preprocessing"] = _CHECKPOINT_DATA.get("preprocessing")
        info["training"] = _CHECKPOINT_DATA.get("training")
        info["model_config"] = _CHECKPOINT_DATA.get("model_config") or _CHECKPOINT_DATA.get("architecture")
    return info


def to_db(arr: np.ndarray, domain: str) -> np.ndarray:
    x = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    if domain == "sigma0_db":
        return x
    if domain in {"sigma0_linear", "gamma0_linear_rtc"}:
        return 10.0 * np.log10(np.maximum(x, 1e-8))
    if domain == "gamma0_db_rtc":
        return x
    raise IncompatibleSARInput(f"unsupported_sar_value_domain:{domain}")


def normalize_db(arr: np.ndarray, db_min: float, db_max: float) -> np.ndarray:
    return ((np.clip(arr, db_min, db_max) - db_min) / max(db_max - db_min, 1e-6)).astype(np.float32)


def _origins(length: int, tile: int, stride: int) -> list[int]:
    if length <= tile:
        return [0]
    out = list(range(0, length - tile + 1, stride))
    if out[-1] != length - tile:
        out.append(length - tile)
    return out


def infer_probability(vv: np.ndarray, vh: np.ndarray, domain: str) -> tuple[np.ndarray, dict[str, Any]]:
    """Return an HxW oil probability map and runtime metadata."""
    if vv.shape != vh.shape or vv.ndim != 2:
        raise IncompatibleSARInput(f"vv_vh_shape_mismatch:{vv.shape}:{vh.shape}")
    h, w = vv.shape
    if h * w > MAX_PIXELS:
        raise IncompatibleSARInput(
            f"scene_too_large_for_runtime:{h}x{w}; prepare/crop an AOI or raise SAR_SEG_MAX_PIXELS"
        )

    model, checkpoint, device = _load_model()
    import torch

    prep = checkpoint.get("preprocessing") or {}
    tile = int(prep.get("tile_size", 512))
    db_min = float(prep.get("db_min", -50.0))
    db_max = float(prep.get("db_max", 5.0))
    threshold = float(os.getenv("SAR_SEG_THRESHOLD", str(checkpoint.get("decision_threshold", DEFAULT_THRESHOLD))))
    overlap = min(DEFAULT_OVERLAP, max(tile // 2 - 1, 0))

    vv_db, vh_db = to_db(vv, domain), to_db(vh, domain)
    image = np.stack([normalize_db(vv_db, db_min, db_max), normalize_db(vh_db, db_min, db_max)])

    pad_h, pad_w = max(0, tile - h), max(0, tile - w)
    if pad_h or pad_w:
        image = np.pad(image, ((0, 0), (0, pad_h), (0, pad_w)), mode="edge")
    _, hp, wp = image.shape
    stride = max(1, tile - overlap)
    score = np.zeros((hp, wp), np.float32)
    count = np.zeros((hp, wp), np.float32)

    autocast_enabled = getattr(device, "type", "cpu") == "cuda"
    with torch.inference_mode():
        for y in _origins(hp, tile, stride):
            for x in _origins(wp, tile, stride):
                t = torch.from_numpy(image[:, y:y + tile, x:x + tile]).unsqueeze(0).to(device)
                with torch.autocast(device_type="cuda", enabled=autocast_enabled):
                    p = torch.sigmoid(model(t))[0, 0].float().cpu().numpy()
                score[y:y + tile, x:x + tile] += p
                count[y:y + tile, x:x + tile] += 1.0

    probability = (score / np.maximum(count, 1.0))[:h, :w]
    meta = {
        "threshold": threshold,
        "input_domain": domain,
        "training_domain": "sigma0_db",
        "domain_shift": domain.startswith("gamma0"),
        "device": str(device),
        "tile_size": tile,
        "overlap": overlap,
        "db_min": db_min,
        "db_max": db_max,
    }
    return probability, meta
