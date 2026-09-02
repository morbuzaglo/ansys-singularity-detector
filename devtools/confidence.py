"""Singularity Confidence Score (Milestone 6, spec S33).

External automation layer -> CPython 3 + numpy.

An ENGINEERING CLASSIFICATION SCORE in 0..100 -- NOT a statistical probability.
Combines the primary mesh-divergence evidence (Milestone 4) with the secondary
metrics (Milestone 5) using the spec's initial weighting, then applies the
global-solution sanity gate as a multiplier:

    raw_i = 0.55 * mesh_divergence_evidence_i
          + locality_i * (0.20 * S28 + 0.10 * S29)
          + 0.15 * geometry_prior_i
    confidence_i = clip(raw_i, 0, 1) * S32_gate * 100

`locality_i` keeps the S28/S29 terms (measured AT the hotspot) near the hotspot
instead of lifting the whole model.  `mesh_divergence_evidence_i` and
`geometry_prior_i` are already per point.

Weights and category thresholds are PRELIMINARY (spec S33) -- see
`WEIGHTS` / `CATEGORIES`; calibrate on the benchmark suite.
"""

from __future__ import annotations

import dataclasses

import numpy as np

WEIGHTS = {"mesh_divergence": 0.55, "nodal_disagreement": 0.20,
           "discretisation_error": 0.10, "geometry_prior": 0.15}

CATEGORIES = [
    (0, 40, "likely convergent"),
    (40, 70, "uncertain"),
    (70, 90, "probable singularity"),
    (90, 100.0001, "very strong singularity evidence"),
]


def category(confidence: float) -> str:
    for lo, hi, name in CATEGORIES:
        if lo <= confidence < hi:
            return name
    return "likely convergent" if confidence < 40 else "very strong singularity evidence"


@dataclasses.dataclass
class ConfidenceField:
    coords: np.ndarray            # (M, 3)
    confidence: np.ndarray        # (M,) 0..100
    components: dict              # per-point arrays that went into it
    gate: float                   # S32 multiplier applied
    weights: dict
    hotspot_index: int

    @property
    def hotspot_confidence(self) -> float:
        return float(self.confidence[self.hotspot_index])

    def band_fractions(self) -> dict:
        c = self.confidence
        return {name: float(np.mean((c >= lo) & (c < hi))) for lo, hi, name in CATEGORIES}

    def summary(self) -> dict:
        return {
            "hotspot_confidence": round(self.hotspot_confidence, 2),
            "hotspot_category": category(self.hotspot_confidence),
            "gate": round(self.gate, 4),
            "weights": self.weights,
            "max_confidence": round(float(np.nanmax(self.confidence)), 2),
            "fraction_ge_70": float(np.mean(self.confidence >= 70)),
            "fraction_ge_90": float(np.mean(self.confidence >= 90)),
            "band_fractions": self.band_fractions(),
            "note": "engineering classification score, not a statistical probability",
        }


def compute(
    coords: np.ndarray,
    mesh_divergence_evidence: np.ndarray,      # (M,) 0..1 per point (field_divergence)
    *,
    s28: float,
    s29: float,
    s32_gate: float,
    geometry_prior: np.ndarray | None = None,  # (M,) 0..1 per point
    hotspot_xyz: np.ndarray | None = None,
    locality_radius_frac: float = 0.12,
    weights: dict | None = None,
) -> ConfidenceField:
    coords = np.asarray(coords, float).reshape(-1, 3)
    e = np.clip(np.asarray(mesh_divergence_evidence, float).reshape(-1), 0.0, 1.0)
    M = coords.shape[0]
    w = dict(WEIGHTS if weights is None else weights)
    g = (np.zeros(M) if geometry_prior is None
         else np.clip(np.asarray(geometry_prior, float).reshape(-1), 0.0, 1.0))

    diag = float(np.linalg.norm(coords.max(0) - coords.min(0))) or 1.0
    if hotspot_xyz is None:
        hs_i = int(np.nanargmax(e + 1e-6 * g))
    else:
        hs_i = int(np.argmin(np.linalg.norm(coords - np.asarray(hotspot_xyz, float), axis=1)))
    d = np.linalg.norm(coords - coords[hs_i], axis=1) / diag
    locality = np.exp(-(d / max(locality_radius_frac, 1e-6)) ** 2)

    sec_local = locality * (w["nodal_disagreement"] * float(np.clip(s28, 0, 1))
                            + w["discretisation_error"] * float(np.clip(s29, 0, 1)))
    raw = (w["mesh_divergence"] * e
           + sec_local
           + w["geometry_prior"] * g)
    gate = float(np.clip(s32_gate, 0.0, 1.0))
    conf = np.clip(raw, 0.0, 1.0) * gate * 100.0

    return ConfidenceField(
        coords=coords, confidence=conf, gate=gate, weights=w, hotspot_index=hs_i,
        components={
            "mesh_divergence_evidence": e,
            "geometry_prior": g,
            "locality": locality,
            "s28_applied": w["nodal_disagreement"] * float(np.clip(s28, 0, 1)),
            "s29_applied": w["discretisation_error"] * float(np.clip(s29, 0, 1)),
            "raw": np.clip(raw, 0.0, 1.0),
        },
    )
