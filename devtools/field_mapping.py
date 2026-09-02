"""DPF-agnostic spatial field mapping (Milestone 3 core).

External automation layer -> CPython 3 + numpy/scipy.

The convergence study compares stress at *common physical locations* across
independently remeshed models -- node IDs are meaningless across a remesh
(spec S24).  This module maps a scalar nodal field sampled on one point cloud
onto an arbitrary set of target coordinates, and -- crucially -- reports *how*
each target point was obtained so the error can be quantified rather than
silently swept under a nearest-node fallback (spec S25).

Interior target points: barycentric-linear interpolation on a Delaunay
tetrahedralisation of the source points (exact for a linear field, and exact at
the source points themselves).  Target points outside the source convex hull:
nearest-source value, flagged in ``outside_hull``.

`dpf_adapter` uses an official DPF mapping operator when present and falls back
to this; both paths are validated by `analytic_validation` before any mapped
field feeds classification.
"""

from __future__ import annotations

import dataclasses

import numpy as np

try:  # scipy is a hard dep of the external layer (requirements-dev)
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    from scipy.spatial import cKDTree
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False


@dataclasses.dataclass
class PointField:
    """A scalar field sampled at scattered 3-D points."""
    coords: np.ndarray            # (N, 3) float
    values: np.ndarray            # (N,) float
    name: str = "field"
    unit: str = ""
    location: str = "nodal"

    def __post_init__(self):
        self.coords = np.asarray(self.coords, dtype=float).reshape(-1, 3)
        self.values = np.asarray(self.values, dtype=float).reshape(-1)
        if self.coords.shape[0] != self.values.shape[0]:
            raise ValueError("coords ({0}) and values ({1}) length mismatch".format(
                self.coords.shape[0], self.values.shape[0]))

    @property
    def n(self) -> int:
        return self.values.shape[0]

    def bounds(self):
        lo = self.coords.min(axis=0)
        hi = self.coords.max(axis=0)
        return lo, hi


@dataclasses.dataclass
class MappedField:
    values: np.ndarray            # (M,) mapped scalar
    method: np.ndarray            # (M,) object array of 'linear' | 'nearest' | 'nan'
    outside_hull: np.ndarray      # (M,) bool
    nan_count: int
    source_name: str

    @property
    def m(self) -> int:
        return self.values.shape[0]

    @property
    def fraction_interpolated(self) -> float:
        return float(np.count_nonzero(self.method == "linear")) / max(self.m, 1)


def _require_scipy():
    if not _HAVE_SCIPY:  # pragma: no cover
        raise RuntimeError("scipy is required for field_mapping (pip install -r requirements-dev.txt)")


def map_points_to_points(
    source: PointField,
    target_coords: np.ndarray,
    *,
    fill_outside: str = "nearest",     # 'nearest' | 'nan'
) -> MappedField:
    """Map ``source`` onto ``target_coords`` (M, 3). See module docstring."""
    _require_scipy()
    tgt = np.asarray(target_coords, dtype=float).reshape(-1, 3)
    m = tgt.shape[0]

    # Degenerate source (all points on a plane/line) -> LinearNDInterpolator fails;
    # fall back to pure nearest for everything, flagged.
    try:
        lin = LinearNDInterpolator(source.coords, source.values)
        lin_vals = lin(tgt)
    except Exception:
        lin_vals = np.full(m, np.nan)

    inside = ~np.isnan(lin_vals)
    values = np.array(lin_vals, dtype=float)
    method = np.where(inside, "linear", "nan").astype(object)
    outside = ~inside

    if np.any(outside):
        if fill_outside == "nearest":
            nn = NearestNDInterpolator(source.coords, source.values)
            values[outside] = nn(tgt[outside])
            method[outside] = "nearest"
        elif fill_outside == "nan":
            pass
        else:
            raise ValueError("fill_outside must be 'nearest' or 'nan'")

    nan_count = int(np.count_nonzero(np.isnan(values)))
    return MappedField(values=values, method=method, outside_hull=outside,
                       nan_count=nan_count, source_name=source.name)


def self_consistency(source: PointField) -> dict:
    """Map a field onto its own coordinates; residual should be ~0 (linear
    interpolation reproduces data exactly at the data points)."""
    mp = map_points_to_points(source, source.coords, fill_outside="nearest")
    resid = mp.values - source.values
    return _err_stats(resid, source.values, extra={"nan_count": mp.nan_count})


def _err_stats(resid: np.ndarray, ref: np.ndarray, extra: dict | None = None) -> dict:
    resid = np.asarray(resid, dtype=float)
    finite = np.isfinite(resid)
    r = resid[finite]
    ref = np.asarray(ref, dtype=float)[finite]
    scale = np.maximum(np.abs(ref), np.finfo(float).tiny)
    out = {
        "n": int(resid.size),
        "n_finite": int(r.size),
        "max_abs": float(np.max(np.abs(r))) if r.size else float("nan"),
        "mean_abs": float(np.mean(np.abs(r))) if r.size else float("nan"),
        "rms": float(np.sqrt(np.mean(r ** 2))) if r.size else float("nan"),
        "max_rel": float(np.max(np.abs(r) / scale)) if r.size else float("nan"),
    }
    if extra:
        out.update(extra)
    return out


def analytic_validation(
    func,
    source_coords: np.ndarray,
    target_coords: np.ndarray,
    *,
    name: str = "analytic",
) -> dict:
    """Sample ``func(coords)->values`` on ``source_coords``, map to
    ``target_coords``, compare against the exact ``func(target_coords)``.

    For a linear ``func`` and interior targets this should be ~machine precision.
    """
    src_c = np.asarray(source_coords, dtype=float).reshape(-1, 3)
    tgt_c = np.asarray(target_coords, dtype=float).reshape(-1, 3)
    src = PointField(src_c, np.asarray(func(src_c), dtype=float).reshape(-1), name=name)
    mapped = map_points_to_points(src, tgt_c, fill_outside="nearest")
    exact = np.asarray(func(tgt_c), dtype=float).reshape(-1)

    interior = mapped.method == "linear"
    stats_all = _err_stats(mapped.values - exact, exact)
    stats_interior = _err_stats(
        (mapped.values - exact)[interior], exact[interior]) if np.any(interior) else {}
    return {
        "n_target": int(tgt_c.shape[0]),
        "n_interior": int(np.count_nonzero(interior)),
        "n_outside_hull": int(np.count_nonzero(mapped.outside_hull)),
        "fraction_interpolated": mapped.fraction_interpolated,
        "error_all": stats_all,
        "error_interior": stats_interior,
    }


def nearest_value_error(source: PointField, target_coords: np.ndarray) -> dict:
    """Quantify the error of a pure nearest-source mapping vs the linear one,
    so a nearest fallback is only ever used with its error stated (spec S25)."""
    _require_scipy()
    tgt = np.asarray(target_coords, dtype=float).reshape(-1, 3)
    tree = cKDTree(source.coords)
    _, idx = tree.query(tgt, k=1)
    nearest_vals = source.values[idx]
    linear = map_points_to_points(source, tgt, fill_outside="nan")
    interior = ~np.isnan(linear.values)
    resid = nearest_vals[interior] - linear.values[interior]
    return _err_stats(resid, linear.values[interior],
                      extra={"n_interior_compared": int(np.count_nonzero(interior))})
