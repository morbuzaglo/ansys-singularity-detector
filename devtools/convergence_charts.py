"""Convergence-visualisation charts (Milestone 8, spec S39).

External automation layer -> CPython 3 + numpy + matplotlib.

From a completed + analysed study dir produce, as PNG **and** the underlying CSV
(spec S39):

  Global
    * max stress vs element size
    * max stress vs element count
    * max deformation vs element size
    * strain energy vs element size            (when available)
    * reaction imbalance vs element size

  Per singular region (from singularity_analysis.json)
    * peak stress vs local element size
    * log stress vs log element size
    * stress increment vs mesh size
    * divergence-exponent fit
    * Nodal Fraction vs mesh level              (hotspot region, from secondary_metrics.json)
    * energy-error (ZZ-lite) vs mesh level      (hotspot region)
    * singularity evidence vs number of levels used

Outputs go in <study_dir>/charts/ with a charts_index.json.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import convergence_classifier as cc  # noqa: E402
from devtools import cross_mesh  # noqa: E402

_MPL = None


def _plt():
    global _MPL
    if _MPL is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        _MPL = plt
    return _MPL


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    _plt().close(fig)


def _csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _global_levels(summ):
    lv = [x for x in summ.get("levels", []) if x.get("status") == "ok"]
    h = np.array([x.get("actual_char_size_m", np.nan) for x in lv], float)
    ne = np.array([x.get("elements", np.nan) for x in lv], float)
    nn = np.array([x.get("nodes", np.nan) for x in lv], float)

    def col(*path):
        out = []
        for x in lv:
            cur = x.get("results", {}) or {}
            for k in path:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            out.append(cur if isinstance(cur, (int, float)) else np.nan)
        return np.array(out, float)

    return {
        "h": h, "n_elem": ne, "n_node": nn,
        "peak_eqv": col("peak_equivalent_stress", "value"),
        "max_def": col("max_total_deformation", "value"),
        "reaction": col("reaction_at_fixed_support", "value"),
        "strain_energy": col("strain_energy", "value"),
        "force": float(summ.get("force_newtons", np.nan) or np.nan),
    }


def _global_charts(out: Path, g: dict) -> list:
    plt = _plt()
    made = []
    order = np.argsort(-g["h"])                     # coarse -> fine
    h = g["h"][order]

    def _i(x):
        return int(x) if np.isfinite(x) else ""

    rows = [(hh, _i(ne), _i(nn), pe, md, rc) for hh, ne, nn, pe, md, rc in zip(
        h, g["n_elem"][order], g["n_node"][order],
        g["peak_eqv"][order], g["max_def"][order], g["reaction"][order])]
    _csv(out / "global_convergence.csv",
         ["char_elem_size_m", "n_elements", "n_nodes", "peak_eqv_stress",
          "max_total_deformation", "reaction_at_support"], rows)

    def line(x, y, xl, yl, fn, logx=False, logy=False):
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 2:
            return
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(x[ok], y[ok], "o-")
        if logx:
            ax.set_xscale("log")
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel(xl); ax.set_ylabel(yl); ax.grid(True, alpha=0.3)
        _save(fig, out / fn)
        made.append(str(out / fn))

    line(h, g["peak_eqv"][order], "char. element size h [m]", "peak eqv stress [MPa]",
         "global_peak_stress_vs_h.png")
    line(g["n_elem"][order], g["peak_eqv"][order], "element count", "peak eqv stress [MPa]",
         "global_peak_stress_vs_nelem.png", logx=True)
    line(h, g["max_def"][order], "char. element size h [m]", "max total deformation [mm]",
         "global_max_deformation_vs_h.png")
    if np.isfinite(g["strain_energy"]).sum() >= 2:
        line(h, g["strain_energy"][order], "char. element size h [m]", "strain energy",
             "global_strain_energy_vs_h.png")
    if np.isfinite(g["force"]) and g["force"] > 0:
        imb = np.abs(g["reaction"][order] - g["force"]) / g["force"]
        line(h, imb, "char. element size h [m]", "|reaction - load| / load",
             "global_reaction_imbalance_vs_h.png")
    return made


def _region_series(series: cross_mesh.CrossMeshSeries, centroid):
    """sigma_vm at the ref point nearest ``centroid``, across levels (coarse->fine),
    with the level sizes."""
    v = np.where(series.valid)[0]
    coords = series.ref_coords[v]
    i = int(np.argmin(np.linalg.norm(coords - np.asarray(centroid, float), axis=1)))
    h_ord, order = cc._ordered_h(series)
    s = series.matrix[v][i, order]
    return np.asarray(h_ord, float), s


def _region_charts(out: Path, rid: int, h, s, verdict) -> list:
    plt = _plt()
    made = []
    _csv(out / "region_{0}_convergence.csv".format(rid),
         ["char_elem_size_m", "peak_eqv_stress"], list(zip(h, s)))

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(h, s, "o-")
    ax.set_xlabel("local char. element size h [m]"); ax.set_ylabel("peak eqv stress [MPa]")
    ax.set_title("Region {0}: stress vs h".format(rid)); ax.grid(True, alpha=0.3)
    _save(fig, out / "region_{0}_stress_vs_h.png".format(rid))
    made.append(str(out / "region_{0}_stress_vs_h.png".format(rid)))

    d = np.abs(np.diff(s))
    hmid = np.sqrt(h[:-1] * h[1:])
    ok = (d > 0) & np.isfinite(hmid)
    if ok.sum() >= 2:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.loglog(hmid[ok], d[ok], "o-")
        b = np.polyfit(np.log(hmid[ok]), np.log(d[ok]), 1)[0]
        ax.set_xlabel("mid h [m]"); ax.set_ylabel("|d sigma| [MPa]")
        ax.set_title("Region {0}: increment slope b={1:.2f} (lambda_est~{2:.2f})".format(
            rid, b, max(-b - 1.0, 0.0)))
        ax.grid(True, which="both", alpha=0.3)
        _save(fig, out / "region_{0}_increment_loglog.png".format(rid))
        made.append(str(out / "region_{0}_increment_loglog.png".format(rid)))

    if verdict is not None and verdict.divergent_fit is not None and verdict.divergent_fit.ok:
        p = verdict.divergent_fit.params
        hh = np.linspace(h.min(), h.max(), 60)
        fit = p["B"] + p["A"] * hh ** (-p["lambda"])
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(h, s, "o", label="data")
        ax.plot(hh, fit, "-", label="B + A h^-{0:.3f}".format(p["lambda"]))
        if verdict.finite_fit is not None and verdict.finite_fit.ok:
            q = verdict.finite_fit.params
            ax.plot(hh, q["sigma_inf"] + q["C"] * hh ** q["p"], "--",
                    label="sigma_inf + C h^{0:.2f}".format(q["p"]))
        ax.set_xlabel("h [m]"); ax.set_ylabel("peak eqv stress [MPa]")
        ax.set_title("Region {0}: model fits".format(rid)); ax.legend(); ax.grid(True, alpha=0.3)
        _save(fig, out / "region_{0}_model_fits.png".format(rid))
        made.append(str(out / "region_{0}_model_fits.png".format(rid)))
    return made


def _secondary_charts(out: Path, rid: int, sec: dict) -> list:
    plt = _plt()
    made = []
    dis = (sec or {}).get("detail", {}).get("disagreement", {})
    nf = dis.get("nodal_fraction_near_hotspot")
    zz = dis.get("zz_lite_near_hotspot")
    if nf:
        lv = list(range(len(nf)))
        _csv(out / "region_{0}_nodal_fraction_vs_level.csv".format(rid),
             ["level", "nodal_fraction_near_hotspot"], list(zip(lv, nf)))
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(lv, nf, "o-")
        ax.set_xlabel("mesh level"); ax.set_ylabel("Nodal Fraction (near hotspot)")
        ax.set_title("Region {0}: Nodal Fraction vs level".format(rid)); ax.grid(True, alpha=0.3)
        _save(fig, out / "region_{0}_nodal_fraction_vs_level.png".format(rid))
        made.append(str(out / "region_{0}_nodal_fraction_vs_level.png".format(rid)))
    if zz:
        lv = list(range(len(zz)))
        _csv(out / "region_{0}_energy_error_vs_level.csv".format(rid),
             ["level", "zz_lite_near_hotspot"], list(zip(lv, zz)))
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(lv, zz, "o-")
        ax.set_xlabel("mesh level"); ax.set_ylabel("ZZ-lite energy error (near hotspot)")
        ax.set_title("Region {0}: energy error vs level".format(rid)); ax.grid(True, alpha=0.3)
        _save(fig, out / "region_{0}_energy_error_vs_level.png".format(rid))
        made.append(str(out / "region_{0}_energy_error_vs_level.png".format(rid)))
    return made


def _evidence_vs_nlevels(out: Path, rid: int, h, s) -> list:
    plt = _plt()
    ns, ev = [], []
    for k in range(3, len(h) + 1):
        ns.append(k)
        ev.append(cc.classify_series(h[:k], s[:k]).singularity_evidence)
    if len(ns) < 2:
        return []
    _csv(out / "region_{0}_evidence_vs_nlevels.csv".format(rid),
         ["n_levels_used", "singularity_evidence"], list(zip(ns, ev)))
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(ns, ev, "o-")
    ax.set_ylim(0, 1); ax.set_xlabel("number of mesh levels used")
    ax.set_ylabel("singularity evidence"); ax.grid(True, alpha=0.3)
    ax.set_title("Region {0}: evidence firming up".format(rid))
    _save(fig, out / "region_{0}_evidence_vs_nlevels.png".format(rid))
    return [str(out / "region_{0}_evidence_vs_nlevels.png".format(rid))]


def build(study_dir: str, *, prefer: str | None = None) -> dict:
    d = Path(study_dir)
    out = d / "charts"
    out.mkdir(exist_ok=True)
    summ = json.loads((d / "study_result.json").read_text(encoding="utf-8"))

    made = _global_charts(out, _global_levels(summ))

    analysis = None
    ap = d / "singularity_analysis.json"
    if ap.is_file():
        analysis = json.loads(ap.read_text(encoding="utf-8"))
    sec = None
    sp = d / "secondary_metrics.json"
    if sp.is_file():
        sec = json.loads(sp.read_text(encoding="utf-8"))

    regions = (analysis or {}).get("regions", [])
    if regions:
        series = cross_mesh.from_study_dir(str(d), prefer=prefer)
        for r in regions:
            rid = r["region_id"]
            h, s = _region_series(series, r["centroid"])
            verdict = cc.classify_series(h, s) if h.size >= 4 else None
            made += _region_charts(out, rid, h, s, verdict)
            made += _evidence_vs_nlevels(out, rid, h, s)
            if rid == 0 and sec is not None:
                made += _secondary_charts(out, rid, sec)

    index = {
        "study_dir": str(d),
        "n_charts": len(made),
        "charts": [Path(m).name for m in made],
        "n_regions": len(regions),
    }
    (out / "charts_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("study_dir")
    p.add_argument("--prefer", choices=["dpf_on_coordinates", "scipy_linear"], default=None)
    a = p.parse_args()
    idx = build(a.study_dir, prefer=a.prefer)
    print(json.dumps(idx, indent=2))
    print("\n{0} charts + CSVs in {1}/charts/".format(idx["n_charts"], a.study_dir))
