"""Evaluate a frozen sar_spill_seg_v2 checkpoint on untouched Zenodo Part III scenes.

Reports pixel-level Dice/IoU/precision/recall plus scene-level false-positive rate
for no-oil and look-alike scenes. Part III must never be used for training or
threshold tuning if these numbers are presented as independent test metrics.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from sar_unet import UNetSmall
from sentinel1_dataset import SceneSample, pair_image_masks, read_mask, read_sar, _tiffs, normalize_db


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--oil-images", required=True)
    p.add_argument("--oil-masks", required=True)
    p.add_argument("--no-oil-images", required=True)
    p.add_argument("--lookalike-images", required=True)
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--overlap", type=int, default=64)
    p.add_argument("--output", default="checkpoints/sar_spill_seg_v2_test_metrics.json")
    return p.parse_args()


def build_scenes(args):
    scenes = [SceneSample(i, m, "oil") for i, m in pair_image_masks(args.oil_images, args.oil_masks)]
    scenes += [SceneSample(i, None, "no_oil") for i in _tiffs(args.no_oil_images)]
    scenes += [SceneSample(i, None, "lookalike") for i in _tiffs(args.lookalike_images)]
    return scenes


def origins(length: int, tile: int, stride: int):
    if length <= tile:
        return [0]
    out = list(range(0, length - tile + 1, stride))
    if out[-1] != length - tile:
        out.append(length - tile)
    return out


@torch.no_grad()
def sliding_probability(model, image, tile_size, overlap, device):
    _, h, w = image.shape
    if h < tile_size or w < tile_size:
        raise ValueError(f"Test scene {h}x{w} is smaller than tile size {tile_size}")
    stride = max(1, tile_size - overlap)
    score = np.zeros((h, w), dtype=np.float32)
    count = np.zeros((h, w), dtype=np.float32)
    for y in origins(h, tile_size, stride):
        for x in origins(w, tile_size, stride):
            tile = torch.from_numpy(image[:, y:y + tile_size, x:x + tile_size]).unsqueeze(0).to(device)
            prob = torch.sigmoid(model(tile))[0, 0].cpu().numpy()
            score[y:y + tile_size, x:x + tile_size] += prob
            count[y:y + tile_size, x:x + tile_size] += 1.0
    return score / np.maximum(count, 1.0)


def update_counts(dst, pred, target):
    pred = pred.astype(bool)
    target = target.astype(bool)
    dst["tp"] += int(np.logical_and(pred, target).sum())
    dst["fp"] += int(np.logical_and(pred, ~target).sum())
    dst["fn"] += int(np.logical_and(~pred, target).sum())


def finish(counts):
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    iou = tp / max(tp + fp + fn, 1)
    dice = 2 * tp / max(2 * tp + fp + fn, 1)
    return {"precision": precision, "recall": recall, "f1": f1, "iou": iou, "dice": dice, **counts}


def main():
    args = parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(args.checkpoint, map_location="cpu")
    preprocessing = checkpoint["preprocessing"]
    tile_size = int(preprocessing["tile_size"])
    model = UNetSmall(in_channels=2, out_channels=1, base=32)
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()

    scenes = build_scenes(args)
    total = {"tp": 0, "fp": 0, "fn": 0}
    by_kind = {k: {"tp": 0, "fp": 0, "fn": 0} for k in ("oil", "no_oil", "lookalike")}
    scene_rows = []
    false_positive_scenes = {"no_oil": 0, "lookalike": 0}
    scene_counts = {"no_oil": 0, "lookalike": 0}

    for n, scene in enumerate(scenes, 1):
        raw = read_sar(scene.image)
        image = normalize_db(raw, preprocessing["db_min"], preprocessing["db_max"])
        target = read_mask(scene.mask, raw.shape[1:])
        prob = sliding_probability(model, image, tile_size, args.overlap, device)
        pred = prob >= args.threshold
        local = {"tp": 0, "fp": 0, "fn": 0}
        update_counts(local, pred, target)
        update_counts(total, pred, target)
        update_counts(by_kind[scene.kind], pred, target)
        predicted_fraction = float(pred.mean())
        if scene.kind in scene_counts:
            scene_counts[scene.kind] += 1
            if pred.any():
                false_positive_scenes[scene.kind] += 1
        scene_rows.append({
            "scene_id": scene.scene_id,
            "kind": scene.kind,
            "predicted_oil_fraction": predicted_fraction,
            **finish(local),
        })
        print(f"[{n}/{len(scenes)}] {scene.scene_id} predicted_oil={predicted_fraction:.5f}")

    results = {
        "model_id": checkpoint.get("model_id"),
        "checkpoint": str(args.checkpoint),
        "test_dataset_doi": "10.5281/zenodo.13761290",
        "test_protocol": "untouched_part_III_full_scene_sliding_window",
        "threshold": args.threshold,
        "overall": finish(total),
        "by_scene_class": {k: finish(v) for k, v in by_kind.items()},
        "scene_false_positive_rate": {
            k: false_positive_scenes[k] / max(scene_counts[k], 1) for k in scene_counts
        },
        "scenes": scene_rows,
        "warning": "Do not tune on Part III and then describe these as independent test metrics.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({k: v for k, v in results.items() if k != "scenes"}, indent=2))


if __name__ == "__main__":
    main()
