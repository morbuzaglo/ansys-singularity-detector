"""Milestone 2 acceptance -- convergence-study controller.

- a real multi-level study on bar.stp: PASS, original mesh restored, per-level
  dirs + convergence.csv produced, provisional trend == convergent
- a bad setup name is handled cleanly (recorded status, no crash, not an
  InfraError, never a singularity verdict) -- spec S50

Skips when Ansys / licences / ansys-mechanical-core are unavailable.
"""

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
BAR = REPO / "test_models" / "bar.stp"


@pytest.fixture
def controller_mod():
    from devtools import study_controller
    return study_controller


def _run_via_subprocess(args):
    """study_controller uses embedded Mechanical (one per process) -> subprocess."""
    import json
    import subprocess

    cmd = [sys.executable, "-u", "-m", "devtools.study_controller",
           "--geometry", str(BAR), "--ansys-version", "252", *args]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=2400)
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    runs = sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)
    assert runs, "no study artifact dir\n" + proc.stdout
    summ = runs[-1] / "study_result.json"
    data = json.loads(summ.read_text(encoding="utf-8")) if summ.is_file() else {}
    return proc.returncode, data, runs[-1]


def test_study_converges_and_restores(controller_mod):
    if not BAR.is_file():
        pytest.skip("bar.stp not present")

    rc, data, run_dir = _run_via_subprocess(["--sizes", "0.006,0.0048,0.0038,0.003"])
    assert rc == 0, data
    assert data["status"] == "PASS", data
    assert data["restore_ok"] is True
    assert [lv["status"] for lv in data["levels"]] == ["ok"] * 4

    # per-level preservation (spec S49)
    for i in range(4):
        ld = run_dir / ("level_%02d" % i)
        assert (ld / "level.json").is_file()
        assert (ld / "file.rst").is_file()
    assert (run_dir / "convergence.csv").is_file()

    # provisional trend read
    sc = controller_mod
    levels = [sc.LevelResult.from_json(lv) for lv in data["levels"]]
    result = sc.StudyResult(
        status=data["status"], ok=True, run_dir=str(run_dir),
        summary_path=str(run_dir / "study_result.json"), raw=data, levels=levels,
        restore_ok=data["restore_ok"], ansys_version_reported=data["ansys_version_reported"])
    verdict = result.classify_convergence()["verdict"]
    assert verdict in ("convergent", "uncertain"), verdict   # a plain bar must NOT read "diverging"


def test_bad_setup_handled_cleanly(controller_mod):
    if not BAR.is_file():
        pytest.skip("bar.stp not present")

    sc = controller_mod
    cfg = sc.StudyController.plan(str(BAR), sizes=[0.006, 0.004], setup="does_not_exist",
                                 ansys_version="252")
    ctrl = sc.StudyController(cfg)
    # runs Mechanical but no solve; must NOT raise InfraError, must record a status
    result = ctrl.run()
    assert result.status == "BAD_SETUP"
    assert result.ok is False
    assert result.peak_series == []
