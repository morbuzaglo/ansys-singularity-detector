"""Milestone 6 acceptance -- Singularity Confidence score + region clustering.

`analyze_study` runs the whole pipeline (cross_mesh -> classifier -> secondary
metrics -> confidence -> regions) on a re-entrant corner and a plate-with-hole
and must:

  * corner  -> classification "singular", headline confidence in the
               "probable singularity" band (>=70), exactly one region on the
               re-entrant edge with a plausible lambda
  * plate   -> classification "convergent", confidence < 40, zero regions

Reuses an existing clean-tension study for each geometry if present, else runs
one.  Skips when Ansys / licences / DPF are unavailable.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
SIZES = "0.004,0.003,0.0022,0.0016,0.0012"


def _find_study(geo_stem):
    for d in sorted((REPO / "artifacts").glob("*_study_*"), reverse=True):
        sr = d / "study_result.json"
        if not sr.is_file():
            continue
        try:
            s = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (s.get("status") == "PASS" and s.get("setup") == "clean_tension"
                and geo_stem in str(s.get("geometry", ""))
                and len(s.get("levels", [])) >= 5
                and len(list(d.glob("level_*/file.rst"))) >= 5):
            return d
    return None


def _study(geo_name):
    geo = REPO / "test_models" / geo_name
    if not geo.is_file():
        pytest.skip("{0} missing".format(geo_name))
    existing = _find_study(Path(geo_name).stem)
    if existing:
        return existing
    try:
        from devtools import compatibility, detect_ansys
        detect_ansys.resolve("252")
        if not compatibility.probe_dpf().available:
            pytest.skip("DPF unavailable")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("prereqs: {0}".format(exc))
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "devtools.study_controller",
         "--geometry", str(geo), "--setup", "clean_tension",
         "--ansys-version", "252", "--sizes", SIZES],
        cwd=str(REPO), capture_output=True, text=True, timeout=3600)
    assert "status              : PASS" in proc.stdout, proc.stdout[-1500:]
    return sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)[-1]


@pytest.fixture(scope="module")
def analysis():
    from devtools import analyze_study
    try:
        from devtools import compatibility
        if not compatibility.probe_dpf().available:
            pytest.skip("DPF unavailable")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("DPF: {0}".format(exc))
    corner = analyze_study.analyze(str(_study("lbracket.stp")))
    plate = analyze_study.analyze(str(_study("plate_hole.stp")))
    return {"corner": corner, "plate": plate}


def test_reentrant_corner_probable_singularity(analysis):
    h = analysis["corner"]["headline"]
    assert h["classification"] == "singular"
    assert h["singularity_confidence"] >= 70.0
    assert confidence_band(h["singularity_confidence"]) in (
        "probable singularity", "very strong singularity evidence")
    assert h["divergence_exponent"] is not None
    assert 0.3 <= h["divergence_exponent"] <= 0.65


def test_reentrant_corner_one_region_on_the_edge(analysis):
    regs = analysis["corner"]["regions"]
    assert len(regs) == 1
    r = regs[0]
    assert r["max_confidence"] >= 70.0
    assert r["nearest_reentrant_edge_frac"] is not None
    assert r["nearest_reentrant_edge_frac"] < 0.06
    assert "re-entrant" in r["probable_cause"]
    assert r["size_frac"] < 0.15                       # a localised region, not the whole part


def test_plate_with_hole_no_singularity(analysis):
    h = analysis["plate"]["headline"]
    assert h["classification"] == "convergent"
    assert h["singularity_confidence"] < 40.0
    assert analysis["plate"]["n_regions"] == 0
    assert h["extrapolated_limit"] is not None         # a finite Kt


def test_confidence_and_analysis_json_written(analysis):
    for k in ("corner", "plate"):
        d = Path(analysis[k]["study_dir"])
        assert (d / "singularity_analysis.json").is_file()
        assert (d / "confidence_field.npz").is_file()
        assert analysis[k]["confidence"]["note"].startswith("engineering classification score")


def confidence_band(c):
    from devtools import confidence
    return confidence.category(c)
