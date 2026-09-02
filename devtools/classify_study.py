"""Primary singularity classification of a completed convergence study (Milestone 4).

External automation layer -> CPython 3.  Ties the pieces together:

    study dir  ->  cross_mesh.CrossMeshSeries (sigma_vm(x, h_i) on the finest mesh)
               ->  convergence_classifier  (finite-limit vs divergent, per S26/S27)
               ->  classification.json  { hotspot verdict, field summary }

This is the function an ACT "Classify" callback delegates to; a test drives it
directly.  It is *only* the mesh convergence/divergence stage -- the secondary
metrics, geometry priors and the Confidence score come in later milestones.

    python devtools/classify_study.py artifacts/2026-..._study_252
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import convergence_classifier as cc  # noqa: E402
from devtools import cross_mesh  # noqa: E402


def classify_series_obj(series: cross_mesh.CrossMeshSeries, *, field_sample: int = 4000) -> dict:
    hotspot = cc.classify_hotspot_neighborhood(series)
    bulk = cc.classify_hotspot(series)

    # per-point divergence read over a subsample of valid points -- vectorised,
    # cheap (no curve_fit); the full two-model analysis is reserved for hotspots
    v = np.where(series.valid)[0]
    if field_sample and v.size > field_sample:
        v = v[np.linspace(0, v.size - 1, field_sample).astype(int)]
    h_ord, order = cc._ordered_h(series)
    mat = series.matrix[v][:, order]

    fd = cc.field_divergence(h_ord, mat)
    classes = {"convergent": 0, "singular": 0, "uncertain": 0, "insufficient_data": 0}
    for c in fd["classification"]:
        classes[str(c)] = classes.get(str(c), 0) + 1
    evid = fd["evidence"]
    lam = fd["lambda_est"]

    return {
        "n_ref_points": series.m,
        "n_valid": int(series.valid.sum()),
        "levels": [
            {"index": j, "size_m": series.sizes_m[j], "method": series.methods[j]}
            for j in range(series.n_levels)
        ],
        "hotspot_neighborhood": _verdict_dict(hotspot),
        "bulk_p99": _verdict_dict(bulk),
        "field": {
            "n_classified": int(v.size),
            "class_counts": classes,
            "singular_fraction": float(classes.get("singular", 0)) / max(v.size, 1),
            "evidence_mean": float(np.nanmean(evid)),
            "evidence_p95": float(np.nanpercentile(evid, 95)),
            "lambda_median_singular": (
                float(np.nanmedian(lam)) if np.isfinite(lam).any() else None),
        },
    }


def _clean(o):
    """Recursively coerce numpy scalars/arrays to plain Python for json.dump."""
    if isinstance(o, dict):
        return {k: _clean(x) for k, x in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return [_clean(x) for x in o.tolist()]
    return o


def _verdict_dict(v: cc.ConvergenceVerdict) -> dict:
    d = {
        "classification": v.classification,
        "singularity_evidence": round(v.singularity_evidence, 4),
        "divergence_exponent": None if v.divergence_exponent is None else round(v.divergence_exponent, 4),
        "extrapolated_limit": None if v.extrapolated_limit is None else round(v.extrapolated_limit, 4),
        "limit_is_trustworthy": v.limit_is_trustworthy,
    }
    for fit in (v.finite_fit, v.divergent_fit):
        if fit is not None:
            d[fit.name + "_fit"] = {
                "rel_resid": round(fit.rel_resid, 5), "r2": round(fit.r2, 5),
                "ok": fit.ok, "params": {k: round(x, 5) for k, x in fit.params.items()},
            }
    d["evidence"] = {k: (round(x, 4) if isinstance(x, float) else x)
                     for k, x in v.evidence.items()}
    return d


def classify_study_dir(study_dir: str, *, prefer: str | None = None) -> dict:
    series = cross_mesh.from_study_dir(study_dir, prefer=prefer)
    out = _clean(classify_series_obj(series))
    out["study_dir"] = str(study_dir)
    Path(study_dir, "classification.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("study_dir")
    p.add_argument("--prefer", choices=["dpf_on_coordinates", "scipy_linear"], default=None)
    a = p.parse_args()

    res = classify_study_dir(a.study_dir, prefer=a.prefer)
    hs = res["hotspot_neighborhood"]
    print("hotspot (corner/peak neighbourhood):")
    print("  classification      : {0}".format(hs["classification"]))
    print("  singularity_evidence: {0}".format(hs["singularity_evidence"]))
    print("  divergence_exponent : {0}".format(hs["divergence_exponent"]))
    print("  extrapolated_limit  : {0} (trust={1})".format(
        hs["extrapolated_limit"], hs["limit_is_trustworthy"]))
    if "finite_fit" in hs:
        print("  finite  fit rel_resid={0}  p={1}".format(
            hs["finite_fit"]["rel_resid"], hs["finite_fit"]["params"].get("p")))
    if "divergent_fit" in hs:
        print("  diverg. fit rel_resid={0}  lambda={1}".format(
            hs["divergent_fit"]["rel_resid"], hs["divergent_fit"]["params"].get("lambda")))
    print("\nfield: singular_fraction={0:.3f}  evidence_mean={1:.3f}".format(
        res["field"]["singular_fraction"], res["field"]["evidence_mean"]))
    print("classification.json written to", a.study_dir)
