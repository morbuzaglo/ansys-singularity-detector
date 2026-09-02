# -*- coding: utf-8 -*-
# ==========================================================================
# Milestone 1 -- Mesh/Solve prototype: parametrised element-size sweep.
#
# RUNTIME: Mechanical scripting engine == IronPython 2.7.
#
#   import geometry -> Static Structural + BCs (once)
#   -> for each requested element size:
#        apply size -> generate mesh -> measure -> solve -> extract scalars
#        -> copy file.rst into <run_dir>/level_XX/
#   -> restore original mesh settings
#   -> write <run_dir>/<sentinel> as JSON (per-level arrays)
#
# ENV IN
#   SD_RUN_DIR     dir for the sentinel + level_XX/ result copies
#   SD_SENTINEL    result file name (default milestone_result.json)
#   SD_EXT_DIR     path to the repo's extension/ dir (for imports)
#   SD_GEOMETRY    geometry fixture (.stp/...); REQUIRED for a real sweep
#   SD_ELEM_SIZES  csv of characteristic element sizes in metres, decreasing
#                  (computed by devtools/mesh_plan.py in the orchestrator)
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

# Hand the engine-injected names to the Mechanical-side library modules
# (imported modules do not inherit this script's globals).
_g = globals()


def _maybe(name):
    if name in _g:
        return _g[name]
    try:
        return eval(name, _g)          # resolve CLR namespaces (e.g. Ansys) if not a plain global
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


def _run_dir():
    d = os.environ.get("SD_RUN_DIR") or os.getcwd()
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            d = os.getcwd()
    return d


def _write(payload):
    payload.setdefault("milestone", 1)
    payload.setdefault("script", "milestone1_sweep.py")
    payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = os.path.join(_run_dir(), os.environ.get("SD_SENTINEL", "milestone_result.json"))
    f = open(path, "w")
    try:
        f.write(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        f.close()
    try:
        ExtAPI.Log.WriteMessage("[milestone1] wrote " + path)      # noqa: F821
    except Exception:
        print("[milestone1] wrote " + path)


def _sizes():
    raw = (os.environ.get("SD_ELEM_SIZES") or "").replace(";", ",")
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            out.append(float(tok))
    return out


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


def _copy_rst(analysis, run_dir, level_idx):
    try:
        wd = analysis.WorkingDir
    except Exception:
        return None
    src = os.path.join(wd, "file.rst")
    if not os.path.isfile(src):
        return None
    ld = os.path.join(run_dir, "level_%02d" % level_idx)
    if not os.path.isdir(ld):
        os.makedirs(ld)
    dst = os.path.join(ld, "file.rst")
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None


def main():
    result = {"status": "ERROR", "levels": [], "steps": []}
    step = result["steps"].append
    run_dir = _run_dir()

    try:
        result["ansys_version_reported"] = "unknown"
        for _getter in (
            lambda: str(ExtAPI.DataModel.Project.ProductVersion),   # noqa: F821
            lambda: str(ExtAPI.Application.VersionInfo),            # noqa: F821
        ):
            try:
                result["ansys_version_reported"] = _getter()
                break
            except Exception:
                pass

        sizes = _sizes()
        geo = os.environ.get("SD_GEOMETRY", "").strip()
        result["geometry"] = geo
        result["requested_sizes_m"] = sizes

        if not geo or not os.path.isfile(geo):
            result["status"] = "NO_GEOMETRY"
            result["message"] = "SD_GEOMETRY missing; Milestone 1 needs a fixture."
            _write(result)
            return 0
        if len(sizes) < 2:
            result["status"] = "BAD_PLAN"
            result["message"] = "SD_ELEM_SIZES needs >= 2 sizes, got %r" % sizes
            _write(result)
            return 1

        model = ExtAPI.DataModel.Project.Model                                   # noqa: F821
        analysis, fixed, faces = model_setup.build_axial_bar_analysis(model, geo)
        result["faces"] = faces
        step("model-built")

        mm = mesh_manager.MeshManager(model)
        result["original_mesh"] = mm.capture_original()
        step("original-captured")

        rs = result_extractor.ResultSet(analysis, fixed_support=fixed)
        step("results-added")

        for i, h in enumerate(sizes):
            lvl = {"index": i, "requested_size_m": h}
            mm.apply_size(h)
            gsec = mm.generate()
            st = mm.stats(generate_seconds=gsec)
            lvl.update(st)
            if st["nodes"] <= 0:
                lvl["status"] = "MESH_FAILED"
                result["levels"].append(lvl)
                result["status"] = "MESH_FAILED"
                _write(result)
                return 1

            t0 = time.time()
            try:
                analysis.Solution.Solve(True)
            except Exception:
                lvl["solve_exception"] = traceback.format_exc()
            solve_status = str(analysis.Solution.Status)
            lvl["solve_status"] = solve_status
            lvl["solve_wall_seconds"] = round(time.time() - t0, 2)
            if solve_status != "Done":
                lvl["status"] = "SOLVE_FAILED"
                lvl["messages"] = _messages()
                result["levels"].append(lvl)
                result["status"] = "SOLVE_FAILED"
                _write(result)
                return 1

            diag = rs.read()
            lvl["results"] = diag
            lvl["rst_copy"] = _copy_rst(analysis, run_dir, i)
            lvl["status"] = "ok"
            result["levels"].append(lvl)
            _write(result)     # checkpoint after every level (spec S18: keep evidence)
            step("level-%d-done" % i)

        mm.restore_original()
        step("mesh-restored")

        # quick convergence read-out
        peaks = []
        for lv in result["levels"]:
            pe = lv.get("results", {}).get("peak_equivalent_stress")
            if pe:
                peaks.append(pe["value"])
        result["peak_equivalent_stress_series"] = peaks
        if len(peaks) >= 3:
            d1 = abs(peaks[-2] - peaks[-3])
            d2 = abs(peaks[-1] - peaks[-2])
            result["last_two_increments"] = [d1, d2]
            result["increment_ratio"] = (d2 / d1) if d1 > 0 else None

        result["status"] = "PASS"
        _write(result)
        return 0

    except Exception:
        result["status"] = "EXCEPTION"
        result["traceback"] = traceback.format_exc()
        try:
            _write(result)
        except Exception:
            pass
        return 1


_RC = main()
try:
    ExtAPI.Log.WriteMessage("[milestone1] rc=%d" % _RC)      # noqa: F821
except Exception:
    print("[milestone1] rc=%d" % _RC)

if __name__ == "__main__":
    try:
        sys.exit(_RC)
    except SystemExit:
        raise
    except Exception:
        pass
