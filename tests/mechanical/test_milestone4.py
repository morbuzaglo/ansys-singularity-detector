"""Milestone 4 acceptance -- the primary singularity classifier on real solver data.

Spec Milestone 4: "Validate: plate with hole, re-entrant corner. Do not continue
until these cases clearly separate."

Runs a clean-tension convergence study on each (subprocess), classifies the
finest-mesh hotspot neighbourhood, and asserts:

  * plate_with_hole  -> convergent  (finite Kt; low singularity evidence)
  * L-bracket corner -> singular    (diverges; lambda near the mode-I 90deg value)
  * the two evidence scores are clearly separated

Skips when Ansys / licences / DPF are unavailable.
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


def _study_and_classify(geo_name):
    geo = REPO / "test_models" / geo_name
    if not geo.is_file():
        pytest.skip("{0} fixture missing".format(geo_name))
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
    print(proc.stdout[-2000:])
    print(proc.stderr[-1000:], file=sys.stderr)
    assert "status              : PASS" in proc.stdout, proc.stdout[-2000:]

    sd = sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)[-1]
    from devtools import classify_study
    return classify_study.classify_study_dir(str(sd)), sd


@pytest.fixture(scope="module")
def results():
    plate, _ = _study_and_classify("plate_hole.stp")
    corner, _ = _study_and_classify("lbracket.stp")
    return {"plate": plate["hotspot_neighborhood"], "corner": corner["hotspot_neighborhood"],
            "plate_full": plate, "corner_full": corner}


def test_plate_with_hole_converges(results):
    hs = results["plate"]
    assert hs["classification"] == "convergent", hs
    assert hs["singularity_evidence"] < 0.45
    assert hs["extrapolated_limit"] is not None       # a finite Kt limit exists


def test_reentrant_corner_is_singular(results):
    hs = results["corner"]
    assert hs["classification"] == "singular", hs
    assert hs["singularity_evidence"] >= 0.7
    assert not hs["limit_is_trustworthy"]
    lam = hs["divergence_exponent"]
    assert lam is not None and 0.25 <= lam <= 0.75     # mode-I 90deg re-entrant ~ 0.4555


def test_cases_clearly_separate(results):
    gap = results["corner"]["singularity_evidence"] - results["plate"]["singularity_evidence"]
    assert gap > 0.4, (results["plate"]["singularity_evidence"],
                       results["corner"]["singularity_evidence"])
    # the divergent model wins for the corner, the finite model wins for the plate
    assert results["corner"]["divergent_fit"]["rel_resid"] < results["corner"]["finite_fit"]["rel_resid"]
    assert results["plate"]["finite_fit"]["rel_resid"] <= results["plate"]["divergent_fit"]["rel_resid"] + 0.02
