# -*- coding: utf-8 -*-
# ==========================================================================
# main.py -- SingularityDetector ACT extension script (IronPython 2.7).
#
# The ribbon.  Buttons, each a one-liner into a tested function (spec S21):
#
#   Run Singularity Study   -> run_singularity_study(analysis)
#       in-session mesh-refinement study (extension/act_study.py, on the user's
#       existing analysis) -> shell out to the repo .venv for the numpy/DPF
#       analysis -> add + evaluate the three result contours -> summary.
#       The model is LEFT at the finest mesh (spec S7), solved.
#   Restore Original Mesh   -> restore_original_mesh(analysis)
#   Study Settings          -> show_settings(analysis)   (the "Singularity Study"
#                              tree object; created with defaults if absent)
#   Add Contours            -> add_contours(analysis)
#
# The extension is IronPython 2.7 with NO numpy; the heavy analysis lives in the
# repo .venv and is invoked as a subprocess.  Deploy config (venv python, repo
# path) is in sd_config.json next to this file (extension_deployer writes it).
# ==========================================================================

import os
import sys
import json
import time
import traceback
import subprocess

# ACT execs this script; __file__ may not be defined depending on the loader.
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _HERE = None
if not _HERE or not os.path.isfile(os.path.join(_HERE, "visualization.py")):
    for _c in list(sys.path) + [os.getcwd()]:
        if _c and os.path.isfile(os.path.join(_c, "visualization.py")):
            _HERE = _c
            break
if not _HERE:
    _HERE = os.getcwd()
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from visualization import (evaluate_raw_stress,                         # noqa: F401
                           evaluate_singularity_confidence,             # noqa: F401
                           evaluate_singularity_filtered_stress,        # noqa: F401
                           add_singularity_contours,
                           remove_singularity_contours)
import visualization
import mech_env

SETUP_OBJ_NAME = "Singularity Study"
DEFAULT_SETTINGS = {"refinements": 3, "ratio": 0.75, "confidence_threshold": 70.0,
                    "result_quantity": "Equivalent (von Mises)"}


# --------------------------------------------------------------- engine binding
def _bind_engine():
    """ACT injects ExtAPI/Model/Quantity/... into THIS script's namespace only;
    hand them to the imported Mechanical-side modules via mech_env."""
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
    return m.get("ExtAPI")


def _E():
    return _bind_engine() or mech_env.G.ExtAPI


# ------------------------------------------------------------------- config
def _config():
    for p in (os.path.join(_HERE, "sd_config.json"), os.environ.get("SD_CONFIG", "")):
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


# ------------------------------------------------------------------- utilities
def _log(msg):
    try:
        _E().Log.WriteMessage("[SingularityDetector] " + str(msg))
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


def _guard(fn):
    """Wrap a ribbon callback: bind the engine, and turn any exception into a
    readable message box instead of a silent failure / buried log line."""
    def _wrapped(analysis):
        try:
            _bind_engine()
            return fn(analysis)
        except Exception:
            tb = traceback.format_exc()
            _log("ERROR in %s:\n%s" % (fn.__name__, tb))
            _msgbox("'%s' failed:\n\n%s" % (fn.__name__, tb[-1400:]))
    _wrapped.__name__ = fn.__name__
    return _wrapped


# --------------------------------------------------------- status window (Forms)
class _Status(object):
    def __init__(self, title="Singularity Study"):
        self.form = None
        self.label = None
        try:
            import clr
            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
            from System.Windows.Forms import Form, Label, FormBorderStyle
            from System.Drawing import Size, Point
            f = Form()
            f.Text = title
            f.Size = Size(500, 140)
            f.FormBorderStyle = FormBorderStyle.FixedToolWindow
            f.TopMost = True
            f.ShowInTaskbar = False
            lbl = Label()
            lbl.AutoSize = False
            lbl.Size = Size(468, 84)
            lbl.Location = Point(14, 14)
            f.Controls.Add(lbl)
            f.Show()
            self.form, self.label = f, lbl
            self.update(0.0, "starting...")
        except Exception:
            pass

    def update(self, frac, msg):
        try:
            if self.label is not None:
                self.label.Text = "[%3d%%]  %s" % (int(100 * (frac or 0.0)), msg)
                from System.Windows.Forms import Application
                Application.DoEvents()
        except Exception:
            pass
        _log("[%3d%%] %s" % (int(100 * (frac or 0.0)), msg))

    def close(self):
        try:
            if self.form is not None:
                self.form.Close()
        except Exception:
            pass


# ------------------------------------------------------------ settings tree object
def _find_setup(analysis):
    e = None
    try:
        e = _E()
    except Exception:
        e = None
    containers = [analysis]
    if e is not None:
        containers.append(getattr(getattr(e, "DataModel", None), "Project", None))
    for container in containers:
        if container is None:
            continue
        try:
            kids = container.Children
        except Exception:
            kids = []
        for k in kids:
            try:
                if str(k.Name) == SETUP_OBJ_NAME:
                    return k
            except Exception:
                pass
    return None


def _create_setup(analysis):
    ext = None
    try:
        ext = _E().ExtensionManager.CurrentExtension
    except Exception:
        ext = None
    for maker in (lambda: analysis.CreateObject(SETUP_OBJ_NAME, ext),
                  lambda: analysis.CreateObject(SETUP_OBJ_NAME),
                  lambda: _E().DataModel.CreateObject(SETUP_OBJ_NAME, ext),
                  lambda: _E().DataModel.CreateObject(SETUP_OBJ_NAME)):
        try:
            obj = maker()
            if obj is not None:
                return obj
        except Exception:
            pass
    return None


def _get_or_create_setup(analysis):
    return _find_setup(analysis) or _create_setup(analysis)


def _prop(obj, name, default):
    try:
        return obj.Properties[name].Value
    except Exception:
        pass
    try:
        return obj.GetCustomPropertyByPath(name).Value
    except Exception:
        pass
    return default


def _read_settings(analysis):
    s = dict(DEFAULT_SETTINGS)
    obj = _find_setup(analysis)
    if obj is not None:
        s["refinements"] = int(_prop(obj, "Refinements", s["refinements"]))
        s["ratio"] = float(_prop(obj, "Ratio", s["ratio"]))
        s["confidence_threshold"] = float(_prop(obj, "ConfidenceThreshold",
                                                s["confidence_threshold"]))
        s["result_quantity"] = str(_prop(obj, "ResultQuantity", s["result_quantity"]))
    else:
        p = os.path.join(_HERE, "sd_study_settings.json")     # fallback
        if os.path.isfile(p):
            try:
                s.update(json.load(open(p)))
            except Exception:
                pass
    # clamp ratio to the allowed band (spec S7)
    s["ratio"] = max(0.50, min(0.90, s["ratio"]))
    s["refinements"] = max(1, min(8, int(s["refinements"])))
    return s


# ---------------------------------------------------------------- study planning
def _current_size_m(model):
    try:
        import units
        q = model.Mesh.ElementSize
        val = float(q.Value)
        if val > 0:
            return float(units.ConvertUnit(val, str(q.Unit), "m", "Length"))
    except Exception:
        pass
    try:
        geo = _E().DataModel.GeoData
        pts = []
        for asm in geo.Assemblies:
            for part in asm.Parts:
                for body in part.Bodies:
                    for f in body.Faces:
                        pts.append(f.Centroid)
        if pts:
            import math
            mn = [min(p[k] for p in pts) for k in range(3)]
            mx = [max(p[k] for p in pts) for k in range(3)]
            diag = math.sqrt(sum((mx[k] - mn[k]) ** 2 for k in range(3)))
            return (diag / 20.0) / 1000.0        # centroids in mm -> m
    except Exception:
        pass
    return 0.005


def _plan_sizes(model, settings):
    h0 = _current_size_m(model)
    r = float(settings["ratio"])
    n = int(settings["refinements"])
    sizes = [h0]
    for _ in range(n):
        h0 = h0 * r
        sizes.append(h0)
    return sizes


def _run_external(module, run_dir, status=None, frac=0.9):
    """Run `python -m devtools.<module> <run_dir>` in the repo venv.

    Ansys' bundled IronPython subprocess cannot redirect stderr->stdout, so
    stdout/stderr go to real files in run_dir and are read back."""
    py = _venv_python()
    if not py:
        return (127, "venv python not found -- set sd_config.json venv_python")
    cmd = [py, "-u", "-m", "devtools." + module, run_dir]
    _log("run: " + " ".join(cmd))
    if status is not None:
        status.update(frac, "analysing: %s ..." % module)
    out_path = os.path.join(run_dir, module + ".out")
    err_path = os.path.join(run_dir, module + ".err")
    try:
        fo = open(out_path, "wb")
        fe = open(err_path, "wb")
        try:
            p = subprocess.Popen(cmd, cwd=_repo(), stdout=fo, stderr=fe)
            rc = p.wait()
        finally:
            fo.close()
            fe.close()
        txt = ""
        for pth in (out_path, err_path):
            try:
                txt += open(pth, "rb").read().decode("utf-8", "replace")
            except Exception:
                pass
        return (rc, txt)
    except Exception:
        return (1, traceback.format_exc())


# ------------------------------------------------------------------ callbacks
def on_init(context):
    _bind_engine()
    _log("loaded (context=%s)  venv=%s" % (context, _venv_python()))


@_guard
def add_contours(analysis):
    ext = None
    try:
        ext = _E().ExtensionManager.CurrentExtension
    except Exception:
        ext = None
    made = add_singularity_contours(analysis, ext=ext)
    _msgbox("Added: " + (", ".join(made) if made else "(nothing)") +
            "\n\nRun a Singularity Study first if the values show as blank.")


@_guard
def show_settings(analysis):
    obj = _get_or_create_setup(analysis)
    if obj is not None:
        try:
            _E().DataModel.Tree.Activate([obj])
        except Exception:
            try:
                obj.Activate()
            except Exception:
                pass
        s = _read_settings(analysis)
        _msgbox("Edit the '%s' object in the tree (Details view):\n\n"
                "  Refinements          : %s\n"
                "  Ratio (h[i+1]/h[i])  : %s\n"
                "  Confidence threshold : %s %%\n"
                "  Result quantity      : %s\n\n"
                "The study uses the model's EXISTING loads and supports."
                % (SETUP_OBJ_NAME, s["refinements"], s["ratio"],
                   s["confidence_threshold"], s["result_quantity"]))
    else:
        p = os.path.join(_HERE, "sd_study_settings.json")
        _msgbox("Could not create the settings object; edit this file instead:\n%s" % p)


@_guard
def restore_original_mesh(analysis):
    import mesh_manager
    model = _E().DataModel.Project.Model
    mm = mesh_manager.MeshManager(model)
    snap = None
    for base in (analysis.WorkingDir, _latest_actstudy_dir()):
        if not base:
            continue
        f = os.path.join(base, "sd_original_mesh.json")
        if os.path.isfile(f):
            try:
                snap = json.load(open(f))
                break
            except Exception:
                pass
    if snap is None:
        # nothing recorded -> just go back to program-controlled sizing
        snap = {"ElementSize": "0 [m]"}
    ok = mm.restore_from_snapshot(snap)
    _msgbox("Original mesh settings restored%s.\nRe-generate the mesh and solve "
            "to update your results." % ("" if ok else " (partially)"))


def _latest_actstudy_dir():
    try:
        base = os.path.join(_repo(), "artifacts")
        cand = [os.path.join(base, d) for d in os.listdir(base) if d.endswith("_actstudy")]
        cand = [d for d in cand if os.path.isdir(d)]
        return max(cand, key=os.path.getmtime) if cand else None
    except Exception:
        return None


@_guard
def run_singularity_study(analysis):
    if not _venv_python():
        _msgbox("Cannot find the analysis venv Python.\nSet 'venv_python' in\n%s"
                % os.path.join(_HERE, "sd_config.json"))
        return

    model = _E().DataModel.Project.Model
    settings = _read_settings(analysis)
    sizes = _plan_sizes(model, settings)
    run_dir = os.path.join(_repo(), "artifacts",
                           time.strftime("%Y-%m-%d_%H%M%S") + "_actstudy")
    _log("study dir: %s   settings: %s   sizes(m): %s"
         % (run_dir, settings, ["%.4g" % x for x in sizes]))

    status = _Status()
    try:
        # remove any stale SD result objects so Mechanical does not try to
        # evaluate them on every level's solve (before contour_fields.csv exists)
        try:
            remove_singularity_contours(analysis)
        except Exception:
            pass

        import act_study
        summ = act_study.run(analysis, sizes, run_dir,
                             progress=lambda f, m: status.update(0.05 + 0.75 * f, m),
                             restore=False)
        if summ.get("status") != "PASS":
            _msgbox("Study did not complete: %s\nSee %s"
                    % (summ.get("status"), run_dir))
            return

        for mod, fr in (("analyze_study", 0.82), ("build_contours", 0.90),
                        ("convergence_charts", 0.95)):
            rc, out = _run_external(mod, run_dir, status=status, frac=fr)
            _log("%s rc=%s\n%s" % (mod, rc, out[-1800:]))
            if rc != 0 and mod != "convergence_charts":
                _msgbox("%s failed (rc=%s).\n\n%s" % (mod, rc, out[-1200:]))
                return

        status.update(0.96, "adding result contours...")
        csv_path = os.path.join(run_dir, "contour_fields.csv")
        if os.path.isfile(csv_path):
            try:
                import shutil
                shutil.copy2(csv_path, os.path.join(analysis.WorkingDir,
                                                    "contour_fields.csv"))
            except Exception:
                pass
            visualization.CONTOUR_CSV_PATH = csv_path
            visualization._CACHE.clear()

        ext = None
        try:
            ext = _E().ExtensionManager.CurrentExtension
        except Exception:
            ext = None
        add_singularity_contours(analysis, ext=ext)
        try:
            analysis.Solution.EvaluateAllResults()
        except Exception:
            pass

        head = {}
        ap = os.path.join(run_dir, "singularity_analysis.json")
        if os.path.isfile(ap):
            try:
                head = json.load(open(ap)).get("headline", {})
            except Exception:
                head = {}
        status.update(1.0, "done")
        _msgbox(
            "Singularity study complete.  Model left at the finest mesh.\n\n"
            "  classification : %s\n"
            "  confidence     : %s / 100   [%s]\n"
            "  divergence exp : %s\n\n"
            "Results: Raw Stress, Singularity Confidence [%%], "
            "Singularity-Filtered Stress.\nCharts + JSON:\n%s"
            % (head.get("classification"), head.get("singularity_confidence"),
               head.get("category"), head.get("divergence_exponent"), run_dir))
    finally:
        status.close()
