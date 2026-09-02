"""Unit tests for devtools.build_contours -- synthetic confidence_field.npz, no Ansys."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import build_contours as bc  # noqa: E402

RNG = np.random.default_rng(70)


def _make_study(tmp_path, n=12000):
    d = tmp_path / "study"
    d.mkdir()
    coords = RNG.random((n, 3)) * 100.0            # mm cube
    r = np.linalg.norm(coords - 50.0, axis=1)
    raw = 100.0 + 5.0 * (r / 100.0)
    conf = np.full(n, 8.0)
    spike = int(np.argmin(r))
    raw[spike] = 950.0
    conf[spike] = 92.0
    np.savez_compressed(d / "confidence_field.npz",
                        coords=coords, confidence=conf, raw_stress_finest=raw,
                        evidence=conf / 100, lambda_est=np.full(n, np.nan),
                        geometry_prior=np.zeros(n))
    # node spacing ~ 100 / n**(1/3) mm -> ~4.4 mm; use a consistent element size
    (d / "study_result.json").write_text(json.dumps({
        "levels": [{"status": "ok", "actual_char_size_m": 0.006}]}), encoding="utf-8")
    return d, spike


def test_build_writes_all_outputs(tmp_path):
    d, spike = _make_study(tmp_path)
    s = bc.build(str(d), make_png=False)
    assert (d / "contour_fields.npz").is_file()
    assert (d / "contour_fields.csv").is_file()
    assert (d / "contours_summary.json").is_file()
    assert s["n_nodes"] == 12000
    assert set(s["fields"]) == {bc.RAW_NAME, bc.CONF_NAME, bc.FILT_NAME}


def test_raw_field_is_untouched(tmp_path):
    d, spike = _make_study(tmp_path)
    bc.build(str(d), make_png=False)
    z = np.load(d / "contour_fields.npz", allow_pickle=True)
    src = np.load(d / "confidence_field.npz", allow_pickle=True)
    assert np.array_equal(z["raw_stress"], src["raw_stress_finest"])


def test_filtered_stress_reduces_the_spike(tmp_path):
    d, spike = _make_study(tmp_path)
    s = bc.build(str(d), make_png=False)
    z = np.load(d / "contour_fields.npz", allow_pickle=True)
    filt = z["filtered_stress"]
    assert filt[spike] < 200.0                     # 950 spike pulled down
    assert s["peak_reduction_pct"] > 50.0
    # low-confidence points unchanged
    lowc = z["confidence"] < 70
    assert np.allclose(filt[lowc], z["raw_stress"][lowc], equal_nan=True)


def test_confidence_field_in_0_100(tmp_path):
    d, _ = _make_study(tmp_path)
    bc.build(str(d), make_png=False)
    z = np.load(d / "contour_fields.npz", allow_pickle=True)
    c = z["confidence"]
    assert c.min() >= 0.0 and c.max() <= 100.0


def test_missing_npz_raises(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        bc.build(str(d), make_png=False)


def test_csv_roundtrips_with_visualization_lookup(tmp_path):
    import importlib.util
    d, spike = _make_study(tmp_path)
    bc.build(str(d), make_png=False)
    spec = importlib.util.spec_from_file_location(
        "sd_viz2", Path(__file__).resolve().parents[2] / "extension" / "visualization.py")
    viz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viz)
    fld = viz.load_field(str(d / "contour_fields.csv"))
    src = np.load(d / "confidence_field.npz", allow_pickle=True)
    x = tuple(src["coords"][spike])
    assert fld.value_at(x, "confidence_pct") == pytest.approx(92.0, abs=0.01)
    assert fld.value_at(x, "filtered_stress") < 200.0
