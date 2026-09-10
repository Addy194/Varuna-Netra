"""Safely promote an evaluated sar_spill_seg_v2 checkpoint for runtime use.

Promotion verifies that the independent Part III metrics belong to the exact
checkpoint bytes being installed. This prevents accidentally deploying an
unevaluated or different model under the same filename.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import torch

MODEL_ID = "sar_spill_seg_v2"
TEST_DOI = "10.5281/zenodo.13761290"
TEST_PROTOCOL = "untouched_part_III_full_scene_sliding_window"
REQUIRED_METRICS = ("precision", "recall", "f1", "iou", "dice")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", default="checkpoints/sar_spill_seg_v2.pt")
    p.add_argument("--metrics", default="checkpoints/sar_spill_seg_v2_test_metrics.json")
    p.add_argument("--destination", default="checkpoints/sar_spill_seg_v2.pt")
    p.add_argument("--manifest", default="checkpoints/sar_spill_seg_v2.deployment.json")
    p.add_argument("--min-dice", type=float, default=None,
                   help="Optional project-defined minimum Part III Dice gate")
    p.add_argument("--min-recall", type=float, default=None,
                   help="Optional project-defined minimum Part III recall gate")
    p.add_argument("--max-lookalike-fpr", type=float, default=None,
                   help="Optional maximum look-alike scene false-positive rate")
    p.add_argument("--max-no-oil-fpr", type=float, default=None,
                   help="Optional maximum no-oil scene false-positive rate")
    return p.parse_args()


def require(condition: bool, message: str):
    if not condition:
        raise SystemExit(f"PROMOTION REFUSED: {message}")


def main():
    args = parse_args()
    checkpoint_path = Path(args.checkpoint).expanduser().resolve()
    metrics_path = Path(args.metrics).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()

    require(checkpoint_path.is_file(), f"checkpoint not found: {checkpoint_path}")
    require(metrics_path.is_file(), f"metrics file not found: {metrics_path}")

    checkpoint_hash = sha256_file(checkpoint_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    try:
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        checkpoint = torch.load(checkpoint_path, map_location="cpu")

    require(checkpoint.get("model_id") == MODEL_ID,
            f"checkpoint model_id must be {MODEL_ID!r}, got {checkpoint.get('model_id')!r}")
    require(metrics.get("model_id") == MODEL_ID,
            f"metrics model_id must be {MODEL_ID!r}, got {metrics.get('model_id')!r}")
    require(metrics.get("checkpoint_sha256") == checkpoint_hash,
            "metrics were not produced from these exact checkpoint bytes")
    require(metrics.get("test_dataset_doi") == TEST_DOI,
            f"expected independent Part III DOI {TEST_DOI}")
    require(metrics.get("test_protocol") == TEST_PROTOCOL,
            f"expected test protocol {TEST_PROTOCOL}")

    channels = checkpoint.get("input_channels")
    require(channels == ["VV", "VH"], f"checkpoint must use VV+VH, got {channels!r}")
    require("Sigma0" in str(checkpoint.get("input_representation", "")),
            "checkpoint input representation must declare Sentinel-1 Sigma0")
    require(isinstance(checkpoint.get("state_dict"), dict) and checkpoint["state_dict"],
            "checkpoint has no model state_dict")

    overall = metrics.get("overall") or {}
    for key in REQUIRED_METRICS:
        require(key in overall, f"missing independent-test metric: overall.{key}")
        require(0.0 <= float(overall[key]) <= 1.0, f"overall.{key} must be in [0,1]")

    scene_counts = metrics.get("scene_counts") or {}
    for kind in ("oil", "no_oil", "lookalike"):
        require(int(scene_counts.get(kind, 0)) >= 150,
                f"Part III must include at least 150 {kind} scenes; got {scene_counts.get(kind, 0)}")

    fp_rates = metrics.get("scene_false_positive_rate") or {}
    for kind in ("no_oil", "lookalike"):
        require(kind in fp_rates, f"missing scene false-positive rate for {kind}")
        require(0.0 <= float(fp_rates[kind]) <= 1.0, f"invalid {kind} false-positive rate")

    if args.min_dice is not None:
        require(float(overall["dice"]) >= args.min_dice,
                f"Dice {overall['dice']:.4f} is below required {args.min_dice:.4f}")
    if args.min_recall is not None:
        require(float(overall["recall"]) >= args.min_recall,
                f"recall {overall['recall']:.4f} is below required {args.min_recall:.4f}")
    if args.max_lookalike_fpr is not None:
        require(float(fp_rates["lookalike"]) <= args.max_lookalike_fpr,
                f"look-alike FPR {fp_rates['lookalike']:.4f} exceeds {args.max_lookalike_fpr:.4f}")
    if args.max_no_oil_fpr is not None:
        require(float(fp_rates["no_oil"]) <= args.max_no_oil_fpr,
                f"no-oil FPR {fp_rates['no_oil']:.4f} exceeds {args.max_no_oil_fpr:.4f}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if checkpoint_path != destination:
        shutil.copy2(checkpoint_path, destination)
        installed_hash = sha256_file(destination)
        require(installed_hash == checkpoint_hash, "checkpoint hash changed during copy")

    manifest = {
        "model_id": MODEL_ID,
        "checkpoint": str(destination),
        "checkpoint_sha256": checkpoint_hash,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "independent_test": {
            "dataset_doi": TEST_DOI,
            "protocol": TEST_PROTOCOL,
            "scene_counts": scene_counts,
            "threshold": metrics.get("threshold"),
            "overall": {k: overall[k] for k in REQUIRED_METRICS},
            "scene_false_positive_rate": fp_rates,
        },
        "training": checkpoint.get("training", {}),
        "preprocessing": checkpoint.get("preprocessing", {}),
        "input_channels": channels,
        "input_representation": checkpoint.get("input_representation"),
        "analyst_review_required": True,
        "field_validation_required": True,
        "note": "Promotion verifies independent benchmark evaluation; it is not operational certification.",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("PROMOTION CHECKS PASSED")
    print(f"Checkpoint SHA-256 : {checkpoint_hash}")
    print(f"Installed checkpoint: {destination}")
    print(f"Deployment manifest : {manifest_path}")
    print("\nRuntime configuration:")
    print("  SAR_SEG_ENABLE=auto")
    print(f"  SAR_SEG_CHECKPOINT={destination}")
    print("For Docker builds also set INSTALL_SAR_SEG_RUNTIME=true.")
    print("Analyst review and field validation remain required.")


if __name__ == "__main__":
    main()
