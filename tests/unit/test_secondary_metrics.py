"""Unit tests for the pure secondary metrics -- no Ansys, no DPF.

Covers S30 (hotspot localisation) and S32 (global sanity gate); the DPF-backed
S28/S29/S31 are exercised by tests/mechanical/test_milestone5.py.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import cross_mesh  # noqa: E402
from devtools import secondary_metrics as sm  # noqa: E402

RNG = np.random.default_rng(51)


def _series_localising(n=4000):
    """A field where the hot region SHRINKS as h -> 0 while the peak grows
    (singular pattern)."""
    h = np.array([4e-3, 3e-3, 2.2e-3, 1.6e-3, 1.2e-3])
    coords = RNG.random((n, 3)) * 0.1
    centre = np.array([0.05, 0.05, 0.05])
    r = np.linalg.norm(coords - centre, axis=1)
    mat = np.empty((n, h.size))
    for j, hj in enumerate(h):
        width = 0.06 * (hj / h[0])                 # hot zone width ~ h -> shrinks
        peak = 10.0 * (h[0] / hj) ** 0.3            # peak grows
        mat[:, j] = 3.0 + peak * np.exp(-(r / width) ** 2)
    return cross_mesh.CrossMeshSeries(
        ref_coords=coords, length_unit="m", stress_unit="MPa", sizes_m=list(h),
        matrix=mat, methods=["x"] * 4 + ["identity"],
        outside_any=np.zeros(n, bool), ref_index=h.size - 1)


def _series_stable_profile(n=4000):
    """Hot region keeps a fixed physical size (finite concentration)."""
    h = np.array([4e-3, 3e-3, 2.2e-3, 1.6e-3, 1.2e-3])
    coords = RNG.random((n, 3)) * 0.1
    r = np.linalg.norm(coords - np.array([0.05, 0.05, 0.05]), axis=1)
    mat = np.repeat((3.0 + 7.0 * np.exp(-(r / 0.02) ** 2))[:, None], h.size, axis=1)
    mat += RNG.normal(0, 0.01, mat.shape)
    return cross_mesh.CrossMeshSeries(
        ref_coords=coords, length_unit="m", stress_unit="MPa", sizes_m=list(h),
        matrix=mat, methods=["x"] * 4 + ["identity"],
        outside_any=np.zeros(n, bool), ref_index=h.size - 1)


def test_hotspot_shrinks_scores_high():
    out = sm.hotspot_localization(_series_localising())
    assert out["peak_growing"] is True
    assert out["radius_ratio_last_first"] < 0.8
    assert out["score"] >= 0.5


def test_stable_hotspot_scores_low():
    out = sm.hotspot_localization(_series_stable_profile())
    assert out["score"] < 0.3
    assert 0.7 < out["radius_ratio_last_first"] < 1.4


def _summary(defo, reac, force=1000.0):
    return {
        "force_newtons": force,
        "levels": [
            {"status": "ok", "results": {
                "max_total_deformation": {"value": d},
                "reaction_at_fixed_support": {"value": r}}}
            for d, r in zip(defo, reac)
        ],
    }


def test_global_sanity_stable_solution():
    s = _summary([5.00, 4.99, 4.995, 4.996, 4.996], [1000.0] * 5)
    out = sm.global_sanity(s)
    assert out["solution_stable"] > 0.9
    assert out["notes"] == []


def test_global_sanity_flags_moving_deformation():
    s = _summary([5.0, 6.0, 7.5, 9.0, 11.0], [1000.0] * 5)     # not settling
    out = sm.global_sanity(s)
    assert out["solution_stable"] < 0.7
    assert any("deformation" in n for n in out["notes"])


def test_global_sanity_flags_reaction_imbalance():
    s = _summary([5.0] * 5, [1400.0, 1380.0, 1390.0, 1385.0, 1388.0], force=1000.0)
    out = sm.global_sanity(s)
    assert out["reaction_load_imbalance"] > 0.3
    assert out["solution_stable"] < 0.7


def test_json_safe_roundtrips_numpy():
    d = sm._json_safe({"a": np.float64(1.5), "b": np.array([1, 2]), "c": [np.int64(3)]})
    import json
    assert json.loads(json.dumps(d)) == {"a": 1.5, "b": [1, 2], "c": [3]}
