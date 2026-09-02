"""Milestone 7 acceptance -- the three custom contour fields (spec S35-S37).

`build_contours` on a re-entrant-corner study and a plate-with-hole study must:

  * Raw Stress -- never altered (byte-identical to the finest-mesh field)
  * Singularity Confidence [%] -- in 0..100
  * Singularity-Filtered Stress:
      - corner: the confidence>=70 nodes are RECOVERED to a lower value, built
        only from converged neighbours; no invented values
      - low-confidence nodes: filtered == raw exactly
      - plate: nothing flagged -> filtered == raw everywhere

Reuses an existing study (with confidence_field.npz) if present.
Skips when Ansys / DPF are unavailable.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _study_with_confidence(geo_stem):
    for d in sorted((REPO / "artifacts").glob("*_study_*"), reverse=True):
        sr = d / "study_result.json"
        if not sr.is_file():
            continue
        try:
            s = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (s.get("status") == "PASS" and geo_stem in str(s.get("geometry", ""))
                and (d / "confidence_field.npz").is_file()):
            return d
    return None


def _prepare(geo_name):
    geo_stem = Path(geo_name).stem
    d = _study_with_confidence(geo_stem)
    if d is None:
        geo = REPO / "test_models" / geo_name
        if not geo.is_file():
            pytest.skip("{0} missing and no prepared study".format(geo_name))
        try:
            from devtools import compatibility, detect_ansys
            detect_ansys.resolve("252")
            if not compatibility.probe_dpf().available:
                pytest.skip("DPF unavailable")
        except Exception as exc:  # noqa: BLE001
            pytest.skip("prereqs: {0}".format(exc))
        proc = subprocess.run(
            [sys.executable, "-u", "-m", "devtools.study_controller", "--geometry", str(geo),
             "--setup", "clean_tension", "--ansys-version", "252",
             "--sizes", "0.004,0.003,0.0022,0.0016,0.0012"],
            cwd=str(REPO), capture_output=True, text=True, timeout=3600)
        assert "status              : PASS" in proc.stdout, proc.stdout[-1200:]
        d = sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)[-1]
        from devtools import analyze_study
        analyze_study.analyze(str(d))
    from devtools import build_contours
    summary = build_contours.build(str(d), make_png=False)
    z = np.load(d / "contour_fields.npz", allow_pickle=True)
    return d, summary, z


@pytest.fixture(scope="module")
def corner():
    return _prepare("lbracket.stp")


@pytest.fixture(scope="module")
def plate():
    return _prepare("plate_hole.stp")


def test_raw_stress_is_untouched(corner):
    d, _, z = corner
    src = np.load(d / "confidence_field.npz", allow_pickle=True)
    assert np.array_equal(z["raw_stress"], src["raw_stress_finest"])


def test_confidence_field_in_range(corner):
    _, _, z = corner
    c = z["confidence"]
    assert c.min() >= 0.0 and c.max() <= 100.0


def test_corner_region_is_recovered_downwards(corner):
    d, summary, z = corner
    st = z["recovery_status"]
    rec = st == "recovered"
    assert rec.sum() >= 5, "expected a flagged region at the corner"
    assert (z["recovery_status"] == "not_recoverable").sum() == 0
    rr, rf = z["raw_stress"][rec], z["filtered_stress"][rec]
    assert np.all(np.isfinite(rf))
    assert rf.mean() < rr.mean()                          # pulled toward converged neighbours
    assert summary["recovered_region"]["mean_reduction_pct"] > 3.0


def test_low_confidence_nodes_keep_raw_value(corner):
    _, _, z = corner
    low = z["confidence"] < 70.0
    assert np.array_equal(z["filtered_stress"][low], z["raw_stress"][low])


def test_plate_with_hole_filters_nothing(plate):
    d, summary, z = plate
    assert summary["recovery"]["n_recovered"] == 0
    assert summary["recovery"]["n_not_recoverable"] == 0
    assert np.array_equal(z["filtered_stress"], z["raw_stress"])


def test_outputs_written(corner):
    d, _, _ = corner
    assert (d / "contour_fields.csv").is_file()
    assert (d / "contour_fields.npz").is_file()
    s = json.loads((d / "contours_summary.json").read_text(encoding="utf-8"))
    assert "estimate" in s["fields"]["Singularity-Filtered Stress"]["note"]
