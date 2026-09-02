"""Milestone 1 acceptance -- parametrised mesh/solve sweep.

Runs ``devtools/run_milestone1.py`` in a subprocess against ``test_models/bar.stp``
and checks: every level solved, per-level ``file.rst`` was preserved, global
equilibrium held (reaction == applied load), and the plain bar's peak stress
*converges* (decelerating increments) rather than diverging.

Skips when Ansys / licences / ansys-mechanical-core are unavailable.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "devtools" / "run_milestone1.py"
BAR = REPO / "test_models" / "bar.stp"


def _run(extra):
    cmd = [sys.executable, "-u", str(RUNNER),
           "--geometry", str(BAR), "--ansys-version", "252", *extra]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    runs = sorted((REPO / "artifacts").glob("*_milestone1_*"), key=lambda p: p.stat().st_mtime)
    assert runs, "no milestone1 artifact dir\n" + proc.stdout
    data = json.loads((runs[-1] / "milestone_result.json").read_text(encoding="utf-8"))
    return proc.returncode, data, runs[-1]


def test_sweep_converges_on_plain_bar():
    if not BAR.is_file():
        pytest.skip("test_models/bar.stp not present")

    rc, data, run_dir = _run(["--sizes", "0.005,0.004,0.003"])
    assert rc == 0, data
    assert data["status"] == "PASS", data

    levels = data["levels"]
    assert len(levels) == 3
    assert [lv["status"] for lv in levels] == ["ok", "ok", "ok"]
    assert data["steps"][-1] == "mesh-restored"

    # refinement really refined
    n = [lv["elements"] for lv in levels]
    assert n[0] < n[1] <= n[2], n

    for i, lv in enumerate(levels):
        r = lv["results"]
        assert r["solve_status"] == "Done"
        # global equilibrium: reaction magnitude == applied 1000 N (spec S32)
        assert abs(r["reaction_at_fixed_support"]["value"] - 1000.0) < 1.0
        # per-level result file preserved (spec S49)
        assert (run_dir / ("level_%02d" % i) / "file.rst").is_file()

    # plain bar: peak stress CONVERGES -> |increments| decrease -> ratio < 1
    peaks = data["peak_equivalent_stress_series"]
    assert len(peaks) == 3
    d1, d2 = abs(peaks[1] - peaks[0]), abs(peaks[2] - peaks[1])
    assert d1 > 0
    assert d2 < d1, "increments not decreasing: {0} then {1}".format(d1, d2)
    assert data["increment_ratio"] < 1.0

    # deformation is essentially converged (analytic delta = FL/AE = 5 um)
    defs_um = [lv["results"]["max_total_deformation"]["value"] * 1000.0 for lv in levels]
    assert all(4.5 < d < 5.5 for d in defs_um), defs_um
