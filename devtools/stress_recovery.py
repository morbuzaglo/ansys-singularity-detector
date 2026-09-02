"""Singularity-Filtered Stress -- neighbourhood stress recovery (Milestone 7, spec S37).

External automation layer -> CPython 3 + numpy/scipy.  Pure; unit-tested.

This is a POST-PROCESSING VISUALISATION ESTIMATE.  It is never "true", "correct"
or "exact" stress (spec S37).  For a location whose Singularity Confidence is
below the threshold the finest-mesh raw stress is kept unchanged.  At or above
the threshold the value is replaced by a robust weighted estimate built only
from nearby LOCALLY CONVERGED points:

    w_j = (1 - C_j/100)^2 / (d_j + 0.25 * h_local)^2
    sigma_recovered = sum(w_j * sigma_j) / sum(w_j)

Neighbour constraints (spec S37):
  * same body, same material (when labels are supplied)
  * within ~2-3 local element layers
  * exclude other singular / flagged locations
  * robust outlier rejection (median +/- k*MAD) before averaging
  * if too few valid neighbours remain -> "Not Recoverable" (no invented value)

Alternative recovery schemes (MLS, polynomial patch, ZZ, SPR) are compared in
docs/stress_recovery_research.md (spec S38).
"""

from __future__ import annotations

import dataclasses

import numpy as np

try:
    from scipy.spatial import cKDTree
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

CONFIDENCE_THRESHOLD = 70.0     # spec S37
NEIGHBOUR_LAYERS = 2.5          # "~2-3 local element layers"
MIN_NEIGHBOURS = 6
MAD_K = 3.0                     # robust outlier rejection width


@dataclasses.dataclass
class RecoveryResult:
    filtered_stress: np.ndarray    # (M,)  raw where conf<thr, recovered where possible, nan otherwise
    status: np.ndarray             # (M,)  'raw' | 'recovered' | 'not_recoverable'
    n_neighbours_used: np.ndarray   # (M,)  int (0 for 'raw')

    @property
    def n_recovered(self) -> int:
        return int(np.count_nonzero(self.status == "recovered"))

    @property
    def n_not_recoverable(self) -> int:
        return int(np.count_nonzero(self.status == "not_recoverable"))

    def as_dict(self) -> dict:
        return {
            "n_points": int(self.status.size),
            "n_raw": int(np.count_nonzero(self.status == "raw")),
            "n_recovered": self.n_recovered,
            "n_not_recoverable": self.n_not_recoverable,
        }


def recover(
    coords: np.ndarray,             # (M, 3)
    raw_stress: np.ndarray,         # (M,)  finest-mesh von Mises
    confidence: np.ndarray,         # (M,)  0..100
    h_local: np.ndarray | float,    # (M,) or scalar -- local characteristic element size
    *,
    body_id: np.ndarray | None = None,
    material_id: np.ndarray | None = None,
    threshold: float = CONFIDENCE_THRESHOLD,
    layers: float = NEIGHBOUR_LAYERS,
    min_neighbours: int = MIN_NEIGHBOURS,
) -> RecoveryResult:
    if not _HAVE_SCIPY:  # pragma: no cover
        raise RuntimeError("scipy required for stress recovery")
    coords = np.asarray(coords, float).reshape(-1, 3)
    raw = np.asarray(raw_stress, float).reshape(-1)
    conf = np.asarray(confidence, float).reshape(-1)
    M = coords.shape[0]
    h = np.full(M, float(h_local)) if np.isscalar(h_local) else np.asarray(h_local, float).reshape(-1)

    out = raw.copy()
    status = np.where(conf >= threshold, "pending", "raw").astype(object)
    nused = np.zeros(M, dtype=int)

    flagged = conf >= threshold                       # "other singular locations"
    donors = ~flagged & np.isfinite(raw)             # locally converged points only
    if donors.sum() == 0:
        status[status == "pending"] = "not_recoverable"
        out[flagged] = np.nan
        return RecoveryResult(out, status, nused)

    donor_idx = np.where(donors)[0]
    tree = cKDTree(coords[donor_idx])
    targets = np.where(flagged)[0]

    for i in targets:
        radius = layers * h[i]
        cand_local = tree.query_ball_point(coords[i], r=radius)
        if not cand_local:
            status[i] = "not_recoverable"
            out[i] = np.nan
            continue
        cand = donor_idx[np.asarray(cand_local, dtype=int)]

        if body_id is not None:
            cand = cand[np.asarray(body_id)[cand] == np.asarray(body_id)[i]]
        if material_id is not None:
            cand = cand[np.asarray(material_id)[cand] == np.asarray(material_id)[i]]
        if cand.size < min_neighbours:
            status[i] = "not_recoverable"
            out[i] = np.nan
            continue

        sig = raw[cand]
        med = np.median(sig)
        mad = np.median(np.abs(sig - med))
        if mad > 0:
            keep = np.abs(sig - med) <= MAD_K * 1.4826 * mad
            cand, sig = cand[keep], sig[keep]
        if cand.size < min_neighbours:
            status[i] = "not_recoverable"
            out[i] = np.nan
            continue

        d = np.linalg.norm(coords[cand] - coords[i], axis=1)
        w = (1.0 - conf[cand] / 100.0) ** 2 / (d + 0.25 * h[i]) ** 2
        wsum = float(np.sum(w))
        if wsum <= 0 or not np.isfinite(wsum):
            status[i] = "not_recoverable"
            out[i] = np.nan
            continue
        out[i] = float(np.sum(w * sig) / wsum)
        status[i] = "recovered"
        nused[i] = int(cand.size)

    return RecoveryResult(out, status, nused)
