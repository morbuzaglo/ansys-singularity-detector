# -*- coding: utf-8 -*-
# ==========================================================================
# study_runner -- Mechanical runtime layer (IronPython 2.7).
#
# Milestone 2: the automated convergence-study loop (spec S22).
#
#   build analysis (via a named setup)  -- once
#   add result objects                  -- once
#   for each requested characteristic element size:
#       apply size -> generate -> verify -> measure ->
#       solve -> verify -> extract -> PRESERVE level_XX/{level.json, file.rst}
#   restore the user's original mesh settings (spec S9)
#   write <run_dir>/<sentinel> summarising every level
#
# Clean failure (spec S50): a mesh/solve failure is RECORDED, the loop stops,
# the mesh is still restored, the summary is still written, rc != 0.  A failed
# level is never classified as singular / non-singular.
#
# ENV IN
#   SD_RUN_DIR, SD_SENTINEL(=study_result.json), SD_EXT_DIR
#   SD_GEOMETRY, SD_ELEM_SIZES (csv metres, decreasing)
#   SD_SETUP (default "axial_bar"), SD_FORCE_N (default 1000)
# ==========================================================================

import os
import sys
import json
import time
import shutil
import traceback

_EXT_DIR = os.environ.get("SD_EXT_DIR", "")
if _EXT_DIR and _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

import mech_env             # noqa: E402

_g = globals()


def _maybe(name):
    if name in _g:
        return _g[name]
    try:
        return eval(name, _g)
    except Exception:
        return None


mech_env.bind({
    "ExtAPI": _maybe("ExtAPI"),
    "Model": _maybe("Model"),
    "Quantity": _maybe("Quantity"),
    "Ansys": _maybe("Ansys"),
    "LoadDefineBy": _maybe("LoadDefineBy"),
})

import model_setup          # noqa: E402
import mesh_manager         # noqa: E402
import result_extractor     # noqa: E402


# -- setup registry: name -> callable(model, geo_path, force_n) ------------
def _setup_axial_bar(model, geo_path, force_n):
    return model_setup.build_axial_bar_analysis(model, geo_path, force_newtons=force_n)


def _setup_clean_tension(model, geo_path, force_n):
    return model_setup.build_clean_tension_analysis(model, geo_path, force_newtons=force_n)


SETUPS = {"axial_bar": _setup_axial_bar, "clean_tension": _setup_clean_tension}


def _run_dir():
    d = os.environ.get("SD_RUN_DIR") or os.getcwd()
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            d = os.getcwd()
    return d


def _sentinel_name():
    return os.environ.get("SD_SENTINEL", "study_result.json")


def _write_json(path, payload):
    f = open(path, "w")
    try:
        f.write(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        f.close()


def _write_summary(payload):
    payload.setdefault("milestone", 2)
    payload.setdefault("script", "study_runner.py")
    payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = os.path.join(_run_dir(), _sentinel_name())
    _write_json(path, payload)
    try:
        ExtAPI.Log.WriteMessage("[study] wrote " + path)      # noqa: F821
    except Exception:
        print("[study] wrote " + path)


def _sizes():
    raw = (os.environ.get("SD_ELEM_SIZES") or "").replace(";", ",")
    return [float(t) for t in raw.split(",") if t.strip()]


def _preserve_level(run_dir, analysis, level):
    ld = os.path.join(run_dir, "level_%02d" % level["index"])
    if not os.path.isdir(ld):
        os.makedirs(ld)
    _write_json(os.path.join(ld, "level.json"), level)
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
    _write_json(os.path.join(ld, "level.json"), level)   # rewrite with paths
    return ld


def _version():
    for get in (lambda: str(ExtAPI.DataModel.Project.ProductVersion),   # noqa: F821
                lambda: str(ExtAPI.Application.VersionInfo)):            # noqa: F821
        try:
            return get()
        except Exception:
            pass
    return "unknown"


def _messages():
    out = []
    try:
        for m in ExtAPI.Application.Messages:      # noqa: F821
            try:
                out.append("[%s] %s" % (str(m.Severity), str(m.DisplayString)))
            except Exception:
                out.append(str(m))
    except Exception:
        pass
    return out


def main():
    run_dir = _run_dir()
    summary = {
        "status": "ERROR",
        "ansys_version_reported": _version(),
        "setup": os.environ.get("SD_SETUP", "axial_bar"),
        "geometry": os.environ.get("SD_GEOMETRY", "").strip(),
        "force_newtons": float(os.environ.get("SD_FORCE_N", "1000") or 1000),
        "requested_sizes_m": _sizes(),
        "levels": [],
        "steps": [],
        "restore_ok": None,
    }
    step = summary["steps"].append
    mm = None

    try:
        geo = summary["geometry"]
        sizes = summary["requested_sizes_m"]
        setup_name = summary["setup"]

        if not geo or not os.path.isfile(geo):
            summary["status"] = "NO_GEOMETRY"
            _write_summary(summary)
            return 0
        if len(sizes) < 2:
            summary["status"] = "BAD_PLAN"
            summary["message"] = "need >= 2 element sizes"
            _write_summary(summary)
            return 1
        if setup_name not in SETUPS:
            summary["status"] = "BAD_SETUP"
            summary["message"] = "unknown setup %r; have %r" % (setup_name, list(SETUPS))
            _write_summary(summary)
            return 1

        model = ExtAPI.DataModel.Project.Model                       # noqa: F821
        analysis, fixed, faces = SETUPS[setup_name](model, geo, summary["force_newtons"])
        summary["faces"] = faces
        step("model-built")

        mm = mesh_manager.MeshManager(model)
        summary["original_mesh"] = mm.capture_original()
        step("original-captured")

        rs = result_extractor.ResultSet(analysis, fixed_support=fixed)
        step("results-added")

        stop_reason = None
        for i, h in enumerate(sizes):
            lvl = {"index": i, "requested_size_m": h}
            t_mesh = time.time()
            mm.apply_size(h)
            gsec = mm.generate()
            lvl.update(mm.stats(generate_seconds=gsec))
            lvl["mesh_wall_seconds"] = round(time.time() - t_mesh, 2)

            if lvl["nodes"] <= 0 or lvl["elements"] <= 0:
                lvl["status"] = "MESH_FAILED"
                lvl["messages"] = _messages()
                summary["levels"].append(lvl)
                _preserve_level(run_dir, analysis, lvl)
                stop_reason = "MESH_FAILED"
                _write_summary(summary)
                break

            t_solve = time.time()
            try:
                analysis.Solution.Solve(True)
            except Exception:
                lvl["solve_exception"] = traceback.format_exc()
            solve_status = str(analysis.Solution.Status)
            lvl["solve_status"] = solve_status
            lvl["solve_wall_seconds"] = round(time.time() - t_solve, 2)

            if solve_status != "Done":
                lvl["status"] = "SOLVE_FAILED"
                lvl["messages"] = _messages()
                summary["levels"].append(lvl)
                _preserve_level(run_dir, analysis, lvl)
                stop_reason = "SOLVE_FAILED"
                _write_summary(summary)
                break

            lvl["results"] = rs.read()
            lvl["status"] = "ok"
            summary["levels"].append(lvl)
            _preserve_level(run_dir, analysis, lvl)
            _write_summary(summary)              # checkpoint every level
            step("level-%d-ok" % i)

        # always try to restore, even after a failure
        try:
            summary["restore_ok"] = bool(mm.restore_original())
        except Exception:
            summary["restore_ok"] = False
        step("mesh-restored")

        ok_levels = [lv for lv in summary["levels"] if lv.get("status") == "ok"]
        peaks = []
        for lv in ok_levels:
            pe = (lv.get("results") or {}).get("peak_equivalent_stress")
            if pe:
                peaks.append(pe["value"])
        summary["peak_equivalent_stress_series"] = peaks

        if stop_reason:
            summary["status"] = stop_reason
            _write_summary(summary)
            return 1

        summary["status"] = "PASS"
        _write_summary(summary)
        return 0

    except Exception:
        summary["status"] = "EXCEPTION"
        summary["traceback"] = traceback.format_exc()
        if mm is not None:
            try:
                summary["restore_ok"] = bool(mm.restore_original())
            except Exception:
                summary["restore_ok"] = False
        try:
            _write_summary(summary)
        except Exception:
            pass
        return 1


_RC = main()
try:
    ExtAPI.Log.WriteMessage("[study] rc=%d" % _RC)      # noqa: F821
except Exception:
    print("[study] rc=%d" % _RC)

if __name__ == "__main__":
    try:
        sys.exit(_RC)
    except SystemExit:
        raise
    except Exception:
        pass
