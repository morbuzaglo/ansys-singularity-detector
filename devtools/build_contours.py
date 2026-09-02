"""Build the three Milestone 7 contour fields for a completed study (spec S35-S37).

External automation layer -> CPython 3.

From a study's confidence_field.npz (written by devtools/analyze_study) produce,
on the finest-mesh node locations:

  * Raw Stress -- Original FE Solution   (finest-mesh von Mises, UNTOUCHED, spec S36)
  * Singularity Confidence [%]           (0..100, spec S35)
  * Singularity-Filtered Stress          (raw where confidence < 70; robust
                                          neighbourhood recovery where >= 70;
                                          NaN / "Not Recoverable" where it can't
                                          be recovered -- spec S37)

Outputs in the study dir:
  contour_fields.npz     -- coords + the three fields + the recovery status
  contour_fields.csv     -- same, for the IronPython extension side to read
  contours_summary.json  -- counts + ranges
  contours/*.png         -- quick 3-D scatter previews (matplotlib)

An ACT custom-result <evaluate> callback later reads contour_fields.csv and
fills the Result Collector (extension/visualization.py).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import stress_recovery  # noqa: E402

RAW_NAME = "Raw Stress (Original FE Solution)"
CONF_NAME = "Singularity Confidence [%]"
FILT_NAME = "Singularity-Filtered Stress"


def _finest_char_size_m(study_dir: Path) -> float:
    try:
        s = json.loads((study_dir / "study_result.json").read_text(encoding="utf-8"))
        sizes = [lv.get("actual_char_size_m") for lv in s.get("levels", [])
                 if lv.get("status") == "ok" and lv.get("actual_char_size_m")]
        if sizes:
            return float(min(sizes))
    except Exception:
        pass
    return 1e-3


# Values handed to a Mechanical custom Result Collector must be in
# solver-consistent SI (Pa).  DPF returns the field in the .rst display unit,
# which for a standard NMM structural model is MPa -- so the raw numbers are
# ~1e6x too small when the ACT result evaluates them.  Calibrate the scale
# against Mechanical's own finest-level peak (which carries a unit string)
# rather than trusting DPF's unit attribute.
_STRESS_TO_PA = {
    "pa": 1.0, "n/m^2": 1.0, "n/m2": 1.0,
    "kpa": 1e3, "mpa": 1e6, "n/mm^2": 1e6, "n/mm2": 1e6, "gpa": 1e9,
    "bar": 1e5, "psi": 6894.757293168, "ksi": 6894757.293168,
}
_KNOWN_FACTORS = (1.0, 1e3, 1e6, 1e9)


def _mech_finest_peak_pa(study_dir: Path):
    """Mechanical's finest solved level peak equivalent stress, in Pa (or None)."""
    try:
        s = json.loads((study_dir / "study_result.json").read_text(encoding="utf-8"))
        ok = [lv for lv in s.get("levels", []) if lv.get("status") == "ok"]
        ok.sort(key=lambda lv: lv.get("actual_char_size_m") or 1e30)
        if not ok:
            return None
        pe = (ok[0].get("results") or {}).get("peak_equivalent_stress") or {}
        val = pe.get("value")
        unit = str(pe.get("unit", "")).strip().lower().replace(" ", "")
        if val and unit in _STRESS_TO_PA:
            return float(val) * _STRESS_TO_PA[unit]
    except Exception:
        pass
    return None


def _stress_scale_to_pa(raw: np.ndarray, study_dir: Path):
    """Return (factor, note) so raw * factor is in Pa."""
    rmax = float(np.nanmax(raw)) if raw.size and np.isfinite(np.nanmax(raw)) else 0.0
    peak_pa = _mech_finest_peak_pa(study_dir)
    if peak_pa and rmax > 0:
        ratio = peak_pa / rmax
        for f in _KNOWN_FACTORS:
            if 0.8 <= ratio / f <= 1.25:
                return f, "calibrated to Mechanical finest-level peak (x{0:g})".format(f)
        return 1.0, ("no clean factor vs Mechanical peak (ratio {0:.3g}); "
                     "left as-is".format(ratio))
    return 1.0, "no Mechanical peak reference; assumed already Pa"


def build(study_dir: str, *, threshold: float = 70.0, make_png: bool = True) -> dict:
    d = Path(study_dir)
    npz = d / "confidence_field.npz"
    if not npz.is_file():
        raise FileNotFoundError(
            "{0} not found -- run `python -m devtools.analyze_study {1}` first".format(npz, study_dir))
    z = np.load(npz, allow_pickle=True)
    coords = np.asarray(z["coords"], float)
    confidence = np.asarray(z["confidence"], float)
    raw = np.asarray(z["raw_stress_finest"], float)

    h_local = _finest_char_size_m(d)
    # coords are in the rst length unit (mm); convert h to that unit for radii
    # by matching scale to the model bounding box.
    diag_m_guess = float(np.linalg.norm(coords.max(0) - coords.min(0)))
    h_in_coord_units = h_local * (1000.0 if diag_m_guess > 1.0 else 1.0)

    rec = stress_recovery.recover(coords, raw, confidence, h_in_coord_units, threshold=threshold)
    filt = rec.filtered_stress
    status = rec.status

    # SI (Pa) copies for the ACT custom-result collector -- see _stress_scale_to_pa
    scale_pa, scale_note = _stress_scale_to_pa(raw, d)
    raw_pa = raw * scale_pa
    filt_pa = filt * scale_pa

    np.savez_compressed(
        d / "contour_fields.npz",
        coords=coords, raw_stress=raw, confidence=confidence,
        filtered_stress=filt, recovery_status=status.astype("U16"),
        raw_stress_pa=raw_pa, filtered_stress_pa=filt_pa, stress_scale_to_pa=scale_pa,
    )

    with open(d / "contour_fields.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "z", "raw_stress", "confidence_pct",
                    "filtered_stress", "recovery_status"])
        for i in range(coords.shape[0]):
            w.writerow([coords[i, 0], coords[i, 1], coords[i, 2],
                        raw_pa[i], confidence[i],
                        ("" if not np.isfinite(filt_pa[i]) else filt_pa[i]), status[i]])

    finite_filt = filt[np.isfinite(filt)]
    _raw_unit = "Pa" if scale_pa != 1.0 else "MPa or Pa (uncalibrated -- see csv_unit_note)"
    summary = {
        "study_dir": str(d),
        "threshold": threshold,
        "n_nodes": int(coords.shape[0]),
        "csv_stress_scale_to_pa": scale_pa,
        "csv_unit_note": scale_note + " -- contour_fields.csv raw_stress/filtered_stress are in Pa",
        "fields": {
            RAW_NAME: {"min": float(np.nanmin(raw)), "max": float(np.nanmax(raw)), "unit": _raw_unit},
            CONF_NAME: {"min": float(np.nanmin(confidence)), "max": float(np.nanmax(confidence)),
                        "unit": "%", "range": [0, 100]},
            FILT_NAME: {"min": float(finite_filt.min()) if finite_filt.size else None,
                        "max": float(finite_filt.max()) if finite_filt.size else None,
                        "unit": _raw_unit,
                        "note": "post-processing estimate -- NOT true/correct/exact stress"},
        },
        "recovery": rec.as_dict(),
        "peak_raw": float(np.nanmax(raw)),
        "peak_filtered": (float(finite_filt.max()) if finite_filt.size else None),
        "peak_reduction_pct": (
            None if not finite_filt.size or np.nanmax(raw) <= 0
            else round(100.0 * (1.0 - finite_filt.max() / np.nanmax(raw)), 1)),
    }
    recmask = status == "recovered"
    if recmask.any():
        rr = raw[recmask]
        rf = filt[recmask]
        summary["recovered_region"] = {
            "raw_mean": float(rr.mean()), "raw_max": float(rr.max()),
            "filtered_mean": float(rf.mean()), "filtered_max": float(rf.max()),
            "mean_reduction_pct": round(float(np.mean(100.0 * (1.0 - rf / rr))), 1),
            "max_reduction_pct": round(float(np.max(100.0 * (1.0 - rf / rr))), 1),
        }
    (d / "contours_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # small sidecar the IronPython visualization layer reads to (a) know the Pa
    # scale and (b) hide sub-threshold nodes in the Confidence contour so the
    # singular regions read clearly.
    (d / "contour_meta.json").write_text(json.dumps({
        "threshold": float(threshold),
        "stress_scale_to_pa": float(scale_pa),
        "n_nodes": int(coords.shape[0]),
        "n_confidence_ge_threshold": int((confidence >= threshold).sum()),
    }, indent=2), encoding="utf-8")

    if make_png:
        try:
            _previews(d, coords, raw, confidence, filt)
        except Exception as exc:  # noqa: BLE001
            summary["png_error"] = str(exc)

    return summary


def _previews(d, coords, raw, conf, filt):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = d / "contours"
    out.mkdir(exist_ok=True)
    # subsample for speed
    n = coords.shape[0]
    idx = np.arange(n) if n <= 20000 else np.linspace(0, n - 1, 20000).astype(int)
    c = coords[idx]
    for name, val, cmap in [("raw_stress", raw[idx], "viridis"),
                            ("singularity_confidence", conf[idx], "inferno"),
                            ("singularity_filtered_stress", filt[idx], "viridis")]:
        fig = plt.figure(figsize=(7, 5))
        ax = fig.add_subplot(111, projection="3d")
        finite = np.isfinite(val)
        p = ax.scatter(c[finite, 0], c[finite, 1], c[finite, 2], c=val[finite],
                       cmap=cmap, s=4)
        fig.colorbar(p, ax=ax, shrink=0.7, label=name)
        ax.set_title(name)
        fig.tight_layout()
        fig.savefig(out / (name + ".png"), dpi=110)
        plt.close(fig)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("study_dir")
    p.add_argument("--threshold", type=float, default=70.0)
    p.add_argument("--no-png", action="store_true")
    a = p.parse_args()

    s = build(a.study_dir, threshold=a.threshold, make_png=not a.no_png)
    print(json.dumps(s, indent=2))
    r = s["recovery"]
    print("\nRaw peak       : {0:.3f} MPa".format(s["peak_raw"]))
    print("Filtered peak  : {0} MPa   ({1}% below raw)".format(
        s["peak_filtered"], s["peak_reduction_pct"]))
    print("recovered {0}, not-recoverable {1}, left raw {2}".format(
        r["n_recovered"], r["n_not_recoverable"], r["n_raw"]))
