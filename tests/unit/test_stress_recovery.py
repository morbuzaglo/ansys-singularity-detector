"""Unit tests for devtools.stress_recovery (spec S37) -- no Ansys.

Synthetic field: a smooth background with a sharp singular spike at one point;
recovery must pull the spike down toward the background and leave everything
else untouched, or say "Not Recoverable" when there aren't enough donors.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import stress_recovery as sr  # noqa: E402

RNG = np.random.default_rng(37)


def _field(n=2000, h=0.01):
    coords = RNG.random((n, 3)) * (10 * h)
    centre = np.full(3, 5 * h)
    r = np.linalg.norm(coords - centre, axis=1)
    raw = 100.0 + 5.0 * (r / (10 * h))            # smooth background ~100..105
    conf = np.full(n, 10.0)                        # everything converged...
    spike = int(np.argmin(r))
    raw[spike] = 900.0                             # ...except a big spike
    conf[spike] = 95.0
    return coords, raw, conf, h, spike


def test_low_confidence_points_are_left_raw():
    coords, raw, conf, h, spike = _field()
    res = sr.recover(coords, raw, conf, h)
    off = np.arange(len(raw)) != spike
    assert np.array_equal(res.filtered_stress[off], raw[off])
    assert set(res.status[off]) == {"raw"}


def test_spike_is_recovered_toward_background():
    coords, raw, conf, h, spike = _field()
    res = sr.recover(coords, raw, conf, h)
    assert res.status[spike] == "recovered"
    val = res.filtered_stress[spike]
    assert 95.0 < val < 130.0                      # near the local background, not 900
    assert res.n_neighbours_used[spike] >= sr.MIN_NEIGHBOURS


def test_recovery_ignores_other_flagged_points_as_donors():
    coords, raw, conf, h, spike = _field()
    # flag a second nearby point as singular with an absurd value; it must NOT
    # be used as a donor for the first
    r = np.linalg.norm(coords - coords[spike], axis=1)
    near = np.argsort(r)[3]
    conf[near] = 99.0
    raw[near] = 5000.0
    res = sr.recover(coords, raw, conf, h)
    assert res.filtered_stress[spike] < 200.0


def test_outlier_donor_is_rejected():
    coords, raw, conf, h, spike = _field()
    r = np.linalg.norm(coords - coords[spike], axis=1)
    bad = np.argsort(r)[5]                         # a *converged* donor with a crazy value
    raw[bad] = 5000.0
    res = sr.recover(coords, raw, conf, h)
    assert res.filtered_stress[spike] < 150.0      # MAD rejection kept it sane


def test_not_recoverable_when_no_donors_in_radius():
    coords, raw, conf, h, spike = _field(n=300)
    # isolate the spike far from everything, tiny search radius
    coords[spike] = np.array([1e6, 1e6, 1e6])
    res = sr.recover(coords, raw, conf, h, layers=2.5)
    assert res.status[spike] == "not_recoverable"
    assert np.isnan(res.filtered_stress[spike])


def test_not_recoverable_when_too_few_same_body_neighbours():
    coords, raw, conf, h, spike = _field()
    body = np.ones(len(raw), dtype=int)
    body[spike] = 7                                # spike alone on body 7
    res = sr.recover(coords, raw, conf, h, body_id=body)
    assert res.status[spike] == "not_recoverable"


def test_material_constraint_respected():
    coords, raw, conf, h, spike = _field()
    mat = np.ones(len(raw), dtype=int)
    r = np.linalg.norm(coords - coords[spike], axis=1)
    # only a handful of same-material donors, but enough
    mat[:] = 2
    mat[np.argsort(r)[:40]] = 1
    mat[spike] = 1
    res = sr.recover(coords, raw, conf, h, material_id=mat, min_neighbours=6)
    assert res.status[spike] in ("recovered", "not_recoverable")
    if res.status[spike] == "recovered":
        assert res.filtered_stress[spike] < 200.0


def test_result_summary_counts():
    coords, raw, conf, h, spike = _field()
    conf[:5] = 80.0                                # a few more flagged (may or may not recover)
    res = sr.recover(coords, raw, conf, h)
    d = res.as_dict()
    assert d["n_points"] == len(raw)
    assert d["n_raw"] + d["n_recovered"] + d["n_not_recoverable"] == len(raw)
    assert d["n_recovered"] >= 1
