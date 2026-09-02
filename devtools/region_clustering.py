"""Singular-region clustering (Milestone 6, spec S34).

External automation layer -> CPython 3 + numpy/scipy.

Flagged locations (confidence >= threshold) are grouped into spatially connected
REGIONS so the result is "one re-entrant corner", not 3000 separate nodes.  For
each region a compact report is produced (spec S34):

    Region ID, point count, max/mean confidence, centroid, physical size,
    max raw stress, divergence exponent, nearest geometry feature,
    nearest BC/load face, probable cause.
"""

from __future__ import annotations

import dataclasses

import numpy as np

try:
    from scipy.spatial import cKDTree
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False


@dataclasses.dataclass
class Region:
    region_id: int
    n_points: int
    max_confidence: float
    mean_confidence: float
    centroid: list
    size_frac: float                 # RMS radius / model diagonal
    bbox_size: list                  # physical extent (same units as coords)
    max_raw_stress: float | None
    divergence_exponent: float | None
    nearest_reentrant_edge_frac: float | None
    nearest_bc_face_frac: float | None
    probable_cause: str

    def as_dict(self):
        return dataclasses.asdict(self)


def _connected_components(points, link_radius):
    """Union-find over a KD-tree radius graph -> integer labels."""
    n = points.shape[0]
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    tree = cKDTree(points)
    for a, b in tree.query_pairs(link_radius):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(n)])
    _, labels = np.unique(roots, return_inverse=True)
    return labels


def cluster(
    coords: np.ndarray,
    confidence: np.ndarray,
    *,
    threshold: float = 70.0,
    link_radius_frac: float = 0.03,
    min_points: int = 4,
    raw_stress: np.ndarray | None = None,
    divergence_exponent: np.ndarray | None = None,
    reentrant_pts: np.ndarray | None = None,
    bc_centroids: np.ndarray | None = None,
) -> list[Region]:
    if not _HAVE_SCIPY:  # pragma: no cover
        raise RuntimeError("scipy required for region clustering")
    coords = np.asarray(coords, float).reshape(-1, 3)
    conf = np.asarray(confidence, float).reshape(-1)
    diag = float(np.linalg.norm(coords.max(0) - coords.min(0))) or 1.0

    flag = conf >= threshold
    if flag.sum() == 0:
        return []
    pts = coords[flag]
    labels = _connected_components(pts, link_radius_frac * diag)

    re_tree = cKDTree(reentrant_pts) if (reentrant_pts is not None and len(reentrant_pts)) else None
    bc_tree = cKDTree(bc_centroids) if (bc_centroids is not None and len(bc_centroids)) else None

    idx_all = np.where(flag)[0]
    regions = []
    rid = 0
    for lab in range(labels.max() + 1):
        m = labels == lab
        if m.sum() < min_points:
            continue
        sub = idx_all[m]
        c = coords[sub]
        cf = conf[sub]
        centre = c.mean(0)
        rms_r = float(np.sqrt(np.mean(np.sum((c - centre) ** 2, axis=1))))
        d_re = (float(re_tree.query(centre, k=1)[0] / diag) if re_tree is not None else None)
        d_bc = (float(bc_tree.query(centre, k=1)[0] / diag) if bc_tree is not None else None)
        lam = None
        if divergence_exponent is not None:
            dv = np.asarray(divergence_exponent, float).reshape(-1)[sub]
            dv = dv[np.isfinite(dv)]
            lam = float(np.median(dv)) if dv.size else None
        mrs = (float(np.nanmax(np.asarray(raw_stress, float).reshape(-1)[sub]))
               if raw_stress is not None else None)

        regions.append(Region(
            region_id=rid,
            n_points=int(m.sum()),
            max_confidence=float(np.max(cf)),
            mean_confidence=float(np.mean(cf)),
            centroid=[float(x) for x in centre],
            size_frac=rms_r / diag,
            bbox_size=[float(x) for x in (c.max(0) - c.min(0))],
            max_raw_stress=mrs,
            divergence_exponent=lam,
            nearest_reentrant_edge_frac=d_re,
            nearest_bc_face_frac=d_bc,
            probable_cause=_probable_cause(d_re, d_bc, rms_r / diag, lam),
        ))
        rid += 1

    regions.sort(key=lambda r: r.max_confidence, reverse=True)
    for i, r in enumerate(regions):
        r.region_id = i
    return regions


def _probable_cause(d_re, d_bc, size_frac, lam):
    lam_txt = " (lambda ~ {0:.2f})".format(lam) if (lam is not None and 0.02 < lam < 1.5) else ""
    if d_re is not None and d_re < 0.05:
        return "re-entrant corner / sharp internal edge" + lam_txt
    if d_bc is not None and d_bc < 0.05:
        if size_frac < 0.02:
            return "point / edge boundary condition (concentrated load or restraint)"
        return "boundary-condition discontinuity (fixed/free transition)"
    if lam is not None and lam > 0.15:
        return "mathematical stress singularity (source not localised to a known feature)"
    return "unresolved stress concentration -- refine locally to confirm"


def regions_to_dict(regions: list[Region]) -> list[dict]:
    return [r.as_dict() for r in regions]
