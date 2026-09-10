"""Offline tests for windowed SAR COG reads used by sar_spill_seg_v2."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import detector
from sar_segmentation_runtime import IncompatibleSARInput


def write_test_raster(path):
    data = np.arange(100 * 100, dtype=np.float32).reshape(100, 100)
    # WGS84 raster covering lon 70..71, lat 19..20 at 0.01 degree pixels.
    transform = from_origin(70.0, 20.0, 0.01, 0.01)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as ds:
        ds.write(data, 1)
    return data


def test_analysis_bbox_reads_only_requested_window(tmp_path, monkeypatch):
    raster = tmp_path / "vv.tif"
    write_test_raster(raster)
    monkeypatch.setattr(detector, "SEG_MAX_PIXELS", 5000)

    arr, crs, transform, bbox = detector._read_remote_band(
        str(raster), [70.2, 19.4, 70.6, 19.8]
    )

    assert arr.shape == (40, 40)
    assert crs == "EPSG:4326"
    assert transform.a == pytest.approx(0.01)
    assert bbox == pytest.approx([70.2, 19.4, 70.6, 19.8], abs=0.011)


def test_full_oversized_scene_requires_analysis_bbox(tmp_path, monkeypatch):
    raster = tmp_path / "vv.tif"
    write_test_raster(raster)
    monkeypatch.setattr(detector, "SEG_MAX_PIXELS", 1000)

    with pytest.raises(IncompatibleSARInput, match="register the scene with analysis_bbox"):
        detector._read_remote_band(str(raster))

    arr, _, _, _ = detector._read_remote_band(
        str(raster), [70.2, 19.4, 70.4, 19.6]
    )
    assert arr.size <= 1000
