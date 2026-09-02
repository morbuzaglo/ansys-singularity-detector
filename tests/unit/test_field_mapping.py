"""Unit tests for devtools.field_mapping -- no Ansys, no DPF.

Synthetic point clouds; checks that linear fields map exactly, that the
convex-hull boundary is detected, that self-consistency is ~0, and that the
nearest-fallback error is actually quantified.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import field_mapping as fm  # noqa: E402

RNG = np.random.default_rng(1234)


def _cloud(n, lo=(0, 0, 0), hi=(1, 1, 1)):
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    return lo + (hi - lo) * RNG.random((n, 3))


def test_pointfield_shape_validation():
    with pytest.raises(ValueError):
        fm.PointField(np.zeros((5, 3)), np.zeros(4))


def test_linear_field_maps_exactly_interior():
    # f(x,y,z) = 3x - 2y + 0.5z + 7  -- linear -> barycentric interp is exact
    def f(c):
        return 3 * c[:, 0] - 2 * c[:, 1] + 0.5 * c[:, 2] + 7.0

    src = _cloud(400)
    # targets well inside the hull
    tgt = 0.2 + 0.6 * RNG.random((200, 3))
    out = fm.analytic_validation(f, src, tgt)
    assert out["n_outside_hull"] == 0
    assert out["error_interior"]["max_abs"] < 1e-9
    assert out["fraction_interpolated"] == 1.0


def test_outside_hull_flagged_and_filled():
    def f(c):
        return c[:, 0] + c[:, 1] + c[:, 2]

    src = _cloud(300, lo=(0, 0, 0), hi=(1, 1, 1))
    tgt = np.vstack([_cloud(50, (0.3, 0.3, 0.3), (0.7, 0.7, 0.7)),
                     _cloud(50, (2.0, 2.0, 2.0), (3.0, 3.0, 3.0))])  # far outside
    sf = fm.PointField(src, f(src))
    mapped = fm.map_points_to_points(sf, tgt, fill_outside="nearest")
    assert mapped.outside_hull[:50].sum() == 0
    assert mapped.outside_hull[50:].all()
    assert set(np.unique(mapped.method)) <= {"linear", "nearest"}
    assert mapped.nan_count == 0

    mapped_nan = fm.map_points_to_points(sf, tgt, fill_outside="nan")
    assert mapped_nan.nan_count == 50
    assert np.isnan(mapped_nan.values[50:]).all()


def test_self_consistency_is_zero():
    def f(c):
        return np.sin(c[:, 0]) + c[:, 1] ** 2 - c[:, 2]

    src = _cloud(500)
    sf = fm.PointField(src, f(src), name="s")
    stats = fm.self_consistency(sf)
    assert stats["max_abs"] < 1e-9
    assert stats["nan_count"] == 0


def test_quadratic_field_has_bounded_error_that_shrinks_with_density():
    # f = x^2 : linear interp has O(h^2) error; denser source -> smaller error
    def f(c):
        return c[:, 0] ** 2

    tgt = 0.2 + 0.6 * RNG.random((300, 3))
    e_coarse = fm.analytic_validation(f, _cloud(200), tgt)["error_interior"]["rms"]
    e_fine = fm.analytic_validation(f, _cloud(3000), tgt)["error_interior"]["rms"]
    assert e_fine < e_coarse
    assert e_coarse < 0.05          # still small in absolute terms on a unit cube


def test_nearest_fallback_error_is_quantified():
    def f(c):
        return 5 * c[:, 0] - c[:, 1]

    src = _cloud(300)
    sf = fm.PointField(src, f(src))
    tgt = 0.2 + 0.6 * RNG.random((150, 3))
    stats = fm.nearest_value_error(sf, tgt)
    # nearest is worse than linear for a non-constant field -> non-zero, finite
    assert stats["max_abs"] > 0.0
    assert np.isfinite(stats["rms"])
    assert stats["n_interior_compared"] > 0


def test_degenerate_planar_source_does_not_crash():
    # all z = 0 -> Delaunay in 3D is degenerate; expect graceful nearest fill
    c = _cloud(100)
    c[:, 2] = 0.0
    sf = fm.PointField(c, c[:, 0] + c[:, 1])
    mapped = fm.map_points_to_points(sf, _cloud(20), fill_outside="nearest")
    assert mapped.m == 20
    assert np.isfinite(mapped.values).all()
