"""Milestone 5 acceptance -- secondary metrics separate singular vs convergent.

Runs a clean-tension study on the L-bracket (re-entrant corner -> singular) and
the plate-with-hole (smooth -> convergent), then `secondary_metrics.assemble`
on each and checks:

  S28 nodal-difference persistence   -- high for the corner, ~0 for the plate
  S30 hotspot localisation           -- corner: hot region shrinks + peak grows
  S31 geometry / BC prior            -- corner is near a re-entrant edge
  S32 global-solution sanity gate    -- both solutions are stable (gate ~1)

Skips when Ansys / licences / DPF are unavailable.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
SIZES = "0.004,0.003,0.0022,0.0016,0.0012"


def _study(geo_name):
    geo = REPO / "test_models" / geo_name
    if not geo.is_file():
        pytest.skip("{0} missing".format(geo_name))
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
    print(proc.stdout[-1500:])
    assert "status              : PASS" in proc.stdout, proc.stdout[-1500:]
    return sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)[-1]


@pytest.fixture(scope="module")
def evd():
    from devtools import secondary_metrics
    corner = secondary_metrics.assemble(str(_study("lbracket.stp")))
    plate = secondary_metrics.assemble(str(_study("plate_hole.stp")))
    return {"corner": corner, "plate": plate}


def test_nodal_disagreement_persists_for_corner_only(evd):
    assert evd["corner"].s28_nodal_disagreement >= 0.5
    assert evd["plate"].s28_nodal_disagreement < 0.25
    dc = evd["corner"].detail["disagreement"]
    assert dc["disagreement_persistence"] > 1.4      # near decays slower than bulk


def test_hotspot_localises_for_corner_only(evd):
    assert evd["corner"].s30_hotspot_localisation >= 0.5
    assert evd["plate"].s30_hotspot_localisation < 0.3
    lc = evd["corner"].detail["localisation"]
    assert lc["peak_growing"] is True
    assert lc["hot_fraction_ratio_last_first"] < 0.7


def test_geometry_prior_flags_reentrant_edge(evd):
    assert evd["corner"].s31_geometry_prior >= 0.4
    assert evd["corner"].detail["geometry_prior"]["n_reentrant_edge_points"] > 0
    assert evd["plate"].s31_geometry_prior < 0.2


def test_global_sanity_gate_open_for_both(evd):
    # both are well-behaved linear-elastic solves -> the gate must NOT penalise
    assert evd["corner"].s32_solution_stable > 0.9
    assert evd["plate"].s32_solution_stable > 0.9


def test_secondary_json_written(evd):
    # assemble() writes secondary_metrics.json next to study_result.json
    studies = sorted((REPO / "artifacts").glob("*_study_*"))
    written = [s for s in studies if (s / "secondary_metrics.json").is_file()]
    assert len(written) >= 2
    assert evd["corner"].detail["hotspot_xyz"] is not None
