"""Unit tests for devtools.study_controller pure logic -- no Ansys.

Covers StudyResult parsing, convergence classification, CSV writing, and the
cleanup safety guard (spec S49).
"""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import study_controller as sc  # noqa: E402


def _level(i, h, n_el, peak, status="ok"):
    return {
        "index": i, "requested_size_m": h, "actual_char_size_m": h * 1.02,
        "nodes": n_el * 8, "elements": n_el, "generate_seconds": 1.0,
        "status": status,
        "results": {
            "peak_equivalent_stress": {"value": peak, "unit": "MPa"},
            "max_total_deformation": {"value": 0.005, "unit": "mm"},
            "reaction_at_fixed_support": {"value": 1000.0, "unit": "N"},
            "solve_seconds": 12.0,
        },
    }


def _summary(levels, status="PASS", restore_ok=True):
    return {
        "milestone": 2, "status": status, "restore_ok": restore_ok,
        "ansys_version_reported": "2025 R2", "setup": "axial_bar",
        "levels": levels,
    }


def _result_from(tmp_path, summary):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sp = run_dir / "study_result.json"
    sp.write_text(json.dumps(summary), encoding="utf-8")
    levels = [sc.LevelResult.from_json(lv) for lv in summary["levels"]]
    return sc.StudyResult(
        status=summary["status"], ok=(summary["status"] in ("PASS", "NO_GEOMETRY")),
        run_dir=str(run_dir), summary_path=str(sp), raw=summary, levels=levels,
        restore_ok=summary["restore_ok"], ansys_version_reported="2025 R2",
    )


def test_level_parse():
    lv = sc.LevelResult.from_json(_level(0, 0.01, 100, 12.3))
    assert lv.index == 0 and lv.elements == 100
    assert lv.peak_eqv["value"] == 12.3
    assert lv.reaction["value"] == 1000.0
    assert lv.solve_seconds == 12.0


def test_peak_series_only_ok_levels(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.008, 200, 11.0),
                  _level(2, 0.006, 300, 20.0, status="SOLVE_FAILED")])
    r = _result_from(tmp_path, s)
    assert r.peak_series == [10.0, 11.0]


def test_classify_convergent(tmp_path):
    # decelerating increments, small relative last step
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 11.0),
                  _level(2, 0.0056, 350, 11.4), _level(3, 0.0042, 600, 11.5)])
    v = _result_from(tmp_path, s).classify_convergence()
    assert v["verdict"] == "convergent", v


def test_classify_diverging(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 14.0),
                  _level(2, 0.0056, 350, 22.0), _level(3, 0.0042, 600, 40.0)])
    v = _result_from(tmp_path, s).classify_convergence()
    assert v["verdict"] == "diverging", v


def test_classify_insufficient(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 11.0)])
    assert _result_from(tmp_path, s).classify_convergence()["verdict"] == "insufficient_levels"


def test_write_csv(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 11.0),
                  _level(2, 0.0056, 350, 11.4)])
    r = _result_from(tmp_path, s)
    cfg = sc.StudyConfig(geometry="x.stp", sizes=[0.01, 0.0075, 0.0056])
    path = sc.StudyController(cfg).write_csv(r)
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["peak_eqv"] == "10.0"
    assert rows[2]["elements"] == "350"
    assert rows[0]["status"] == "ok"


def test_cleanup_refuses_without_csv(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 11.0)])
    r = _result_from(tmp_path, s)
    (Path(r.run_dir) / "level_00").mkdir()
    cfg = sc.StudyConfig(geometry="x.stp", sizes=[0.01, 0.0075])
    with pytest.raises(RuntimeError):
        sc.StudyController(cfg).cleanup(r)          # no convergence.csv yet


def test_cleanup_keeps_rst_dirs(tmp_path):
    s = _summary([_level(0, 0.01, 100, 10.0), _level(1, 0.0075, 200, 11.0)])
    r = _result_from(tmp_path, s)
    ctrl = sc.StudyController(sc.StudyConfig(geometry="x.stp", sizes=[0.01, 0.0075], keep_rst=True))
    ctrl.write_csv(r)
    d0 = Path(r.run_dir) / "level_00"; d0.mkdir(); (d0 / "file.rst").write_bytes(b"rst")
    d1 = Path(r.run_dir) / "level_01"; d1.mkdir()   # no rst
    removed = ctrl.cleanup(r)
    assert d0.is_dir()            # kept (has rst)
    assert not d1.is_dir()        # removed (no rst)
    assert str(d1) in removed


def test_plan_builds_config():
    cfg = sc.StudyController.plan("test_models/bar.stp", h0=0.01, refinements=3, ratio=0.75)
    assert cfg.geometry.endswith("bar.stp")
    assert len(cfg.sizes) == 4
    assert cfg.sizes[0] == pytest.approx(0.01)
    assert cfg.plan_meta["strategy"] == "global_element_size"


def test_plan_explicit_sizes():
    cfg = sc.StudyController.plan("g.stp", sizes=[0.02, 0.01, 0.005])
    assert cfg.sizes == [0.02, 0.01, 0.005]
