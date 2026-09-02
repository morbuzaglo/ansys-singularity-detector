"""Full singularity analysis of a completed convergence study (Milestone 6).

External automation layer -> CPython 3.  The end-to-end pipeline an ACT
"Analyze" callback delegates to:

    study dir
      -> cross_mesh  (sigma_vm(x, h_i) on the finest mesh, spec S24)
      -> convergence_classifier.field_divergence   (per-point mesh divergence)
      -> convergence_classifier.classify_hotspot_neighborhood  (the headline verdict)
      -> secondary_metrics.assemble   (S28..S32, spec S28-S32)
      -> confidence.compute           (0..100 score, spec S33)
      -> region_clustering.cluster    (connected singular regions, spec S34)
      -> singularity_analysis.json  +  confidence_field.npz

    python devtools/analyze_study.py artifacts/2026-..._study_252
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import (confidence, convergence_classifier as cc, cross_mesh,  # noqa: E402
                      region_clustering, secondary_metrics)


def analyze(study_dir: str, *, prefer: str | None = None,
            confidence_threshold: float = 70.0) -> dict:
    d = Path(study_dir)
    summ = json.loads((d / "study_result.json").read_text(encoding="utf-8"))
    series = cross_mesh.from_study_dir(str(d), prefer=prefer)

    # ---- primary: per-point mesh-divergence evidence + headline verdict ----
    v = series.valid
    coords = series.ref_coords[v]
    h_ord, order = cc._ordered_h(series)
    mat = series.matrix[v][:, order]
    fd = cc.field_divergence(h_ord, mat)
    evidence = fd["evidence"]
    lam_field = fd["lambda_est"]
    raw_stress_finest = mat[:, -1]

    hot_verdict = cc.classify_hotspot_neighborhood(series)
    peak_i = int(np.nanargmax(mat[:, -1]))
    hotspot_xyz = coords[peak_i]

    # ---- secondary metrics (S28..S32) ----
    sec = secondary_metrics.assemble(str(d), prefer=prefer)

    # ---- per-point geometry prior (S31 spatialised) ----
    rsts = sorted(str(p / "file.rst") for p in d.glob("level_*") if (p / "file.rst").is_file())
    diag = float(np.linalg.norm(coords.max(0) - coords.min(0))) or 1.0
    try:
        re_pts = secondary_metrics.reentrant_edge_points(rsts[-1])
    except Exception:
        re_pts = np.empty((0, 3))
    bc_c = np.array([g["centroid"] for g in (summ.get("bc_face_geometry") or {}).values()], float) \
        if summ.get("bc_face_geometry") else np.empty((0, 3))
    geom_prior = secondary_metrics.per_point_geometry_prior(coords, re_pts, bc_c, diag)

    # ---- confidence field (S33) ----
    cfield = confidence.compute(
        coords, evidence,
        s28=sec.s28_nodal_disagreement, s29=sec.s29_discretisation_error,
        s32_gate=sec.s32_solution_stable,
        geometry_prior=geom_prior, hotspot_xyz=hotspot_xyz,
    )

    # ---- regions (S34) ----
    # For the per-point lambda field, clamp the noisy vectorised estimate and
    # splice in the trustworthy curve-fit lambda near the hotspot.
    lam_for_regions = np.where(lam_field < 1.5, lam_field, np.nan)
    if hot_verdict.divergence_exponent is not None:
        near_hs = np.linalg.norm(coords - hotspot_xyz, axis=1) < 0.05 * diag
        lam_for_regions[near_hs] = hot_verdict.divergence_exponent
    regions = region_clustering.cluster(
        coords, cfield.confidence, threshold=confidence_threshold,
        raw_stress=raw_stress_finest, divergence_exponent=lam_for_regions,
        reentrant_pts=re_pts, bc_centroids=bc_c,
    )

    # headline confidence = the strongest region (or the hotspot point if none)
    headline_conf = (max(r.max_confidence for r in regions) if regions
                     else cfield.hotspot_confidence)

    np.savez_compressed(
        d / "confidence_field.npz",
        coords=coords, confidence=cfield.confidence,
        evidence=evidence, lambda_est=lam_field, raw_stress_finest=raw_stress_finest,
        geometry_prior=geom_prior,
    )

    out = {
        "study_dir": str(d),
        "ansys_version_reported": summ.get("ansys_version_reported"),
        "geometry": summ.get("geometry"),
        "setup": summ.get("setup"),
        "n_ref_points": int(series.m),
        "n_valid": int(series.valid.sum()),
        "headline": {
            "classification": hot_verdict.classification,
            "singularity_confidence": round(headline_conf, 2),
            "category": confidence.category(headline_conf),
            "hotspot_point_confidence": round(cfield.hotspot_confidence, 2),
            "divergence_exponent": (None if hot_verdict.divergence_exponent is None
                                    else round(hot_verdict.divergence_exponent, 4)),
            "extrapolated_limit": (None if hot_verdict.extrapolated_limit is None
                                   else round(hot_verdict.extrapolated_limit, 4)),
            "limit_is_trustworthy": bool(hot_verdict.limit_is_trustworthy),
        },
        "confidence": cfield.summary(),
        "secondary_metrics": {
            "s28_nodal_disagreement": sec.s28_nodal_disagreement,
            "s29_discretisation_error": sec.s29_discretisation_error,
            "s30_hotspot_localisation": sec.s30_hotspot_localisation,
            "s31_geometry_prior": sec.s31_geometry_prior,
            "s32_solution_stable": sec.s32_solution_stable,
        },
        "regions": region_clustering.regions_to_dict(regions),
        "n_regions": len(regions),
        "confidence_threshold": confidence_threshold,
        "artifacts": {
            "confidence_field": str(d / "confidence_field.npz"),
            "secondary_metrics": str(d / "secondary_metrics.json"),
        },
    }
    (d / "singularity_analysis.json").write_text(
        json.dumps(_safe(out), indent=2), encoding="utf-8")
    return _safe(out)


def _safe(o):
    if isinstance(o, dict):
        return {k: _safe(x) for k, x in o.items()}
    if isinstance(o, (list, tuple)):
        return [_safe(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return _safe(o.tolist())
    return o


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("study_dir")
    p.add_argument("--prefer", choices=["dpf_on_coordinates", "scipy_linear"], default=None)
    p.add_argument("--threshold", type=float, default=70.0)
    a = p.parse_args()

    res = analyze(a.study_dir, prefer=a.prefer, confidence_threshold=a.threshold)
    h = res["headline"]
    print("=" * 64)
    print("SINGULARITY ANALYSIS  --  {0}".format(Path(a.study_dir).name))
    print("=" * 64)
    print("classification         : {0}".format(h["classification"]))
    print("Singularity Confidence : {0:.1f} / 100   [{1}]".format(
        h["singularity_confidence"], h["category"]))
    print("divergence exponent    : {0}".format(h["divergence_exponent"]))
    print("extrapolated limit     : {0} (trust={1})".format(
        h["extrapolated_limit"], h["limit_is_trustworthy"]))
    s = res["secondary_metrics"]
    print("secondary  S28={0:.2f} S29={1:.2f} S30={2:.2f} S31={3:.2f}  gate S32={4:.2f}".format(
        s["s28_nodal_disagreement"], s["s29_discretisation_error"],
        s["s30_hotspot_localisation"], s["s31_geometry_prior"], s["s32_solution_stable"]))
    print("\n{0} singular region(s) (confidence >= {1}):".format(res["n_regions"], a.threshold))
    for r in res["regions"]:
        print("  #{0}  conf max {1:.0f} / mean {2:.0f}  |  {3} pts  size {4:.1%} of model".format(
            r["region_id"], r["max_confidence"], r["mean_confidence"],
            r["n_points"], r["size_frac"]))
        print("      cause: {0}".format(r["probable_cause"]))
        print("      centroid {0}  max raw stress {1}".format(
            [round(x, 5) for x in r["centroid"]],
            None if r["max_raw_stress"] is None else round(r["max_raw_stress"], 3)))
    print("\nsingularity_analysis.json written to", a.study_dir)
