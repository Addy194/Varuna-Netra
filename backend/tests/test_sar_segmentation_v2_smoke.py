"""End-to-end smoke tests for the optional sar_spill_seg_v2 runtime.

These tests do NOT measure oil-spill accuracy. They create a tiny random-weight
checkpoint with the production schema and verify runtime loading/inference plus
the deployment promotion guardrails. Real accuracy is measured only by the
Zenodo Part III evaluation pipeline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import torch

import sar_segmentation_runtime as runtime
from training.sar_unet import UNetSmall


def make_checkpoint(path: Path):
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
        path,
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def make_metrics(path: Path, checkpoint_hash: str):
    # Test-only fixture values. These are deliberately not project performance claims.
    path.write_text(
        json.dumps(
            {
                "model_id": "sar_spill_seg_v2",
                "checkpoint_sha256": checkpoint_hash,
                "test_dataset_doi": "10.5281/zenodo.13761290",
                "test_protocol": "untouched_part_III_full_scene_sliding_window",
                "threshold": 0.5,
                "overall": {
                    "precision": 0.5,
                    "recall": 0.5,
                    "f1": 0.5,
                    "iou": 0.5,
                    "dice": 0.5,
                },
                "scene_counts": {"oil": 150, "no_oil": 150, "lookalike": 150},
                "scene_false_positive_rate": {"no_oil": 0.5, "lookalike": 0.5},
            }
        ),
        encoding="utf-8",
    )


def test_v2_checkpoint_load_and_inference(monkeypatch, tmp_path):
    checkpoint_path = tmp_path / "sar_spill_seg_v2.pt"
    make_checkpoint(checkpoint_path)

    monkeypatch.setattr(runtime, "CHECKPOINT", checkpoint_path)
    monkeypatch.setattr(runtime, "MODE", "auto")
    monkeypatch.setattr(runtime, "MAX_PIXELS", 4096)
    monkeypatch.setattr(runtime, "DEFAULT_OVERLAP", 8)
    monkeypatch.setattr(runtime, "_MODEL", None)
    monkeypatch.setattr(runtime, "_CHECKPOINT_DATA", None)
    monkeypatch.setattr(runtime, "_DEVICE", None)
    monkeypatch.setattr(runtime, "_LOAD_ERROR", None)

    # Plausible dB-valued inputs; synthetic because this validates plumbing only.
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


def test_promotion_accepts_exact_evaluated_checkpoint(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    metrics = tmp_path / "metrics.json"
    destination = tmp_path / "active" / "sar_spill_seg_v2.pt"
    manifest = tmp_path / "active" / "sar_spill_seg_v2.deployment.json"
    make_checkpoint(checkpoint)
    make_metrics(metrics, sha256(checkpoint))

    result = subprocess.run(
        [
            sys.executable,
            "training/promote_segmentation_checkpoint.py",
            "--checkpoint", str(checkpoint),
            "--metrics", str(metrics),
            "--destination", str(destination),
            "--manifest", str(manifest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert destination.exists()
    assert sha256(destination) == sha256(checkpoint)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["checkpoint_sha256"] == sha256(checkpoint)
    assert payload["independent_test"]["dataset_doi"] == "10.5281/zenodo.13761290"


def test_promotion_rejects_metrics_from_different_checkpoint(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    metrics = tmp_path / "metrics.json"
    make_checkpoint(checkpoint)
    make_metrics(metrics, "0" * 64)

    result = subprocess.run(
        [
            sys.executable,
            "training/promote_segmentation_checkpoint.py",
            "--checkpoint", str(checkpoint),
            "--metrics", str(metrics),
            "--destination", str(tmp_path / "must-not-exist.pt"),
            "--manifest", str(tmp_path / "must-not-exist.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "PROMOTION REFUSED" in (result.stdout + result.stderr)
    assert not (tmp_path / "must-not-exist.pt").exists()
