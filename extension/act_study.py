# -*- coding: utf-8 -*-
# ==========================================================================
# act_study -- Mechanical runtime layer (IronPython 2.7).
#
# Milestone 9: run the mesh-refinement convergence study INSIDE the user's
# live Mechanical session, on their EXISTING Static Structural analysis
# (spec S4 -- "operates from within an existing Static Structural analysis").
#
# It does NOT touch the user's geometry / BCs / loads. It only:
#   * captures the original mesh controls (spec S9)
#   * for each requested characteristic element size: apply -> generate ->
#     verify -> measure -> solve -> verify -> extract -> preserve level_XX/
#     {level.json, file.rst}
#   * restores the original mesh controls
#   * writes <run_dir>/study_result.json  (same schema as the headless
#     devtools/study_runner so the external analysis steps consume it as-is)
#
# The heavy numpy/DPF analysis (analyze_study / build_contours /
# convergence_charts) runs afterwards in the external venv -- see
# extension/main.py :: _run_external.
#
# NO f-strings / dataclasses / type hints.
# ==========================================================================

import os
import sys
import json
import time
import shutil
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mech_env             # noqa: E402


def _bind_globals(g):
    """Bind engine names from ``g`` into mech_env -- but only real values, never
    overwrite an already-good binding with None (the caller, e.g. main.py, may
    have bound them already)."""
    m = {}
    for name in ("ExtAPI", "Model", "Quantity", "Ansys", "LoadDefineBy"):
        v = g.get(name)
        if v is None:
            try:
                v = eval(name, g)
            except Exception:
                v = None
        if v is not None:
            m[name] = v
    if m:
        mech_env.bind(m)


def _E():
    return mech_env.G.require("ExtAPI")


def _write_json(path, obj):
    f = open(path, "w")
    try:
        f.write(json.dumps(obj, indent=2, sort_keys=True))
    finally:
        f.close()


def _preserve_level(run_dir, analysis, level):
    ld = os.path.join(run_dir, "level_%02d" % level["index"])
    if not os.path.isdir(ld):
        os.makedirs(ld)
    rst = None
    try:
        src = os.path.join(analysis.WorkingDir, "file.rst")
        if os.path.isfile(src):
            rst = os.path.join(ld, "file.rst")
            shutil.copy2(src, rst)
    except Exception:
        rst = None
    level["result_path"] = rst
    level["level_dir"] = ld
    _write_json(os.path.join(ld, "level.json"), level)
    return ld


def _version():
    for get in (lambda: str(_E().DataModel.Project.ProductVersion),
                lambda: str(_E().Application.VersionInfo)):
        try:
            return get()
        except Exception:
            pass
    return "unknown"


def _messages():
    out = []
    try:
        for m in _E().Application.Messages:
            try:
                out.append("[%s] %s" % (str(m.Severity), str(m.DisplayString)))
            except Exception:
                out.append(str(m))
    except Exception:
        pass
    return out


def run(analysis, sizes_m, run_dir, progress=None, extra_bc_geometry=None,
        restore=False):
    """Run the study on `analysis` (an existing Static Structural analysis).

    sizes_m  : list of characteristic element sizes in metres, decreasing.
    run_dir  : directory to write study_result.json + level_XX/ into.
    progress : optional callable(fraction_0_1, message).
    restore  : if False (default, spec S7) the model is left MESHED + SOLVED at
               the finest level, and an sd_original_mesh.json snapshot is written
               for the separate "Restore Original Mesh" action; if True the
               original mesh controls are re-applied here.
    Returns the summary dict (also written to disk).
    """
    _bind_globals(globals())
    import mesh_manager
    import result_extractor

    def _p(frac, msg):
        if progress is not None:
            try:
                progress(frac, msg)
            except Exception:
                pass
        try:
            _E().Log.WriteMessage("[SingularityDetector] " + msg)
        except Exception:
            pass

    if not os.path.isdir(run_dir):
        os.makedirs(run_dir)

    model = _E().DataModel.Project.Model
    summary = {
        "milestone": 9,
        "script": "act_study.py",
        "in_session": True,
        "status": "ERROR",
        "ansys_version_reported": _version(),
        "analysis_name": str(getattr(analysis, "Name", "analysis")),
        "requested_sizes_m": list(sizes_m),
        "levels": [],
        "steps": [],
        "restore_ok": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra_bc_geometry:
        summary["bc_face_geometry"] = extra_bc_geometry
    step = summary["steps"].append
    mm = None

    try:
        if len(sizes_m) < 2:
            summary["status"] = "BAD_PLAN"
            _write_json(os.path.join(run_dir, "study_result.json"), summary)
            return summary

        mm = mesh_manager.MeshManager(model)
        summary["original_mesh"] = mm.capture_original()
        try:
            _write_json(os.path.join(run_dir, "sd_original_mesh.json"), mm.snapshot())
            _write_json(os.path.join(analysis.WorkingDir, "sd_original_mesh.json"),
                        mm.snapshot())
        except Exception:
            pass
        step("original-captured")

        sol = analysis.Solution
        rs = result_extractor.ResultSet(analysis, fixed_support=None)
        step("results-added")

        n = len(sizes_m)
        stop_reason = None
        for i, h in enumerate(sizes_m):
            _p(0.05 + 0.9 * i / n, "mesh level %d/%d  (h = %.4g m)" % (i + 1, n, h))
            lvl = {"index": i, "requested_size_m": h}
            t0 = time.time()
            mm.apply_size(h)
            gsec = mm.generate()
            lvl.update(mm.stats(generate_seconds=gsec))
            lvl["mesh_wall_seconds"] = round(time.time() - t0, 2)
            if lvl["nodes"] <= 0 or lvl["elements"] <= 0:
                lvl["status"] = "MESH_FAILED"
                lvl["messages"] = _messages()
                summary["levels"].append(lvl)
                _preserve_level(run_dir, analysis, lvl)
                stop_reason = "MESH_FAILED"
                break

            t1 = time.time()
            try:
                sol.Solve(True)
            except Exception:
                lvl["solve_exception"] = traceback.format_exc()
            st = str(sol.Status)
            lvl["solve_status"] = st
            lvl["solve_wall_seconds"] = round(time.time() - t1, 2)
            if st != "Done":
                lvl["status"] = "SOLVE_FAILED"
                lvl["messages"] = _messages()
                summary["levels"].append(lvl)
                _preserve_level(run_dir, analysis, lvl)
                stop_reason = "SOLVE_FAILED"
                break

            lvl["results"] = rs.read()
            lvl["status"] = "ok"
            summary["levels"].append(lvl)
            _preserve_level(run_dir, analysis, lvl)
            _write_json(os.path.join(run_dir, "study_result.json"), summary)  # checkpoint
            step("level-%d-ok" % i)

        # tidy the temporary SD_ result objects off the user's tree (spec S9)
        try:
            rs.remove()
            step("temp-results-removed")
        except Exception:
            pass

        if restore:
            try:
                summary["restore_ok"] = bool(mm.restore_original())
            except Exception:
                summary["restore_ok"] = False
            step("mesh-restored")
        else:
            # spec S7: leave the model meshed + solved at the finest level; the
            # last loop iteration already did that. Re-solve if it went stale.
            summary["left_at_finest_mesh"] = True
            try:
                if str(sol.Status) != "Done":
                    sol.Solve(True)
            except Exception:
                pass
            step("left-at-finest")

        ok = [lv for lv in summary["levels"] if lv.get("status") == "ok"]
        peaks = []
        for lv in ok:
            pe = (lv.get("results") or {}).get("peak_equivalent_stress")
            if pe:
                peaks.append(pe["value"])
        summary["peak_equivalent_stress_series"] = peaks

        summary["status"] = stop_reason if stop_reason else "PASS"
        _write_json(os.path.join(run_dir, "study_result.json"), summary)
        _p(0.98, "study %s (%d levels)" % (summary["status"], len(ok)))
        return summary

    except Exception:
        summary["status"] = "EXCEPTION"
        summary["traceback"] = traceback.format_exc()
        if mm is not None:
            try:
                summary["restore_ok"] = bool(mm.restore_original())
            except Exception:
                summary["restore_ok"] = False
        try:
            _write_json(os.path.join(run_dir, "study_result.json"), summary)
        except Exception:
            pass
        return summary
