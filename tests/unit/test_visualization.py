"""Unit tests for extension/visualization.py's pure-Python parts.

The grid nearest-node lookup runs fine under CPython 3 (no numpy, no Mechanical);
only the .NET/ACT callbacks need the engine.
"""

import csv
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]

# load extension/visualization.py directly (it's IronPython-flavoured but the
# helper classes are plain Python)
_spec = importlib.util.spec_from_file_location(
    "sd_visualization", REPO / "extension" / "visualization.py")
viz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(viz)

RNG = np.random.default_rng(7)


def _write_csv(path, coords, raw, conf, filt, status):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "z", "raw_stress", "confidence_pct",
                    "filtered_stress", "recovery_status"])
        for i in range(len(coords)):
            w.writerow([coords[i, 0], coords[i, 1], coords[i, 2],
                        raw[i], conf[i],
                        "" if not np.isfinite(filt[i]) else filt[i], status[i]])


def test_nearest_lookup_returns_exact_node_value(tmp_path):
    coords = RNG.random((500, 3)) * 100.0
    raw = RNG.random(500) * 50 + 10
    conf = RNG.random(500) * 100
    filt = raw.copy()
    status = np.array(["raw"] * 500)
    p = tmp_path / "contour_fields.csv"
    _write_csv(p, coords, raw, conf, filt, status)

    fld = viz.load_field(str(p))
    # querying exactly at a node returns that node's value
    for i in RNG.choice(500, 20, replace=False):
        assert fld.value_at(tuple(coords[i]), "raw_stress") == pytest.approx(raw[i])
        assert fld.value_at(tuple(coords[i]), "confidence_pct") == pytest.approx(conf[i])


def test_nearest_lookup_between_nodes(tmp_path):
    coords = np.array([[0, 0, 0], [10.0, 0, 0], [0, 10.0, 0], [0, 0, 10.0]])
    raw = np.array([1.0, 2.0, 3.0, 4.0])
    p = tmp_path / "contour_fields.csv"
    _write_csv(p, coords, raw, raw, raw, np.array(["raw"] * 4))
    fld = viz.load_field(str(p))
    assert fld.value_at((1.0, 0.2, 0.2), "raw_stress") == pytest.approx(1.0)
    assert fld.value_at((9.0, 0.1, 0.1), "raw_stress") == pytest.approx(2.0)


def test_nan_filtered_value_reads_back_as_nan(tmp_path):
    coords = RNG.random((50, 3)) * 10
    raw = RNG.random(50) + 1
    filt = raw.copy()
    filt[7] = np.nan
    status = np.array(["raw"] * 50)
    status[7] = "not_recoverable"
    p = tmp_path / "contour_fields.csv"
    _write_csv(p, coords, raw, raw * 0 + 5, filt, status)
    fld = viz.load_field(str(p))
    v = fld.value_at(tuple(coords[7]), "filtered_stress")
    assert v != v          # NaN


def test_load_field_missing_file_raises(tmp_path):
    with pytest.raises(IOError):
        viz.load_field(str(tmp_path / "nope.csv"))


def test_field_cache_is_reused(tmp_path):
    coords = RNG.random((30, 3))
    p = tmp_path / "contour_fields.csv"
    _write_csv(p, coords, np.ones(30), np.ones(30) * 5, np.ones(30), np.array(["raw"] * 30))
    a = viz.load_field(str(p))
    b = viz.load_field(str(p))
    assert a is b
