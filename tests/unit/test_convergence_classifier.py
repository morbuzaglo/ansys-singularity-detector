"""Synthetic-data unit tests for devtools.convergence_classifier (spec S42).

No Ansys.  These MUST pass before the classifier is pointed at solver data.
The mesh sequence is the project default: h0 * 0.75**k.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import convergence_classifier as cc  # noqa: E402


def _h(n=5, h0=1.0, r=0.75):
    return h0 * r ** np.arange(n)


# --------------------------------------------------------------------------- #
# convergent (non-singular) cases                                             #
# --------------------------------------------------------------------------- #
def test_quadratic_convergent_is_non_singular():
    h = _h(6)
    s = 100.0 + 15.0 * h ** 2                     # spec S42 example
    v = cc.classify_series(h, s)
    assert v.classification == "convergent"
    assert v.singularity_evidence < 0.3
    assert v.divergence_exponent is None
    assert v.extrapolated_limit == pytest.approx(100.0, abs=1.0)
    assert v.limit_is_trustworthy


def test_linear_p1_convergent():
    h = _h(6)
    s = 250.0 + 40.0 * h ** 1.0
    v = cc.classify_series(h, s)
    assert v.classification == "convergent"
    assert v.extrapolated_limit == pytest.approx(250.0, abs=2.0)


def test_nearly_constant_strong_convergence():
    h = _h(5)
    s = np.full_like(h, 77.0)
    v = cc.classify_series(h, s)
    assert v.classification == "convergent"
    assert v.singularity_evidence < 0.15


def test_stress_concentration_converges_to_finite_kt():
    # plate-with-hole-like: high but finite; sigma_inf = 3*nominal
    h = _h(6)
    s = 30.0 - 8.0 * h ** 1.5
    v = cc.classify_series(h, s)
    assert v.classification == "convergent"
    assert v.extrapolated_limit == pytest.approx(30.0, abs=1.0)


# --------------------------------------------------------------------------- #
# singular cases                                                              #
# --------------------------------------------------------------------------- #
def test_power_law_divergent_is_singular():
    h = _h(6)
    s = 20.0 + 5.0 * h ** -0.5                    # spec S42 example
    v = cc.classify_series(h, s)
    assert v.classification == "singular"
    assert v.singularity_evidence >= 0.7
    assert v.divergence_exponent == pytest.approx(0.5, abs=0.2)
    assert not v.limit_is_trustworthy


def test_pure_power_law_crack_tip():
    h = _h(6)
    s = 8.0 * h ** -0.3
    v = cc.classify_series(h, s)
    assert v.classification == "singular"
    assert v.divergence_exponent == pytest.approx(0.3, abs=0.2)


def test_reentrant_corner_lambda_045():
    # re-entrant 90-degree corner: lambda ~ 0.4555 for mode I
    h = _h(7)
    s = 12.0 + 3.2 * h ** -0.4555
    v = cc.classify_series(h, s)
    assert v.classification == "singular"
    assert v.divergence_exponent == pytest.approx(0.4555, abs=0.2)


# --------------------------------------------------------------------------- #
# noisy / borderline                                                         #
# --------------------------------------------------------------------------- #
def test_noisy_convergent_not_flagged_singular():
    rng = np.random.default_rng(0)
    fails = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        h = _h(6)
        s = 100.0 + 15.0 * h ** 2 + rng.normal(0, 0.4, h.size)
        v = cc.classify_series(h, s)
        if v.classification == "singular":
            fails += 1
    assert fails == 0, "{0}/20 noisy-convergent series misread as singular".format(fails)


def test_noisy_singular_flagged_and_never_called_convergent():
    hits = wrong = 0
    for seed in range(20):
        rng = np.random.default_rng(100 + seed)
        h = _h(6)
        s = 20.0 + 5.0 * h ** -0.5 + rng.normal(0, 0.2, h.size)
        v = cc.classify_series(h, s)
        hits += (v.classification == "singular")
        wrong += (v.classification == "convergent")
    assert wrong == 0, "{0}/20 noisy-singular series misread as CONVERGENT".format(wrong)
    assert hits >= 12, "only {0}/20 noisy-singular flagged singular (rest uncertain)".format(hits)


# --------------------------------------------------------------------------- #
# guards                                                                     #
# --------------------------------------------------------------------------- #
def test_two_levels_is_insufficient():
    v = cc.classify_series([1.0, 0.75], [10.0, 11.0])
    assert v.classification == "insufficient_data"
    assert v.singularity_evidence == 0.0


def test_tiny_mesh_range_is_insufficient():
    h = np.array([1.0, 0.98, 0.96, 0.94])
    s = 10.0 + 5.0 * h ** -0.5
    v = cc.classify_series(h, s)
    assert v.classification == "insufficient_data"


def test_three_levels_runs_without_fit():
    h = _h(3)
    s = 20.0 + 5.0 * h ** -0.5
    v = cc.classify_series(h, s)
    assert v.classification in {"singular", "uncertain"}
    assert v.finite_fit is None and v.divergent_fit is None


def test_nan_entries_are_dropped():
    h = _h(6)
    s = 100.0 + 15.0 * h ** 2
    s[2] = np.nan
    v = cc.classify_series(h, s)
    assert v.classification == "convergent"
    assert v.evidence["n_levels"] == 5


# --------------------------------------------------------------------------- #
# separation requirement (spec Milestone 4: "clearly separate")               #
# --------------------------------------------------------------------------- #
def test_plate_vs_corner_clearly_separate():
    h = _h(6)
    plate = cc.classify_series(h, 30.0 - 8.0 * h ** 1.5)
    corner = cc.classify_series(h, 12.0 + 3.2 * h ** -0.4555)
    assert plate.classification == "convergent"
    assert corner.classification == "singular"
    assert corner.singularity_evidence - plate.singularity_evidence > 0.5


def test_fits_and_increment_analysis_shapes():
    h = _h(6)
    s = 20.0 + 5.0 * h ** -0.5
    fin = cc.fit_finite_limit(h, s)
    div = cc.fit_divergent(h, s)
    assert div.rel_resid < fin.rel_resid          # divergent explains it better
    inc = cc.increment_analysis(h, s)
    assert inc["mean_increment_ratio"] > 1.0      # increments grow as h -> 0
