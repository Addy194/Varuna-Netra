import importlib

import numpy as np
import pytest

import sar_segmentation_runtime as runtime


def test_db_conversion_and_normalization():
    linear = np.array([[1.0, 0.1], [0.01, 0.001]], dtype=np.float32)
    db = runtime.to_db(linear, "sigma0_linear")
    assert np.allclose(db, [[0.0, -10.0], [-20.0, -30.0]], atol=1e-4)
    scaled = runtime.normalize_db(db, -50.0, 5.0)
    assert scaled.shape == linear.shape
    assert np.all((scaled >= 0.0) & (scaled <= 1.0))


def test_rejects_raw_unknown_radiometry():
    with pytest.raises(runtime.IncompatibleSARInput):
        runtime.to_db(np.ones((2, 2), dtype=np.float32), "raw_grd_amplitude")


def test_missing_checkpoint_reports_safe_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "CHECKPOINT", tmp_path / "missing.pt")
    monkeypatch.setattr(runtime, "MODE", "auto")
    monkeypatch.setattr(runtime, "_LOAD_ERROR", None)
    status = runtime.runtime_status()
    assert status["available"] is False
    assert status["checkpoint_exists"] is False
    assert "checkpoint_not_found" in status["reason"]
