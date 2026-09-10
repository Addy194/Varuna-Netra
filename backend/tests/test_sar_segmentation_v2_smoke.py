"""End-to-end smoke test for the optional sar_spill_seg_v2 runtime.

This does NOT test oil-spill accuracy. It creates a tiny random-weight checkpoint
with the production checkpoint schema and verifies that the runtime can load it
and execute dual-polarization inference. Real accuracy is measured only by the
Zenodo Part III evaluation pipeline.
"""
from __future__ import annotations

import numpy as np
import torch

import sar_segmentation_runtime as runtime
from training.sar_unet import UNetSmall


def test_v2_checkpoint_load_and_inference(monkeypatch, tmp_path):
    checkpoint_path = tmp_path / "sar_spill_seg_v2.pt"
    model = UNetSmall(in_channels=2, out_channels=1, base=4)
    torch.save(
        {
            "model_id": "sar_spill_seg_v2",
            "model_type": "unet_semantic_segmentation",
            "model_config": {"in_channels": 2, "out_channels": 1, "base": 4},
            "state_dict": model.state_dict(),
            "input_channels": ["VV", "VH"],
            "input_representation": "Sentinel-1 Sigma0 dB",
            "preprocessing": {"db_min": -50.0, "db_max": 5.0, "tile_size": 32},
            "training": {
                "dataset_dois": ["smoke-test-only"],
                "operational_validation_required": True,
            },
        },
        checkpoint_path,
    )

    monkeypatch.setattr(runtime, "CHECKPOINT", checkpoint_path)
    monkeypatch.setattr(runtime, "MODE", "auto")
    monkeypatch.setattr(runtime, "MAX_PIXELS", 4096)
    monkeypatch.setattr(runtime, "DEFAULT_OVERLAP", 8)
    monkeypatch.setattr(runtime, "_MODEL", None)
    monkeypatch.setattr(runtime, "_CHECKPOINT_DATA", None)
    monkeypatch.setattr(runtime, "_DEVICE", None)
    monkeypatch.setattr(runtime, "_LOAD_ERROR", None)

    # Plausible dB-valued inputs; values are intentionally synthetic because this
    # test validates runtime plumbing, not scientific performance.
    vv = np.full((48, 56), -12.0, dtype=np.float32)
    vh = np.full((48, 56), -20.0, dtype=np.float32)

    probability, meta = runtime.infer_probability(vv, vh, "sigma0_db")

    assert probability.shape == vv.shape
    assert np.isfinite(probability).all()
    assert np.all((probability >= 0.0) & (probability <= 1.0))
    assert meta["input_domain"] == "sigma0_db"
    assert meta["training_domain"] == "sigma0_db"
    assert meta["domain_shift"] is False

    status = runtime.runtime_status()
    assert status["checkpoint_exists"] is True
    assert status["pytorch_available"] is True
    assert status["available"] is True

    info = runtime.model_info()
    assert info["model_id"] == "sar_spill_seg_v2"
    assert info["model_config"]["base"] == 4
