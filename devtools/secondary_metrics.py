"""Secondary singularity metrics (Milestone 5, spec S28-S32).

External automation layer -> CPython 3 + numpy/scipy (+ DPF for the field ones).

These are SUPPORTING signals -- none classifies on its own (spec S31).  Each
returns a 0..1 sub-score plus its raw trend; Milestone 6 combines them with the
primary mesh-divergence evidence into the Confidence score:

    S28  nodal-difference / elemental-nodal stress disagreement, tracked vs h
    S29  discretisation-error indicator (ZZ-lite), localisation + persistence
    S30  hotspot localisation -- does the sigma > f*peak region shrink as h -> 0
    S31  geometry / BC prior -- proximity to a re-entrant edge or a point BC
    S32  global-solution sanity gate -- displacement / reaction / energy stability

`assemble(study_dir)` runs them all and writes secondary_metrics.json.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]


# ======================================================================= #
# S30  hotspot localisation  (pure -- operates on a cross_mesh series)     #
# ======================================================================= #
def hotspot_localization(series, frac: float = 0.8) -> dict:
    """Per level: peak stress and the physical extent of the region where
    sigma > frac * peak, measured at the fixed reference locations.  A singular
    field concentrates: peak grows while the hot region SHRINKS.  A finite
    concentration approaches a stable spatial profile."""
    from devtools import convergence_classifier as cc

    v = series.valid
    coords = series.ref_coords[v]
    mat = series.matrix[v]
    h_ord, order = cc._ordered_h(series)          # coarse -> fine
    mat = mat[:, order]
    L = mat.shape[1]

    diag = float(np.linalg.norm(coords.max(0) - coords.min(0))) or 1.0
    peaks, radii, counts, frac_pts = [], [], [], []
    for j in range(L):
        col = mat[:, j]
        pk = float(np.nanmax(col))
        hot = col >= frac * pk
        peaks.append(pk)
        counts.append(int(hot.sum()))
        frac_pts.append(float(hot.mean()))
        if hot.sum() >= 2:
            c = coords[hot]
            centre = c.mean(0)
            radii.append(float(np.sqrt(np.mean(np.sum((c - centre) ** 2, axis=1)))) / diag)
        else:
            radii.append(0.0)

    radii = np.array(radii)
    peaks = np.array(peaks)
    fp = np.array(frac_pts)
    # trends (use first/last of each; both should agree for a real singularity)
    r_trend = float(radii[-1] / radii[0]) if radii[0] > 0 else np.nan       # <1 shrinking
    fp_trend = float(fp[-1] / fp[0]) if fp[0] > 0 else np.nan               # <1 shrinking
    p_growing = bool(peaks[-1] > peaks[0] * 1.02)

    score = 0.0
    if p_growing and np.isfinite(fp_trend):
        # the fraction of the domain that is "hot" is the robust localisation
        # measure; a singular field concentrates it strongly.
        if fp_trend < 0.7:
            score = float(np.clip((0.7 - fp_trend) / 0.65, 0.0, 1.0))
        elif fp_trend > 1.2:
            score = 0.0
        else:
            score = 0.2
        if np.isfinite(r_trend) and r_trend < 0.85:
            score = min(1.0, score + 0.2)          # RMS radius agrees -> bonus
    return {
        "peaks": peaks.tolist(),
        "hot_radius_frac": radii.tolist(),
        "hot_point_count": counts,
        "hot_point_fraction": frac_pts,
        "radius_ratio_last_first": r_trend,
        "hot_fraction_ratio_last_first": fp_trend,
        "peak_growing": p_growing,
        "score": score,
    }


# ======================================================================= #
# S32  global-solution sanity gate  (pure -- operates on the study summary) #
# ======================================================================= #
def global_sanity(study_summary: dict) -> dict:
    """Across the last levels, is the OVERALL solution steady?  If displacement,
    reaction, or the load/reaction balance move a lot with refinement, local
    stress divergence cannot yet be trusted as a mathematical singularity
    (spec S32) -- this returns a 0..1 'solution stable' multiplier."""
    levels = [lv for lv in study_summary.get("levels", []) if lv.get("status") == "ok"]
    force = float(study_summary.get("force_newtons", 0.0) or 0.0)

    def col(path):
        out = []
        for lv in levels:
            d = lv.get("results", {}) or {}
            cur = d
            for k in path:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            if isinstance(cur, (int, float)):
                out.append(float(cur))
        return np.array(out, dtype=float)

    defo = col(["max_total_deformation", "value"])
    reac = col(["reaction_at_fixed_support", "value"])

    notes = []

    def cov_last(a, n=3):
        a = a[-n:]
        if a.size < 2 or np.mean(np.abs(a)) < 1e-30:
            return np.nan
        return float(np.std(a) / np.mean(np.abs(a)))

    cov_defo = cov_last(defo)
    cov_reac = cov_last(reac)
    imbalance = np.nan
    if reac.size and force > 0:
        imbalance = float(np.mean(np.abs(reac[-3:] - force)) / force)

    stable = 1.0
    if np.isfinite(cov_defo) and cov_defo > 0.02:
        stable -= min((cov_defo - 0.02) / 0.10, 0.6)
        notes.append("max deformation still moving ({0:.1%} CoV over last levels)".format(cov_defo))
    if np.isfinite(imbalance) and imbalance > 0.03:
        stable -= min((imbalance - 0.03) / 0.20, 0.5)
        notes.append("reaction != applied load ({0:.1%} imbalance)".format(imbalance))
    if np.isfinite(cov_reac) and cov_reac > 0.05:
        stable -= min((cov_reac - 0.05) / 0.20, 0.3)
        notes.append("reaction not settled")

    stable = float(np.clip(stable, 0.0, 1.0))
    return {
        "deformation_cov_last3": cov_defo,
        "reaction_cov_last3": cov_reac,
        "reaction_load_imbalance": imbalance,
        "solution_stable": stable,
        "notes": notes,
    }


# ======================================================================= #
# S28 + S29  elemental-nodal disagreement + ZZ-lite error  (needs DPF)     #
# ======================================================================= #
def nodal_disagreement(rst_path: str) -> dict:
    """Per node: elemental-nodal von Mises disagreement across the elements that
    share it -- via DPF's official ``nodal_difference_fc`` (max - min) and
    ``nodal_fraction_fc`` (difference / averaged).  Plus a global ZZ-lite
    discretisation-error proxy: RMS of (half the disagreement) over RMS averaged."""
    from devtools import dpf_adapter
    dpf, _ = dpf_adapter._dpf()
    from ansys.dpf.core import operators as ops
    import os

    model = dpf.Model(os.path.abspath(rst_path))
    mesh = model.metadata.meshed_region
    all_coords = np.array(mesh.nodes.coordinates_field.data, float).reshape(-1, 3)
    all_ids = np.array(mesh.nodes.scoping.ids, np.int64)
    pos = {int(x): i for i, x in enumerate(all_ids)}

    er = dpf_adapter.eval_retry
    s = model.results.stress()                        # ElementalNodal by default
    vm_fc = er(ops.invariant.von_mises_eqv_fc(fields_container=er(s)))
    diff_f = er(ops.averaging.nodal_difference_fc(fields_container=vm_fc))[0]
    frac_f = er(ops.averaging.nodal_fraction_fc(fields_container=vm_fc))[0]
    avg_f = er(ops.averaging.to_nodal_fc(fields_container=vm_fc))[0]

    ids = np.array(diff_f.scoping.ids, np.int64)
    idx = np.array([pos.get(int(x), -1) for x in ids])
    keep = idx >= 0
    coords = all_coords[idx[keep]]
    diff = np.array(diff_f.data, float).reshape(-1)[keep]
    frac = np.array(frac_f.data, float).reshape(-1)[keep]
    avg = np.array(avg_f.data, float).reshape(-1)[keep]

    nz = (np.abs(avg) > 1e-12) & np.isfinite(diff)
    zz = float(np.sqrt(np.nanmean((diff[nz] / 2.0) ** 2)) /
               max(np.sqrt(np.nanmean(avg[nz] ** 2)), 1e-12)) if nz.any() else np.nan
    return {
        "coords": coords, "node_ids": ids[keep],
        "averaged": avg, "nodal_difference": diff, "nodal_fraction": frac,
        "zz_lite_global": zz,
        "n_nodes": int(coords.shape[0]),
    }


def disagreement_series(level_rst_paths: list[str], hotspot_xyz, radius_frac: float = 0.06) -> dict:
    """Track the nodal fraction (S28) and ZZ-lite error (S29) across levels,
    globally and in a neighbourhood of ``hotspot_xyz``.  Persistent / growing
    disagreement near the hotspot supports a singularity; decaying disagreement
    supports convergence."""
    hs = np.asarray(hotspot_xyz, float).reshape(3)
    glob_frac, near_frac, zz_glob, zz_near = [], [], [], []
    for p in level_rst_paths:
        d = nodal_disagreement(p)
        c = d["coords"]
        fr = d["nodal_fraction"]
        diff = d["nodal_difference"]
        avg = d["averaged"]
        ok = np.isfinite(fr)
        diag = float(np.linalg.norm(c.max(0) - c.min(0))) or 1.0
        dist = np.linalg.norm(c - hs, axis=1)
        near = ok & (dist <= radius_frac * diag)
        if near.sum() < 8:                                 # widen until enough
            near = ok & (dist <= 2 * radius_frac * diag)
        glob_frac.append(float(np.nanmedian(fr[ok])) if ok.any() else np.nan)
        # robust: MEDIAN of the nodal fraction in the neighbourhood
        near_frac.append(float(np.nanmedian(fr[near])) if near.sum() >= 3
                         else (float(np.nanmax(fr[ok])) if ok.any() else np.nan))
        zz_glob.append(d["zz_lite_global"])
        nz = near & (np.abs(avg) > 1e-12) & np.isfinite(diff)
        if nz.sum() >= 3:
            zz_near.append(float(np.sqrt(np.nanmean((diff[nz] / 2.0) ** 2)) /
                                 max(np.sqrt(np.nanmean(avg[nz] ** 2)), 1e-12)))
        else:
            zz_near.append(np.nan)

    near_frac = np.array(near_frac); glob_frac = np.array(glob_frac)
    zz_glob = np.array(zz_glob); zz_near = np.array(zz_near)

    def trend_ratio(a):
        a = a[np.isfinite(a)]
        if a.size < 2 or a[0] <= 0:
            return np.nan
        # mean of the last 2 vs the first 2 -> less hostage to one noisy level
        lo = np.mean(a[:2])
        hi = np.mean(a[-2:])
        return float(hi / lo) if lo > 0 else np.nan

    r_near = trend_ratio(near_frac)
    r_glob = trend_ratio(glob_frac)
    # zz near: the coarsest level is a 1-element outlier -> use levels 2..N
    r_zz_near = trend_ratio(zz_near[1:]) if np.isfinite(zz_near[1:]).sum() >= 2 else np.nan

    # S28: near-hotspot elemental-nodal disagreement PERSISTS relative to the
    # bulk.  For a smooth field near/global decay at the same rate (persistence
    # ~1); for a singularity the near disagreement decays much more slowly.
    persistence = (r_near / r_glob) if (np.isfinite(r_near) and np.isfinite(r_glob)
                                        and r_glob > 1e-6) else np.nan
    s28 = 0.0
    if np.isfinite(persistence):
        if persistence >= 1.4:
            s28 = float(np.clip((persistence - 1.4) / 2.6 + 0.4, 0.0, 1.0))
        elif persistence <= 1.15:
            s28 = 0.0
        else:
            s28 = 0.25
    # absolute non-decay is itself strong evidence
    if np.isfinite(r_near) and r_near >= 0.9:
        s28 = max(s28, 0.7)

    # S29: localised discretisation error persists relative to the global one
    r_zz_glob = trend_ratio(zz_glob)
    zz_persist = (r_zz_near / r_zz_glob) if (np.isfinite(r_zz_near) and np.isfinite(r_zz_glob)
                                             and r_zz_glob > 1e-6) else np.nan
    s29 = 0.0
    if np.isfinite(zz_persist):
        s29 = float(np.clip((zz_persist - 1.2) / 2.0, 0.0, 1.0))

    return {
        "nodal_fraction_global": glob_frac.tolist(),
        "nodal_fraction_near_hotspot": near_frac.tolist(),
        "zz_lite_global": zz_glob.tolist(),
        "zz_lite_near_hotspot": zz_near.tolist(),
        "near_trend_ratio": r_near, "global_trend_ratio": r_glob,
        "disagreement_persistence": persistence,
        "zz_near_trend_ratio": r_zz_near, "zz_persistence": zz_persist,
        "score_s28": s28, "score_s29": s29,
    }


# ======================================================================= #
# S31  geometry / BC prior                                                 #
# ======================================================================= #
def geometry_prior(rst_path: str, hotspot_xyz, bc_face_geometry: dict | None = None,
                   reentrant_angle_deg: float = 200.0) -> dict:
    """A 0..1 prior from how close the hotspot sits to (a) a re-entrant surface
    edge -- concave dihedral > ``reentrant_angle_deg`` -- or (b) a BC face
    centroid.  A supporting prior only (spec S31)."""
    from devtools import dpf_adapter
    dpf, _ = dpf_adapter._dpf()
    import os

    hs = np.asarray(hotspot_xyz, float).reshape(3)
    model = dpf.Model(os.path.abspath(rst_path))
    mesh = model.metadata.meshed_region
    coords = np.array(mesh.nodes.coordinates_field.data, float).reshape(-1, 3)
    diag = float(np.linalg.norm(coords.max(0) - coords.min(0))) or 1.0

    # skin the solid, find edges shared by exactly two skin faces, measure the
    # dihedral angle; concave & sharp -> re-entrant
    reentrant_pts = reentrant_edge_points(rst_path, reentrant_angle_deg)
    d_edge = np.nan
    if reentrant_pts.size:
        d_edge = float(np.min(np.linalg.norm(reentrant_pts - hs, axis=1)) / diag)

    d_bc = np.nan
    if bc_face_geometry:
        cs = np.array([v["centroid"] for v in bc_face_geometry.values()], float)
        if cs.size:
            d_bc = float(np.min(np.linalg.norm(cs - hs, axis=1)) / diag)

    prior = 0.0
    reasons = []
    if np.isfinite(d_edge) and d_edge < 0.08:
        prior = max(prior, float(np.clip((0.08 - d_edge) / 0.08, 0.0, 1.0)))
        reasons.append("hotspot within {0:.1%} of model size of a re-entrant edge".format(d_edge))
    if np.isfinite(d_bc) and d_bc < 0.06:
        prior = max(prior, 0.5 * float(np.clip((0.06 - d_bc) / 0.06, 0.0, 1.0)))
        reasons.append("hotspot near a boundary-condition face")
    return {
        "dist_to_reentrant_edge_frac": d_edge,
        "dist_to_bc_face_frac": d_bc,
        "n_reentrant_edge_points": int(reentrant_pts.shape[0]) if reentrant_pts.size else 0,
        "prior": prior,
        "reasons": reasons,
    }


def reentrant_edge_points(rst_path: str, angle_deg: float = 200.0) -> np.ndarray:
    """Public: (K,3) points sampled along re-entrant (concave, sharp) surface
    edges of the model in ``rst_path``.  Empty array if none / on failure."""
    from devtools import dpf_adapter
    import os
    dpf, _ = dpf_adapter._dpf()
    model = dpf.Model(os.path.abspath(rst_path))
    return _reentrant_edge_points(dpf, model.metadata.meshed_region, angle_deg)


def per_point_geometry_prior(coords, reentrant_pts, bc_centroids, diag) -> np.ndarray:
    """0..1 prior at each of ``coords`` (M,3): high near a re-entrant edge,
    moderate near a BC face centroid.  Supporting prior only (spec S31)."""
    coords = np.asarray(coords, float).reshape(-1, 3)
    g = np.zeros(coords.shape[0])
    diag = float(diag) or 1.0
    if reentrant_pts is not None and len(reentrant_pts):
        from scipy.spatial import cKDTree
        d = cKDTree(np.asarray(reentrant_pts, float)).query(coords, k=1)[0] / diag
        g = np.maximum(g, np.clip((0.06 - d) / 0.06, 0.0, 1.0))
    if bc_centroids is not None and len(bc_centroids):
        from scipy.spatial import cKDTree
        d = cKDTree(np.asarray(bc_centroids, float)).query(coords, k=1)[0] / diag
        g = np.maximum(g, 0.5 * np.clip((0.05 - d) / 0.05, 0.0, 1.0))
    return g


def _reentrant_edge_points(dpf, mesh, angle_deg):
    """Sample points along surface edges whose dihedral angle exceeds angle_deg
    (concave / re-entrant).  Best-effort; returns (K,3) or empty."""
    try:
        from ansys.dpf.core import operators as ops
        skin = ops.mesh.skin(mesh=mesh).eval()
        sm = skin if hasattr(skin, "nodes") else skin.get_output(0, dpf.types.meshed_region)
    except Exception:
        return np.empty((0, 3))

    try:
        nodes = np.array(sm.nodes.coordinates_field.data, float).reshape(-1, 3)
        conn = sm.elements.connectivities_field
        n_el = sm.elements.n_elements
        # face normal per skin element (first 3 nodes)
        nid_lists = []
        normals = np.zeros((n_el, 3))
        centroids = np.zeros((n_el, 3))
        node_ids = np.array(sm.nodes.scoping.ids, np.int64)
        nmap = {int(x): i for i, x in enumerate(node_ids)}
        for k in range(n_el):
            nids = np.asarray(conn.get_entity_data(k), np.int64)
            idx = [nmap[int(x)] for x in nids if int(x) in nmap]
            if len(idx) < 3:
                nid_lists.append(set())
                continue
            p = nodes[idx]
            nrm = np.cross(p[1] - p[0], p[2] - p[0])
            ln = np.linalg.norm(nrm)
            normals[k] = nrm / ln if ln > 0 else 0.0
            centroids[k] = p.mean(0)
            nid_lists.append(set(int(x) for x in nids))

        # edges shared by two skin faces
        from collections import defaultdict
        edge_faces = defaultdict(list)
        for k, s in enumerate(nid_lists):
            s = sorted(s)
            for a in range(len(s)):
                for b in range(a + 1, len(s)):
                    edge_faces[(s[a], s[b])].append(k)

        pts = []
        for (a, b), fs in edge_faces.items():
            if len(fs) != 2 or a not in nmap or b not in nmap:
                continue
            k1, k2 = fs
            cang = float(np.clip(np.dot(normals[k1], normals[k2]), -1.0, 1.0))
            # exterior dihedral: angle between faces along a convex edge ~180; a
            # sharp re-entrant edge -> normals point "together" -> larger turn.
            turn = np.degrees(np.pi - np.arccos(cang))     # 0 flat .. 180 fold
            mid = 0.5 * (nodes[nmap[a]] + nodes[nmap[b]])
            # concavity test: does the vector between face centroids point
            # opposite the averaged normal? (re-entrant)
            v = centroids[k2] - centroids[k1]
            concave = np.dot(v, normals[k1] + normals[k2]) < 0
            if turn >= (angle_deg - 180.0) and concave:
                pts.append(mid)
        return np.array(pts) if pts else np.empty((0, 3))
    except Exception:
        return np.empty((0, 3))


# ======================================================================= #
# assemble                                                                 #
# ======================================================================= #
@dataclasses.dataclass
class SecondaryEvidence:
    s28_nodal_disagreement: float
    s29_discretisation_error: float
    s30_hotspot_localisation: float
    s31_geometry_prior: float
    s32_solution_stable: float          # a MULTIPLIER on confidence, not additive
    detail: dict

    def as_dict(self):
        return dataclasses.asdict(self)


def assemble(study_dir: str, *, prefer: str | None = None) -> SecondaryEvidence:
    from devtools import convergence_classifier as cc
    from devtools import cross_mesh

    d = Path(study_dir)
    summ = json.loads((d / "study_result.json").read_text(encoding="utf-8"))
    series = cross_mesh.from_study_dir(str(d), prefer=prefer)

    # hotspot location = finest-level argmax among points valid everywhere
    v = np.where(series.valid)[0]
    peak_i = v[int(np.argmax(series.matrix[v][:, series.ref_index]))]
    hotspot_xyz = series.ref_coords[peak_i]

    loc = hotspot_localization(series)
    san = global_sanity(summ)

    rsts = sorted(str(p / "file.rst") for p in d.glob("level_*") if (p / "file.rst").is_file())
    try:
        dis = disagreement_series(rsts, hotspot_xyz)
    except Exception as exc:  # noqa: BLE001
        dis = {"score_s28": 0.0, "score_s29": 0.0, "error": str(exc)}
    try:
        geo = geometry_prior(rsts[-1], hotspot_xyz, summ.get("bc_face_geometry"))
    except Exception as exc:  # noqa: BLE001
        geo = {"prior": 0.0, "error": str(exc)}

    ev = SecondaryEvidence(
        s28_nodal_disagreement=float(dis.get("score_s28", 0.0)),
        s29_discretisation_error=float(dis.get("score_s29", 0.0)),
        s30_hotspot_localisation=float(loc["score"]),
        s31_geometry_prior=float(geo.get("prior", 0.0)),
        s32_solution_stable=float(san["solution_stable"]),
        detail={"hotspot_xyz": hotspot_xyz.tolist(), "localisation": loc,
                "global_sanity": san, "disagreement": dis, "geometry_prior": geo},
    )
    (d / "secondary_metrics.json").write_text(
        json.dumps(_json_safe(ev.as_dict()), indent=2), encoding="utf-8")
    return ev


def _json_safe(o):
    if isinstance(o, dict):
        return {k: _json_safe(x) for k, x in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return _json_safe(o.tolist())
    return o


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(REPO))
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("study_dir")
    p.add_argument("--prefer", choices=["dpf_on_coordinates", "scipy_linear"], default=None)
    a = p.parse_args()

    ev = assemble(a.study_dir, prefer=a.prefer)
    print(json.dumps(_json_safe(ev.as_dict()), indent=2))
