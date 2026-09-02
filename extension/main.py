# -*- coding: utf-8 -*-
# ==========================================================================
# main.py -- SingularityDetector ACT extension script (IronPython 2.7).
#
# Milestone 9: the ribbon.  Three buttons, each a ONE-LINER into a tested
# function (spec S21):
#
#   Run Singularity Study  -> run_singularity_study(analysis)
#       in-session mesh-refinement study (extension/act_study.py) ->
#       shell out to the external venv for the numpy/DPF analysis
#       (devtools.analyze_study / build_contours / convergence_charts) ->
#       add + evaluate the three result contours -> summary dialog.
#
#   Add Contours           -> add_contours(analysis)   (M7 -- just add the 3 results)
#   Study Settings         -> show_settings(analysis)   (view/edit sd_study_settings.json)
#
# The extension is IronPython 2.7 with NO numpy; the heavy analysis lives in
# the repo .venv and is invoked as a subprocess.  Deploy config (venv python,
# repo path) is in sd_config.json next to this file, written by
# devtools/extension_deployer.py.
# ==========================================================================

import os
import sys
import json
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# re-export the M7 result callbacks so ACT can resolve them against this module
from visualization import (evaluate_raw_stress,                         # noqa: F401
                           evaluate_singularity_confidence,             # noqa: F401
                           evaluate_singularity_filtered_stress,        # noqa: F401
                           add_singularity_contours)
import visualization
import mech_env


def _bind_engine():
    """Give the imported Mechanical-side modules the engine names ACT injects
    into THIS script's namespace only."""
    g = globals()
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

DEFAULT_SETTINGS = {"refinements": 3, "ratio": 0.75, "confidence_threshold": 70.0,
                    "force_newtons_note": "study uses the model's existing loads/supports"}


# --------------------------------------------------------------------- config
def _config():
    for p in (os.path.join(_HERE, "sd_config.json"),
              os.environ.get("SD_CONFIG", "")):
        if p and os.path.isfile(p):
            try:
                return json.load(open(p))
            except Exception:
                pass
    return {}


def _venv_python():
    c = _config()
    py = c.get("venv_python") or os.environ.get("SD_VENV_PYTHON")
    if py and os.path.isfile(py):
        return py
    repo = c.get("repo_path") or os.environ.get("SD_REPO")
    if repo:
        cand = os.path.join(repo, ".venv", "Scripts", "python.exe")
        if os.path.isfile(cand):
            return cand
    return None


def _repo():
    c = _config()
    return c.get("repo_path") or os.environ.get("SD_REPO") or _HERE


def _settings():
    s = dict(DEFAULT_SETTINGS)
    p = os.path.join(_HERE, "sd_study_settings.json")
    if os.path.isfile(p):
        try:
            s.update(json.load(open(p)))
        except Exception:
            pass
    return s


def _settings_path():
    return os.path.join(_HERE, "sd_study_settings.json")


# ---------------------------------------------------------------- utilities
def _log(msg):
    try:
        ExtAPI.Log.WriteMessage("[SingularityDetector] " + str(msg))    # noqa: F821
    except Exception:
        print("[SingularityDetector] " + str(msg))


def _msgbox(text, title="Singularity Detector"):
    try:
        import clr
        clr.AddReference("Ans.UI.Toolkit")
        from Ansys.UI.Toolkit import MessageBox
        MessageBox.Show(str(text), str(title))
    except Exception:
        _log(title + ": " + text)


def _current_size_m(model):
    """The model's global element size in metres, or a bbox-based default."""
    try:
        import units
        q = model.Mesh.ElementSize
        val = float(q.Value)
        if val > 0:
            u = str(q.Unit)
            return float(units.ConvertUnit(val, u, "m", "Length"))
    except Exception:
        pass
    # program-controlled -> estimate from the geometry bounding box
    try:
        geo = ExtAPI.DataModel.GeoData                                  # noqa: F821
        xs = []
        for asm in geo.Assemblies:
            for part in asm.Parts:
                for body in part.Bodies:
                    for f in body.Faces:
                        c = f.Centroid
                        xs.append(c)
        if xs:
            import math
            mn = [min(p[k] for p in xs) for k in range(3)]
            mx = [max(p[k] for p in xs) for k in range(3)]
            diag = math.sqrt(sum((mx[k] - mn[k]) ** 2 for k in range(3)))
            # centroids are in the active unit (mm) -> to metres, ~/20 of the diagonal
            return (diag / 20.0) / 1000.0
    except Exception:
        pass
    return 0.005


def _plan_sizes(model):
    s = _settings()
    h0 = _current_size_m(model)
    r = float(s.get("ratio", 0.75))
    n = int(s.get("refinements", 3))
    sizes = [h0]
    for _ in range(n):
        h0 = h0 * r
        sizes.append(h0)
    return sizes


def _run_external(module, run_dir, timeout_s=1800):
    py = _venv_python()
    if not py:
        return (127, "venv python not found -- set sd_config.json venv_python / SD_VENV_PYTHON")
    cmd = [py, "-u", "-m", "devtools." + module, run_dir]
    _log("run: " + " ".join(cmd))
    try:
        p = subprocess.Popen(cmd, cwd=_repo(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        out, _e = p.communicate()
        try:
            out = out.decode("utf-8", "replace")
        except Exception:
            out = str(out)
        return (p.returncode, out)
    except Exception:
        import traceback
        return (1, traceback.format_exc())


# ------------------------------------------------------------------ callbacks
def on_init(context):
    _bind_engine()
    _log("loaded (context=%s)  venv=%s" % (context, _venv_python()))


def add_contours(analysis):
    add_singularity_contours(analysis)


def show_settings(analysis):
    s = _settings()
    _msgbox(
        "Study settings (edit %s):\n\n"
        "  refinements          : %s   (extra mesh levels)\n"
        "  ratio                : %s   (h_{i+1} = ratio * h_i, 0.50-0.90)\n"
        "  confidence_threshold : %s   (flag nodes at/above this)\n\n"
        "The study uses the model's EXISTING loads and supports."
        % (_settings_path(), s["refinements"], s["ratio"], s["confidence_threshold"]))


def run_singularity_study(analysis):
    """The main button.  Every step delegates to a tested function."""
    _import_check = _venv_python()
    if not _import_check:
        _msgbox("Cannot find the analysis venv Python.\nSet 'venv_python' in\n%s"
                % os.path.join(_HERE, "sd_config.json"))
        return

    _bind_engine()
    model = ExtAPI.DataModel.Project.Model                              # noqa: F821
    sizes = _plan_sizes(model)
    run_dir = os.path.join(_repo(), "artifacts",
                           time.strftime("%Y-%m-%d_%H%M%S") + "_actstudy")
    _log("study dir: %s   sizes(m): %s" % (run_dir, ["%.4g" % x for x in sizes]))

    # 1) in-session mesh/solve loop (Mechanical-side, IronPython)
    import act_study

    def _progress(frac, msg):
        _log("[%3d%%] %s" % (int(100 * frac), msg))

    summ = act_study.run(analysis, sizes, run_dir, progress=_progress)
    if summ.get("status") != "PASS":
        _msgbox("Study did not complete: %s\nSee %s" % (summ.get("status"), run_dir))
        return

    # 2) external numpy/DPF analysis
    for mod in ("analyze_study", "build_contours", "convergence_charts"):
        rc, out = _run_external(mod, run_dir)
        _log("%s rc=%s\n%s" % (mod, rc, out[-1500:]))
        if rc != 0 and mod != "convergence_charts":
            _msgbox("%s failed (rc=%s). See the log." % (mod, rc))
            return

    # 3) surface the results in the tree
    csv_path = os.path.join(run_dir, "contour_fields.csv")
    if os.path.isfile(csv_path):
        try:
            dst = os.path.join(analysis.WorkingDir, "contour_fields.csv")
            import shutil
            shutil.copy2(csv_path, dst)
        except Exception:
            pass
        visualization.CONTOUR_CSV_PATH = csv_path
        visualization._CACHE.clear()
    add_singularity_contours(analysis)
    try:
        for r in analysis.Solution.Children:
            try:
                r.Activate()
            except Exception:
                pass
    except Exception:
        pass

    # 4) headline
    head = {}
    ap = os.path.join(run_dir, "singularity_analysis.json")
    if os.path.isfile(ap):
        try:
            head = json.load(open(ap)).get("headline", {})
        except Exception:
            head = {}
    _msgbox(
        "Singularity study complete.\n\n"
        "  classification : %s\n"
        "  confidence     : %s / 100  [%s]\n"
        "  lambda         : %s\n\n"
        "Results added: Raw Stress, Singularity Confidence [%%], "
        "Singularity-Filtered Stress.\nCharts + JSON in:\n%s"
        % (head.get("classification"), head.get("singularity_confidence"),
           head.get("category"), head.get("divergence_exponent"), run_dir))
