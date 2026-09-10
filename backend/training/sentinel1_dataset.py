"""Dataset utilities for the three-part Zenodo Sentinel-1 oil-spill dataset.

Expected source data are the TIFF files published in:
- Part I:  DOI 10.5281/zenodo.8346860 (oil scenes + masks)
- Part II: DOI 10.5281/zenodo.8253899 (no-oil + look-alike scenes)
- Part III: DOI 10.5281/zenodo.13761290 (independent test set)

The key anti-leakage rule is that train/validation splitting happens on complete
scene files. Tiles from one source scene are never allowed into both splits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import re
from typing import Iterable, Optional

import numpy as np
import rasterio
import torch
from torch.utils.data import Dataset

TIFF_SUFFIXES = {".tif", ".tiff"}


@dataclass(frozen=True)
class SceneSample:
    image: Path
    mask: Optional[Path]
    kind: str  # oil | no_oil | lookalike

    @property
    def scene_id(self) -> str:
        return f"{self.kind}:{self.image.stem}"


def _tiffs(root: str | Path | None) -> list[Path]:
    if not root:
        return []
    p = Path(root)
    if not p.exists():
        raise FileNotFoundError(f"Dataset directory does not exist: {p}")
    return sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in TIFF_SUFFIXES)


def _numeric_key(path: Path) -> str:
    # The published dataset pairs imagery and ground truth using the same numeric id.
    groups = re.findall(r"\d+", path.stem)
    if groups:
        return str(int(groups[-1]))
    return path.stem.lower().replace("mask", "").replace("image", "")


def pair_image_masks(image_dir: str | Path, mask_dir: str | Path) -> list[tuple[Path, Path]]:
    images = _tiffs(image_dir)
    masks = _tiffs(mask_dir)
    by_key = {_numeric_key(m): m for m in masks}
    pairs: list[tuple[Path, Path]] = []
    missing: list[str] = []
    for image in images:
        mask = by_key.get(_numeric_key(image))
        if mask is None:
            missing.append(image.name)
        else:
            pairs.append((image, mask))
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Missing masks for {len(missing)} image(s), e.g. {preview}")
    return pairs


def discover_training_scenes(
    oil_images: str | Path,
    oil_masks: str | Path,
    no_oil_images: str | Path | None = None,
    lookalike_images: str | Path | None = None,
) -> list[SceneSample]:
    scenes = [SceneSample(i, m, "oil") for i, m in pair_image_masks(oil_images, oil_masks)]
    scenes += [SceneSample(i, None, "no_oil") for i in _tiffs(no_oil_images)]
    scenes += [SceneSample(i, None, "lookalike") for i in _tiffs(lookalike_images)]
    if not scenes:
        raise ValueError("No Sentinel-1 TIFF scenes found")
    return scenes


def stratified_scene_split(
    scenes: Iterable[SceneSample], validation_fraction: float = 0.2, seed: int = 194
) -> tuple[list[SceneSample], list[SceneSample]]:
    """Split whole scenes by class before any tiles are sampled."""
    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be between 0 and 1")
    rng = random.Random(seed)
    train: list[SceneSample] = []
    val: list[SceneSample] = []
    scenes = list(scenes)
    for kind in sorted({s.kind for s in scenes}):
        group = [s for s in scenes if s.kind == kind]
        rng.shuffle(group)
        n_val = max(1, int(round(len(group) * validation_fraction))) if len(group) > 1 else 0
        val.extend(group[:n_val])
        train.extend(group[n_val:])
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def read_sar(path: Path) -> np.ndarray:
    """Read dual-polarization Sigma0 dB data as CxHxW float32.

    The selected dataset publishes two-channel VV/VH TIFFs. The pipeline uses
    band 1 as VV and band 2 as VH consistently for training and inference.
    """
    with rasterio.open(path) as ds:
        if ds.count < 2:
            raise ValueError(f"Expected VV+VH two-band TIFF, got {ds.count} band(s): {path}")
        arr = ds.read([1, 2]).astype(np.float32)
    return np.nan_to_num(arr, nan=-50.0, posinf=5.0, neginf=-50.0)


def read_mask(path: Optional[Path], shape_hw: tuple[int, int]) -> np.ndarray:
    if path is None:
        return np.zeros(shape_hw, dtype=np.float32)
    with rasterio.open(path) as ds:
        mask = ds.read(1).astype(np.float32)
    if mask.shape != shape_hw:
        raise ValueError(f"Mask/image size mismatch for {path}: {mask.shape} vs {shape_hw}")
    return (mask > 0).astype(np.float32)


def normalize_db(image: np.ndarray, db_min: float = -50.0, db_max: float = 5.0) -> np.ndarray:
    """Preserve physical dB ordering using one fixed transform for train and inference."""
    if db_max <= db_min:
        raise ValueError("db_max must be greater than db_min")
    image = np.clip(image, db_min, db_max)
    return ((image - db_min) / (db_max - db_min)).astype(np.float32)


class SentinelOilTileDataset(Dataset):
    """Random scene tiles with oil-positive oversampling for sparse segmentation masks."""

    def __init__(
        self,
        scenes: list[SceneSample],
        tile_size: int = 512,
        tiles_per_scene: int = 4,
        positive_crop_probability: float = 0.7,
        db_min: float = -50.0,
        db_max: float = 5.0,
        seed: int = 194,
    ):
        self.scenes = scenes
        self.tile_size = tile_size
        self.tiles_per_scene = tiles_per_scene
        self.positive_crop_probability = positive_crop_probability
        self.db_min = db_min
        self.db_max = db_max
        self.seed = seed
        self.epoch = 0
        if not scenes:
            raise ValueError("At least one scene is required")

    def __len__(self):
        return len(self.scenes) * self.tiles_per_scene

    def set_epoch(self, epoch: int):
        """Change deterministic training crops between epochs.

        Calling this on the training dataset before each DataLoader iteration gives
        new crops every epoch while keeping the entire run reproducible by seed.
        Validation can simply remain at epoch 0 for stable model selection.
        """
        self.epoch = int(epoch)

    def _crop_origin(self, mask: np.ndarray, rng: np.random.Generator) -> tuple[int, int]:
        h, w = mask.shape
        t = self.tile_size
        if h < t or w < t:
            raise ValueError(f"Scene {h}x{w} is smaller than requested tile {t}x{t}")
        positives = np.argwhere(mask > 0.5)
        use_positive = positives.size and rng.random() < self.positive_crop_probability
        if use_positive:
            cy, cx = positives[int(rng.integers(0, len(positives)))]
            y = int(np.clip(cy - rng.integers(t // 4, max(t // 4 + 1, 3 * t // 4)), 0, h - t))
            x = int(np.clip(cx - rng.integers(t // 4, max(t // 4 + 1, 3 * t // 4)), 0, w - t))
            return y, x
        return int(rng.integers(0, h - t + 1)), int(rng.integers(0, w - t + 1))

    def __getitem__(self, index: int):
        scene_index = index // self.tiles_per_scene
        repeat_index = index % self.tiles_per_scene
        scene = self.scenes[scene_index]
        rng = np.random.default_rng(
            self.seed
            + self.epoch * 1_000_003
            + scene_index * 1009
            + repeat_index * 9176
        )
        image = read_sar(scene.image)
        mask = read_mask(scene.mask, image.shape[1:])
        y, x = self._crop_origin(mask, rng)
        t = self.tile_size
        image = normalize_db(image[:, y : y + t, x : x + t], self.db_min, self.db_max)
        mask = mask[y : y + t, x : x + t][None, ...]
        return {
            "image": torch.from_numpy(image),
            "mask": torch.from_numpy(mask),
            "scene_id": scene.scene_id,
            "kind": scene.kind,
        }
