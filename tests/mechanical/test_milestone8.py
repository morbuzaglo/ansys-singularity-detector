"""Milestone 8 acceptance -- convergence-visualisation charts (spec S39).

`convergence_charts.build` on an analysed re-entrant-corner study must produce
the global charts + CSV and, for the one region, the per-region stress-vs-h /
log-log / model-fit / secondary-trend charts, each with its CSV.

Reuses a prepared+analysed study; skips when unavailable.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _analysed_study(geo_stem):
    for d in sorted((REPO / "artifacts").glob("*_study_*"), reverse=True):
        sr = d / "study_result.json"
        an = d / "singularity_analysis.json"
        if not (sr.is_file() and an.is_file()):
            continue
        try:
            s = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.get("status") == "PASS" and geo_stem in str(s.get("geometry", "")):
            return d
    return None


@pytest.fixture(scope="module")
def corner_charts():
    try:
        from devtools import compatibility
        if not compatibility.probe_dpf().available:
            pytest.skip("DPF unavailable")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("DPF: {0}".format(exc))
    d = _analysed_study("lbracket")
    if d is None:
        pytest.skip("no analysed lbracket study present (run milestones 2/4/6 first)")
    from devtools import convergence_charts
    idx = convergence_charts.build(str(d))
    return d, idx


def test_global_charts_and_csv(corner_charts):
    d, idx = corner_charts
    c = d / "charts"
    assert (c / "global_convergence.csv").is_file()
    for name in ("global_peak_stress_vs_h.png", "global_peak_stress_vs_nelem.png",
                 "global_max_deformation_vs_h.png", "global_reaction_imbalance_vs_h.png"):
        assert (c / name).is_file(), name
    rows = list(csv.DictReader(open(c / "global_convergence.csv", encoding="utf-8")))
    assert len(rows) >= 4
    hs = [float(r["char_elem_size_m"]) for r in rows]
    assert hs == sorted(hs, reverse=True)                    # coarse -> fine
    ne = [int(float(r["n_elements"])) for r in rows]
    assert ne[0] < ne[-1]                                    # refinement really refined


def test_region_charts_for_the_corner(corner_charts):
    d, idx = corner_charts
    assert idx["n_regions"] >= 1
    c = d / "charts"
    for name in ("region_0_convergence.csv", "region_0_stress_vs_h.png",
                 "region_0_increment_loglog.png"):
        assert (c / name).is_file(), name
    rows = list(csv.DictReader(open(c / "region_0_convergence.csv", encoding="utf-8")))
    peak = np.array([float(r["peak_eqv_stress"]) for r in rows])
    assert peak[-1] > peak[0]                                # corner stress climbs with refinement


def test_secondary_trend_charts_present(corner_charts):
    d, idx = corner_charts
    c = d / "charts"
    # these need secondary_metrics.json; present after milestone 5/6
    if (d / "secondary_metrics.json").is_file():
        assert (c / "region_0_nodal_fraction_vs_level.png").is_file()
        assert (c / "region_0_energy_error_vs_level.png").is_file()


def test_charts_index_lists_everything(corner_charts):
    d, idx = corner_charts
    listed = set((d / "charts").glob("*.png"))
    assert len(listed) == len([n for n in idx["charts"] if n.endswith(".png")])
