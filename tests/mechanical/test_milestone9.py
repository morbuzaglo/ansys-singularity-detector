"""Milestone 9 acceptance -- the in-session study loop the ACT button runs.

`extension/act_study.run(analysis, sizes, run_dir)` runs the mesh-refinement
study on an EXISTING analysis inside a live Mechanical session (spec S4/S9):

  * it does NOT create geometry / BCs -- it uses the analysis it is given
  * per-level level_XX/{level.json, file.rst} are preserved
  * study_result.json has the same schema the external analysis steps consume
  * the user's original mesh controls are restored

Run via a subprocess probe (embedded Mechanical is one-per-process).
Skips when Ansys / ansys-mechanical-core are unavailable.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "devtools" / "_act_study_probe.py"
BAR = REPO / "test_models" / "lbracket.stp"


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if not BAR.is_file():
        pytest.skip("lbracket.stp missing")
    try:
        from devtools import detect_ansys, pymechanical_runner
        detect_ansys.resolve("252")
        if not pymechanical_runner.available():
            pytest.skip("ansys-mechanical-core not installed")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("prereqs: {0}".format(exc))

    run_dir = tmp_path_factory.mktemp("m9") / "actstudy"
    proc = subprocess.run(
        [sys.executable, "-u", str(PROBE), str(BAR), str(run_dir),
         "0.006", "0.0045", "0.0035", "0.0028"],
        cwd=str(REPO), capture_output=True, text=True, timeout=2400)
    print(proc.stdout[-3000:])
    print(proc.stderr[-3000:], file=sys.stderr)
    assert "SUMMARY_JSON_BEGIN" in proc.stdout, proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    blob = proc.stdout.split("SUMMARY_JSON_BEGIN\n", 1)[1].split("\nSUMMARY_JSON_END", 1)[0]
    return json.loads(blob), Path(run_dir)


def test_in_session_study_passes(result):
    summ, run_dir = result
    assert summ["status"] == "PASS", summ
    assert summ["in_session"] is True
    assert len(summ["levels"]) == 4
    assert [lv["status"] for lv in summ["levels"]] == ["ok"] * 4
    assert summ["steps"][-1] == "mesh-restored"


def test_original_mesh_restored(result):
    summ, _ = result
    assert summ["restore_ok"] is True
    assert "original_mesh" in summ


def test_per_level_rst_and_json_preserved(result):
    summ, run_dir = result
    assert (run_dir / "study_result.json").is_file()
    for i in range(4):
        ld = run_dir / ("level_%02d" % i)
        assert (ld / "level.json").is_file()
        assert (ld / "file.rst").is_file()


def test_schema_is_consumable_by_the_external_pipeline(result):
    summ, run_dir = result
    # analyze_study only needs level_*/file.rst + actual_char_size_m + peak stress
    from devtools import cross_mesh
    series = cross_mesh.from_study_dir(str(run_dir))
    assert series.n_levels == 4
    assert series.valid.mean() > 0.9


def test_refinement_actually_refined(result):
    summ, _ = result
    ne = [lv["elements"] for lv in summ["levels"]]
    assert ne[0] < ne[-1]
