"""Unit tests for extension/main.py's pure helpers -- no Ansys.

main.py is IronPython-flavoured but its config / bridge / settings-fallback
helpers are plain Python; the ACT callbacks that touch ExtAPI are exercised in
tests/mechanical/test_milestone9.py.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "extension"


def _load_main(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(EXT))
    spec = importlib.util.spec_from_file_location("sd_main", EXT / "main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "_HERE", str(tmp_path))
    return m


def test_config_reads_sd_config_json(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    (tmp_path / "sd_config.json").write_text(json.dumps(
        {"repo_path": r"X:\repo", "venv_python": r"X:\repo\.venv\Scripts\python.exe"}),
        encoding="utf-8")
    assert m._config()["repo_path"] == r"X:\repo"
    assert m._repo() == r"X:\repo"


def test_venv_python_prefers_explicit_then_repo(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("")
    (tmp_path / "sd_config.json").write_text(json.dumps(
        {"repo_path": str(tmp_path), "venv_python": str(fake_py)}), encoding="utf-8")
    assert m._venv_python() == str(fake_py)

    (tmp_path / "sd_config.json").write_text(json.dumps({"repo_path": str(tmp_path)}),
                                             encoding="utf-8")
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    derived = tmp_path / ".venv" / "Scripts" / "python.exe"
    derived.write_text("")
    assert m._venv_python() == str(derived)


def test_read_settings_falls_back_to_defaults_and_json(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    # no tree object, no json -> defaults
    s = m._read_settings(None)
    assert s["refinements"] == 3 and s["ratio"] == 0.75 and s["confidence_threshold"] == 70.0
    # json override, with clamping (ratio 0.95 -> 0.90, refinements 20 -> 8)
    (tmp_path / "sd_study_settings.json").write_text(
        json.dumps({"ratio": 0.95, "refinements": 20}), encoding="utf-8")
    s = m._read_settings(None)
    assert s["ratio"] == 0.90
    assert s["refinements"] == 8


def test_plan_sizes_geometric_sequence(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "_current_size_m", lambda model: 0.01)
    sizes = m._plan_sizes(None, {"ratio": 0.75, "refinements": 3})
    assert len(sizes) == 4
    assert sizes[0] == pytest.approx(0.01)
    assert sizes[-1] == pytest.approx(0.01 * 0.75 ** 3)


def test_run_external_builds_module_command(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("")
    (tmp_path / "sd_config.json").write_text(json.dumps(
        {"repo_path": str(tmp_path), "venv_python": str(fake_py)}), encoding="utf-8")

    captured = {}
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class _P:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = kw.get("cwd")
            # the extension writes stdout/stderr to real files (no STDOUT redirect)
            kw["stdout"].write(b"hello\n")

        def wait(self):
            return 0

    monkeypatch.setattr(m.subprocess, "Popen", _P)
    rc, out = m._run_external("analyze_study", str(run_dir))
    assert rc == 0 and "hello" in out
    assert captured["cmd"][:1] == [str(fake_py)]
    assert captured["cmd"][2:] == ["-m", "devtools.analyze_study", str(run_dir)]
    assert captured["cwd"] == str(tmp_path)
    assert (run_dir / "analyze_study.out").is_file()


def test_run_external_without_venv_returns_127(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "_venv_python", lambda: None)
    rc, out = m._run_external("analyze_study", str(tmp_path))
    assert rc == 127 and "venv python not found" in out


def test_guard_turns_exceptions_into_msgbox(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    boxed = {}
    monkeypatch.setattr(m, "_msgbox", lambda text, title="x": boxed.setdefault("t", text))
    monkeypatch.setattr(m, "_bind_engine", lambda: None)

    @m._guard
    def boom(analysis):
        raise ValueError("nope")

    boom(None)                          # must not raise
    assert "boom" in boxed["t"] and "nope" in boxed["t"]


def test_reexports_the_m7_evaluate_callbacks(monkeypatch, tmp_path):
    m = _load_main(monkeypatch, tmp_path)
    for name in ("evaluate_raw_stress", "evaluate_singularity_confidence",
                 "evaluate_singularity_filtered_stress", "add_singularity_contours",
                 "on_init", "run_singularity_study", "add_contours",
                 "restore_original_mesh", "show_settings"):
        assert callable(getattr(m, name))
