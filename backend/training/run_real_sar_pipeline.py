"""One-command real Sentinel-1 training pipeline for Varuna-Netra.

Typical usage after installing backend/training/requirements-ml.txt:

    python backend/training/run_real_sar_pipeline.py

This will:
1. download + MD5-verify + extract Zenodo Parts I, II, III;
2. discover the extracted dataset directories;
3. train sar_spill_seg_v2 on Parts I + II;
4. evaluate the frozen best checkpoint on untouched Part III;
5. write the checkpoint and independent-test metrics under checkpoints/.

Use --stage prepare to download/extract only, --stage train when data are already
prepared, or --stage evaluate to evaluate an existing checkpoint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PREPARE = HERE / "prepare_zenodo_data.py"
TRAIN = HERE / "train_segmentation.py"
EVALUATE = HERE / "evaluate_segmentation.py"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("all", "prepare", "train", "evaluate"), default="all")
    p.add_argument("--data-root", default="data/zenodo_sar")
    p.add_argument("--checkpoint", default="checkpoints/sar_spill_seg_v2.pt")
    p.add_argument("--metrics-output", default="checkpoints/sar_spill_seg_v2_test_metrics.json")
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--tile-size", type=int, default=512)
    p.add_argument("--tiles-per-scene", type=int, default=4)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--learning-rate", type=float, default=3e-4)
    p.add_argument("--val-fraction", type=float, default=0.20)
    p.add_argument("--seed", type=int, default=194)
    p.add_argument("--patience", type=int, default=7)
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--overlap", type=int, default=64)
    p.add_argument("--keep-archives", action="store_true")
    p.add_argument("--skip-verify", action="store_true")
    return p.parse_args()


def run(command: list[str]):
    printable = " ".join(str(x) for x in command)
    print(f"\n$ {printable}\n", flush=True)
    subprocess.run(command, check=True)


def normalized(path: Path) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(path).lower().replace("_", " ").replace("-", " ")))


def tiff_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    counts: dict[Path, int] = {}
    for suffix in ("*.tif", "*.tiff"):
        for file in root.rglob(suffix):
            if file.is_file():
                counts[file.parent] = counts.get(file.parent, 0) + 1
    return [p for p, _ in sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))]


def choose_tiff_dir(root: Path, required: tuple[str, ...], excluded: tuple[str, ...] = ()) -> Path:
    candidates = []
    for path in tiff_dirs(root):
        text = normalized(path)
        if all(token in text for token in required) and not any(token in text for token in excluded):
            count = sum(1 for p in path.iterdir() if p.is_file() and p.suffix.lower() in {".tif", ".tiff"})
            candidates.append((count, path))
    if not candidates:
        available = "\n  ".join(str(p) for p in tiff_dirs(root)[:20]) or "<none>"
        raise FileNotFoundError(
            f"Could not identify a TIFF directory under {root} requiring {required} excluding {excluded}.\n"
            f"Discovered TIFF directories:\n  {available}"
        )
    candidates.sort(key=lambda item: (-item[0], str(item[1])))
    return candidates[0][1]


def discover_layout(data_root: Path) -> dict[str, Path]:
    part1 = data_root / "part1"
    part2 = data_root / "part2"
    part3 = data_root / "part3"

    layout = {
        "train_oil_images": choose_tiff_dir(part1, ("oil", "spill", "images"), ("mask",)),
        "train_oil_masks": choose_tiff_dir(part1, ("oil", "spill", "mask")),
        "train_no_oil_images": choose_tiff_dir(part2, ("no", "oil", "images"), ("mask",)),
        "train_lookalike_images": choose_tiff_dir(part2, ("lookalike", "images"), ("mask",)),
        "test_oil_images": choose_tiff_dir(part3, ("images", "oil"), ("no oil", "lookalike", "mask")),
        "test_oil_masks": choose_tiff_dir(part3, ("mask", "oil"), ("no oil", "lookalike", "images")),
        "test_no_oil_images": choose_tiff_dir(part3, ("images", "no", "oil"), ("mask",)),
        "test_lookalike_images": choose_tiff_dir(part3, ("images", "lookalike"), ("mask",)),
    }
    return layout


def print_environment(data_root: Path):
    data_root.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(data_root).free / (1024 ** 3)
    print(f"Python       : {sys.version.split()[0]}")
    print(f"Data root    : {data_root.resolve()}")
    print(f"Free disk    : {free_gib:.1f} GiB")
    try:
        import torch
        print(f"PyTorch      : {torch.__version__}")
        print(f"CUDA usable  : {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"GPU          : {torch.cuda.get_device_name(0)}")
            memory = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            print(f"GPU memory   : {memory:.1f} GiB")
    except Exception as exc:
        print(f"PyTorch check: unavailable ({exc})")


def prepare(args, data_root: Path):
    command = [sys.executable, str(PREPARE), "--data-root", str(data_root), "--only", "all"]
    if args.keep_archives:
        command.append("--keep-archives")
    if args.skip_verify:
        command.append("--skip-verify")
    run(command)


def train(args, layout: dict[str, Path]):
    run([
        sys.executable,
        str(TRAIN),
        "--oil-images", str(layout["train_oil_images"]),
        "--oil-masks", str(layout["train_oil_masks"]),
        "--no-oil-images", str(layout["train_no_oil_images"]),
        "--lookalike-images", str(layout["train_lookalike_images"]),
        "--output", str(Path(args.checkpoint)),
        "--epochs", str(args.epochs),
        "--batch-size", str(args.batch_size),
        "--tile-size", str(args.tile_size),
        "--tiles-per-scene", str(args.tiles_per_scene),
        "--workers", str(args.workers),
        "--learning-rate", str(args.learning_rate),
        "--val-fraction", str(args.val_fraction),
        "--seed", str(args.seed),
        "--patience", str(args.patience),
    ])


def evaluate(args, layout: dict[str, Path]):
    checkpoint = Path(args.checkpoint)
    if not checkpoint.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    run([
        sys.executable,
        str(EVALUATE),
        "--checkpoint", str(checkpoint),
        "--oil-images", str(layout["test_oil_images"]),
        "--oil-masks", str(layout["test_oil_masks"]),
        "--no-oil-images", str(layout["test_no_oil_images"]),
        "--lookalike-images", str(layout["test_lookalike_images"]),
        "--threshold", str(args.threshold),
        "--overlap", str(args.overlap),
        "--output", str(Path(args.metrics_output)),
    ])


def main():
    args = parse_args()
    data_root = Path(args.data_root).expanduser()
    print("Varuna-Netra real Sentinel-1 training pipeline")
    print("=" * 49)
    print_environment(data_root)

    if args.stage in {"all", "prepare"}:
        prepare(args, data_root)
        if args.stage == "prepare":
            print("\nPreparation finished. Re-run with --stage train when ready to train.")
            return

    layout = discover_layout(data_root)
    print("\nDiscovered dataset layout:")
    print(json.dumps({k: str(v) for k, v in layout.items()}, indent=2))

    if args.stage in {"all", "train"}:
        train(args, layout)
        if args.stage == "train":
            print(f"\nTraining finished. Best checkpoint: {args.checkpoint}")
            return

    if args.stage in {"all", "evaluate"}:
        evaluate(args, layout)
        print("\nPipeline complete.")
        print(f"Checkpoint : {Path(args.checkpoint).resolve()}")
        print(f"Test metrics: {Path(args.metrics_output).resolve()}")
        print("Only quote independent Part III metrics from the generated metrics JSON in SIH materials.")


if __name__ == "__main__":
    main()
