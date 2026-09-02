"""Milestone 0 acceptance -- the autonomous Ansys loop actually runs.

    terminal -> launch Mechanical -> run script -> JSON sentinel -> exit

Each test shells out to ``devtools/run_milestone0.py`` in its own process:
embedded Mechanical is one-per-process, so in-process reuse across tests is
unreliable -- a fresh subprocess per test is both correct and closer to how the
runner is really used.

Skips cleanly when Ansys / licenses / ansys-mechanical-core are unavailable.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.mechanical

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "devtools" / "run_milestone0.py"


def _run_milestone0(extra_args):
    """Invoke the runner in a subprocess; return (returncode, parsed sentinel dict)."""
    cmd = [sys.executable, "-u", str(RUNNER),
           "--ansys-version", "252", "--backend", "pymechanical", *extra_args]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=900)
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)

    # newest milestone0 artifact dir
    runs = sorted((REPO / "artifacts").glob("*_milestone0_*"), key=lambda p: p.stat().st_mtime)
    assert runs, "runner produced no artifact directory\n" + proc.stdout
    sentinel = runs[-1] / "milestone_result.json"
    assert sentinel.is_file(), "no sentinel in {0}\n{1}".format(runs[-1], proc.stdout)
    return proc.returncode, json.loads(sentinel.read_text(encoding="utf-8"))


def test_plumbing_no_geometry(ansys_install, license_ok, pymechanical_available):
    rc, data = _run_milestone0([])
    assert rc == 0
    assert data["status"] in {"NO_GEOMETRY", "PASS"}
    assert data["milestone"] == 0
    assert "engine-up" in data.get("steps", [])


@pytest.mark.benchmark
def test_full_solve_with_bar_fixture(ansys_install, license_ok, pymechanical_available):
    bar = REPO / "test_models" / "bar.stp"
    if not bar.is_file():
        pytest.skip("test_models/bar.stp fixture not present yet")

    rc, data = _run_milestone0(["--geometry", str(bar), "--element-size", "0.005"])
    assert rc == 0, data
    assert data["status"] == "PASS", data
    assert data["steps"][-1] == "results-read"
    assert data["mesh"]["nodes"] > 0
    assert data["solve_status"] == "Done"

    # 100x10x10 mm bar, 1000 N axial on 1e-4 m^2 -> ~10 MPa nominal; delta=FL/AE=5 um.
    peak = data["peak_equivalent_stress"]["max"]
    defo = data["max_total_deformation"]["max"]
    assert 8.0 < peak < 25.0, "peak eqv stress {0} MPa outside sanity band".format(peak)
    assert 0.003 < defo < 0.008, "max deformation {0} mm outside sanity band".format(defo)
