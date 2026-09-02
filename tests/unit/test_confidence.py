"""Unit tests for devtools.confidence + devtools.region_clustering -- no Ansys."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import confidence as cf  # noqa: E402
from devtools import region_clustering as rc  # noqa: E402

RNG = np.random.default_rng(6)


# --------------------------------------------------------------------------- #
# confidence                                                                 #
# --------------------------------------------------------------------------- #
def test_category_boundaries():
    assert cf.category(0) == "likely convergent"
    assert cf.category(39.9) == "likely convergent"
    assert cf.category(40) == "uncertain"
    assert cf.category(69.9) == "uncertain"
    assert cf.category(70) == "probable singularity"
    assert cf.category(89.9) == "probable singularity"
    assert cf.category(90) == "very strong singularity evidence"
    assert cf.category(100) == "very strong singularity evidence"


def test_pure_mesh_divergence_weighting():
    # one point, full divergence evidence, no secondaries, open gate
    c = cf.compute(np.zeros((1, 3)), np.array([1.0]),
                   s28=0.0, s29=0.0, s32_gate=1.0)
    assert c.hotspot_confidence == pytest.approx(55.0)          # 0.55 * 1 * 100
    assert c.summary()["hotspot_category"] == "uncertain"


def test_secondaries_lift_confidence_at_hotspot():
    coords = np.zeros((1, 3))
    base = cf.compute(coords, np.array([0.8]), s28=0.0, s29=0.0, s32_gate=1.0)
    lifted = cf.compute(coords, np.array([0.8]), s28=1.0, s29=1.0, s32_gate=1.0,
                        geometry_prior=np.array([1.0]))
    assert lifted.hotspot_confidence > base.hotspot_confidence
    # 0.55*0.8 + 1*(0.20*1 + 0.10*1) + 0.15*1 = 0.44 + 0.30 + 0.15 = 0.89
    assert lifted.hotspot_confidence == pytest.approx(89.0, abs=1.0)


def test_sanity_gate_multiplies_down():
    coords = np.zeros((1, 3))
    open_ = cf.compute(coords, np.array([1.0]), s28=1.0, s29=1.0, s32_gate=1.0,
                       geometry_prior=np.array([1.0]))
    gated = cf.compute(coords, np.array([1.0]), s28=1.0, s29=1.0, s32_gate=0.4,
                       geometry_prior=np.array([1.0]))
    assert gated.hotspot_confidence == pytest.approx(0.4 * open_.hotspot_confidence, rel=1e-6)


def test_locality_keeps_secondaries_local():
    # far point gets no S28/S29 contribution
    coords = np.array([[0, 0, 0], [10.0, 0, 0]])
    c = cf.compute(coords, np.array([0.3, 0.3]), s28=1.0, s29=1.0, s32_gate=1.0,
                   hotspot_xyz=np.array([0, 0, 0]), locality_radius_frac=0.05)
    near, far = c.confidence
    assert near > far + 10.0


def test_confidence_clipped_0_100():
    c = cf.compute(np.zeros((1, 3)), np.array([1.0]), s28=1.0, s29=1.0, s32_gate=1.0,
                   geometry_prior=np.array([1.0]))
    assert 0.0 <= c.hotspot_confidence <= 100.0
    assert c.hotspot_confidence == pytest.approx(100.0)         # raw >1 -> clipped


# --------------------------------------------------------------------------- #
# region clustering                                                          #
# --------------------------------------------------------------------------- #
def _two_blobs(n=200, gap=1.0):
    a = RNG.normal([0, 0, 0], 0.02, (n, 3))
    b = RNG.normal([gap, 0, 0], 0.02, (n, 3))
    bg = RNG.random((400, 3)) * np.array([gap, 0.3, 0.3])
    coords = np.vstack([a, b, bg])
    conf = np.concatenate([np.full(n, 88.0), np.full(n, 95.0), np.full(400, 20.0)])
    return coords, conf


def test_two_separated_hot_blobs_give_two_regions():
    coords, conf = _two_blobs()
    regs = rc.cluster(coords, conf, threshold=70.0, link_radius_frac=0.02, min_points=5)
    assert len(regs) == 2
    # sorted by max confidence desc
    assert regs[0].max_confidence >= regs[1].max_confidence
    assert regs[0].region_id == 0 and regs[1].region_id == 1


def test_no_region_when_nothing_flagged():
    coords = RNG.random((300, 3))
    conf = np.full(300, 30.0)
    assert rc.cluster(coords, conf, threshold=70.0) == []


def test_region_reports_size_stress_and_cause():
    coords, conf = _two_blobs(gap=2.0)
    raw = np.concatenate([np.full(200, 12.0), np.full(200, 40.0), np.full(400, 5.0)])
    lam = np.concatenate([np.full(200, 0.45), np.full(200, 0.30), np.full(400, np.nan)])
    re_pts = np.array([[0.0, 0.0, 0.0]])          # a re-entrant edge at blob A
    regs = rc.cluster(coords, conf, threshold=70.0, link_radius_frac=0.02, min_points=5,
                      raw_stress=raw, divergence_exponent=lam, reentrant_pts=re_pts)
    assert len(regs) == 2
    by_centroid = sorted(regs, key=lambda r: r.centroid[0])
    near_edge = by_centroid[0]
    assert near_edge.nearest_reentrant_edge_frac < 0.05
    assert "re-entrant" in near_edge.probable_cause
    assert near_edge.divergence_exponent == pytest.approx(0.45, abs=0.05)
    assert by_centroid[1].max_raw_stress == pytest.approx(40.0)
