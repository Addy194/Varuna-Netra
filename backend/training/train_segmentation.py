"""Train sar_spill_seg_v2 on real Sentinel-1 VV/VH scenes.

Example:
python backend/training/train_segmentation.py \
  --oil-images data/part1/01_Train_Val_Oil_Spill_images \
  --oil-masks data/part1/01_Train_Val_Oil_Spill_mask \
  --no-oil-images data/part2/01_Train_Val_No_Oil_Images \
  --lookalike-images data/part2/01_Train_Val_Lookalike_images

The split is performed by complete source scene before cropping, preventing tiles
from the same SAR acquisition leaking into both train and validation sets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from sar_unet import UNetSmall, binary_metrics, dice_loss
from sentinel1_dataset import SentinelOilTileDataset, discover_training_scenes, stratified_scene_split

MODEL_ID = "sar_spill_seg_v2"
DATASET_DOIS = ["10.5281/zenodo.8346860", "10.5281/zenodo.8253899"]


def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def accumulate(total: dict, current: dict):
    for key in ("tp", "fp", "fn"):
        total[key] = total.get(key, 0.0) + current[key]


def metrics_from_counts(counts: dict, eps: float = 1e-8):
    tp, fp, fn = counts.get("tp", 0.0), counts.get("fp", 0.0), counts.get("fn", 0.0)
    precision = tp / max(tp + fp, eps)
    recall = tp / max(tp + fn, eps)
    f1 = 2 * precision * recall / max(precision + recall, eps)
    iou = tp / max(tp + fp + fn, eps)
    dice = 2 * tp / max(2 * tp + fp + fn, eps)
    return {k: round(float(v), 6) for k, v in {
        "precision": precision, "recall": recall, "f1": f1, "iou": iou, "dice": dice
    }.items()}


def run_epoch(model, loader, optimizer, device, train: bool):
    model.train(train)
    bce = nn.BCEWithLogitsLoss()
    losses = []
    counts = {"tp": 0.0, "fp": 0.0, "fn": 0.0}
    grad_context = torch.enable_grad() if train else torch.no_grad()
    with grad_context:
        for batch in loader:
            images = batch["image"].to(device, non_blocking=True)
            masks = batch["mask"].to(device, non_blocking=True)
            logits = model(images)
            loss = 0.5 * bce(logits, masks) + 0.5 * dice_loss(logits, masks)
            if train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
            losses.append(float(loss.detach().cpu()))
            accumulate(counts, binary_metrics(logits.detach(), masks, threshold=0.5))
    return {"loss": round(float(np.mean(losses)) if losses else 0.0, 6), **metrics_from_counts(counts)}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--oil-images", required=True)
    p.add_argument("--oil-masks", required=True)
    p.add_argument("--no-oil-images", required=True)
    p.add_argument("--lookalike-images", required=True)
    p.add_argument("--output", default="checkpoints/sar_spill_seg_v2.pt")
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--tile-size", type=int, default=512)
    p.add_argument("--tiles-per-scene", type=int, default=4)
    p.add_argument("--val-fraction", type=float, default=0.20)
    p.add_argument("--learning-rate", type=float, default=3e-4)
    p.add_argument("--seed", type=int, default=194)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--db-min", type=float, default=-50.0)
    p.add_argument("--db-max", type=float, default=5.0)
    p.add_argument("--patience", type=int, default=7)
    return p.parse_args()


def main():
    args = parse_args()
    seed_everything(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    scenes = discover_training_scenes(
        args.oil_images, args.oil_masks, args.no_oil_images, args.lookalike_images
    )
    train_scenes, val_scenes = stratified_scene_split(scenes, args.val_fraction, args.seed)
    print(json.dumps({
        "device": str(device),
        "total_scenes": len(scenes),
        "train_scenes": len(train_scenes),
        "validation_scenes": len(val_scenes),
        "train_by_class": {k: sum(s.kind == k for s in train_scenes) for k in ("oil", "no_oil", "lookalike")},
        "validation_by_class": {k: sum(s.kind == k for s in val_scenes) for k in ("oil", "no_oil", "lookalike")},
    }, indent=2))

    train_ds = SentinelOilTileDataset(
        train_scenes, args.tile_size, args.tiles_per_scene, 0.70,
        args.db_min, args.db_max, args.seed,
    )
    val_ds = SentinelOilTileDataset(
        val_scenes, args.tile_size, max(1, args.tiles_per_scene // 2), 0.50,
        args.db_min, args.db_max, args.seed + 100_000,
    )
    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True, num_workers=args.workers,
        pin_memory=device.type == "cuda",
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers,
        pin_memory=device.type == "cuda",
    )

    model = UNetSmall(in_channels=2, out_channels=1, base=32).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=2)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    best_dice = -1.0
    stale = 0
    history = []

    for epoch in range(1, args.epochs + 1):
        train_metrics = run_epoch(model, train_loader, optimizer, device, train=True)
        val_metrics = run_epoch(model, val_loader, optimizer, device, train=False)
        scheduler.step(val_metrics["dice"])
        row = {"epoch": epoch, "train": train_metrics, "validation": val_metrics}
        history.append(row)
        print(json.dumps(row))

        if val_metrics["dice"] > best_dice:
            best_dice = val_metrics["dice"]
            stale = 0
            checkpoint = {
                "model_id": MODEL_ID,
                "model_type": "unet_semantic_segmentation",
                "state_dict": model.state_dict(),
                "input_channels": ["VV", "VH"],
                "input_representation": "Sentinel-1 Sigma0 dB",
                "preprocessing": {"db_min": args.db_min, "db_max": args.db_max, "tile_size": args.tile_size},
                "training": {
                    "dataset_dois": DATASET_DOIS,
                    "split_unit": "source_scene",
                    "validation_fraction": args.val_fraction,
                    "seed": args.seed,
                    "train_scenes": len(train_scenes),
                    "validation_scenes": len(val_scenes),
                    "best_validation_metrics": val_metrics,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "operational_validation_required": True,
                },
            }
            torch.save(checkpoint, output)
            output.with_suffix(".json").write_text(json.dumps({k: v for k, v in checkpoint.items() if k != "state_dict"}, indent=2) + "\n")
        else:
            stale += 1
            if stale >= args.patience:
                print(f"Early stopping after {epoch} epochs; best validation Dice={best_dice:.4f}")
                break

    output.with_name(output.stem + "_history.json").write_text(json.dumps(history, indent=2) + "\n")
    print(f"Best checkpoint: {output} (validation Dice={best_dice:.4f})")
    print("Next: evaluate this frozen checkpoint on the untouched Zenodo Part III test set.")


if __name__ == "__main__":
    main()
