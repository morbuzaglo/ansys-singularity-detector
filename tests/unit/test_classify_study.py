"""Unit tests for the series-level classifiers -- no Ansys, no DPF.

A synthetic CrossMeshSeries where a small cluster of points near one corner
carries a divergent stress trend while the bulk converges: the hotspot-
neighbourhood classifier must find the singularity; the bulk (p99) view must
not be dominated by a <1% cluster.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import convergence_classifier as cc  # noqa: E402
from devtools import cross_mesh  # noqa: E402

RNG = np.random.default_rng(20260902)


def _synthetic_series(n=6000, n_sing=25):
    h = np.array([4e-3, 3e-3, 2.2e-3, 1.6e-3, 1.2e-3])          # decreasing, index0=coarsest
    coords = RNG.random((n, 3)) * np.array([0.2, 0.02, 0.02])   # a bar-ish box
    corner = np.array([0.0, 0.0, 0.0])
    d = np.linalg.norm(coords - corner, axis=1)
    sing_idx = np.argsort(d)[:n_sing]                            # closest to the corner

    hn = h / h[0]                                    # normalised so terms are O(1)
    mat = 10.0 + 1.5 * hn[None, :] ** 1.5 + RNG.normal(0, 0.02, (n, h.size))   # bulk: -> 10
    if n_sing:
        mat[sing_idx] = 9.0 + 2.5 * hn[None, :] ** -0.45 + RNG.normal(0, 0.03, (n_sing, h.size))

    return cross_mesh.CrossMeshSeries(
        ref_coords=coords, length_unit="m", stress_unit="MPa",
        sizes_m=list(h), matrix=mat,
        methods=["dpf_on_coordinates"] * 4 + ["identity"],
        outside_any=np.zeros(n, bool), ref_index=h.size - 1,
    )


def test_neighborhood_finds_corner_singularity():
    s = _synthetic_series()
    v = cc.classify_hotspot_neighborhood(s)
    assert v.classification == "singular"
    assert v.singularity_evidence >= 0.7
    assert v.divergence_exponent == pytest.approx(0.45, abs=0.2)
    assert not v.limit_is_trustworthy


def test_bulk_p99_not_dominated_by_tiny_cluster():
    s = _synthetic_series(n=6000, n_sing=20)   # cluster ~0.3% -> below the 99th pct
    v = cc.classify_hotspot(s)
    assert v.classification == "convergent"
    assert v.extrapolated_limit == pytest.approx(10.0, abs=0.6)


def test_classify_series_obj_reports_both_and_field():
    from devtools import classify_study
    s = _synthetic_series()
    out = classify_study.classify_series_obj(s, field_sample=3000)
    assert out["hotspot_neighborhood"]["classification"] == "singular"
    assert out["bulk_p99"]["classification"] == "convergent"
    assert 0.0 <= out["field"]["singular_fraction"] < 0.2      # only a small cluster
    assert out["field"]["class_counts"]["convergent"] > out["field"]["class_counts"]["singular"]
    assert out["n_valid"] == s.m


def test_all_converging_series_reads_convergent_everywhere():
    s = _synthetic_series(n=2000, n_sing=0)
    assert cc.classify_hotspot_neighborhood(s).classification == "convergent"
    assert cc.classify_hotspot(s).classification == "convergent"
