# -*- coding: utf-8 -*-
# ==========================================================================
# Milestone 0 -- Autonomous Ansys Runner proof.
#
# RUNTIME: Mechanical scripting engine == IronPython 2.7.  NO f-strings,
#          NO CPython-3 stdlib, NO numpy.  Keep it conservative.
#          See the `act-builder` skill -> knowledge/compatibility/.
#
# WHAT IT DOES
#   terminal -> AnsysWBU.exe batch -> [this script] ->
#       (optional) import a geometry fixture -> Static Structural ->
#       fixed support + load -> mesh -> solve -> read peak stress ->
#       write <SD_RUN_DIR>/<SD_SENTINEL> as JSON -> exit
#
#   If no geometry fixture is supplied (env SD_GEOMETRY unset / missing),
#   the script STILL writes a machine-readable JSON sentinel with
#   status "NO_GEOMETRY" so the launch->script->json->exit plumbing is
#   proven independently of having a model.
#
# ENV IN
#   SD_RUN_DIR   directory to write the result into      (set by the launcher)
#   SD_SENTINEL  result file name (default milestone_result.json)
#   SD_GEOMETRY  optional path to .step/.stp/.igs/.x_t/.scdoc/.agdb fixture
#   SD_ELEM_SIZE optional global element size, in metres (default 0.01)
# ==========================================================================

import os
import sys
import json
import time
import traceback


def _run_dir():
    d = os.environ.get("SD_RUN_DIR") or os.getcwd()
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
    except Exception:
        d = os.getcwd()
    return d


def _sentinel_path():
    return os.path.join(_run_dir(), os.environ.get("SD_SENTINEL", "milestone_result.json"))


def _write(payload):
    payload.setdefault("milestone", 0)
    payload.setdefault("script", "milestone0_hello.py")
    payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = _sentinel_path()
    f = open(path, "w")
    try:
        f.write(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        f.close()
    try:
        ExtAPI.Log.WriteMessage("[milestone0] wrote " + path)   # noqa: F821
    except Exception:
        print("[milestone0] wrote " + path)


def _mech_version():
    try:
        return str(ExtAPI.Application.VersionInfo)               # noqa: F821
    except Exception:
        pass
    try:
        return str(ExtAPI.DataModel.Project.ProductVersion)     # noqa: F821
    except Exception:
        return "unknown"


def _import_geometry(model, geo_path):
    """Import a geometry fixture into the model. Returns True on success."""
    geo_import = model.GeometryImportGroup.AddGeometryImport()
    try:
        pref = Ansys.Mechanical.DataModel.Enums.GeometryImportPreference        # noqa: F821
        settings = pref()
        settings.ProcessNamedSelections = True
        geo_import.Import(geo_path, True, settings)
    except Exception:
        # Older/newer signature -- fall back to the 1-arg form.
        geo_import.Import(geo_path)
    return len(model.Geometry.Children) > 0


def _first_two_faces_by_x(model):
    """Return (min_x_face_id, max_x_face_id) as GeoData face ids, or (None, None)."""
    try:
        geo = ExtAPI.DataModel.GeoData                                          # noqa: F821
    except Exception:
        return (None, None)
    faces = []
    for asm in geo.Assemblies:
        for part in asm.Parts:
            for body in part.Bodies:
                for face in body.Faces:
                    c = face.Centroid            # [x, y, z]
                    faces.append((c[0], face.Id))
    if len(faces) < 2:
        return (None, None)
    faces.sort()
    return (faces[0][1], faces[-1][1])


def _selection(face_id):
    sm = ExtAPI.SelectionManager                                               # noqa: F821
    info = sm.CreateSelectionInfo(
        Ansys.ACT.Interfaces.Common.SelectionTypeEnum.GeometryEntities         # noqa: F821
    )
    info.Ids = [face_id]
    return info


def main():
    result = {"ansys_version_reported": None, "status": "ERROR", "steps": []}
    step = result["steps"].append
    try:
        result["ansys_version_reported"] = _mech_version()
        step("engine-up")

        geo_path = os.environ.get("SD_GEOMETRY", "").strip()
        if not geo_path or not os.path.isfile(geo_path):
            result["status"] = "NO_GEOMETRY"
            result["message"] = (
                "Launch/script/JSON/exit plumbing OK. No geometry fixture at "
                "SD_GEOMETRY=%r -- supply one to exercise mesh+solve." % geo_path
            )
            _write(result)
            return 0

        result["geometry"] = geo_path
        model = ExtAPI.DataModel.Project.Model                                  # noqa: F821
        if not _import_geometry(model, geo_path):
            result["status"] = "GEOMETRY_IMPORT_FAILED"
            _write(result)
            return 1
        step("geometry-imported")

        analysis = model.AddStaticStructuralAnalysis()
        step("analysis-added")

        min_face, max_face = _first_two_faces_by_x(model)
        if min_face is None:
            result["status"] = "NO_FACES_FOUND"
            _write(result)
            return 1

        fixed = analysis.AddFixedSupport()
        fixed.Location = _selection(min_face)

        force = analysis.AddForce()
        force.Location = _selection(max_face)
        try:
            force.DefineBy = LoadDefineBy.Components                            # noqa: F821
            force.XComponent.Output.DiscreteValues = [Quantity("1000 [N]")]    # noqa: F821
        except Exception:
            force.Magnitude.Output.DiscreteValues = [Quantity("1000 [N]")]     # noqa: F821
        step("bcs-applied")

        mesh = model.Mesh
        try:
            size_m = float(os.environ.get("SD_ELEM_SIZE", "0.01"))
        except ValueError:
            size_m = 0.01
        mesh.ElementSize = Quantity(str(size_m) + " [m]")                       # noqa: F821
        mesh.GenerateMesh()
        result["mesh"] = {
            "requested_element_size_m": size_m,
            "nodes": int(mesh.Nodes),
            "elements": int(mesh.Elements),
        }
        if int(mesh.Nodes) <= 0:
            result["status"] = "MESH_FAILED"
            _write(result)
            return 1
        step("meshed")

        sol = analysis.Solution
        eqv = sol.AddEquivalentStress()
        tot = sol.AddTotalDeformation()

        t0 = time.time()
        sol.Solve(True)
        result["solve_seconds"] = round(time.time() - t0, 2)
        status = str(sol.Status)
        result["solve_status"] = status
        if status != "Done":
            result["status"] = "SOLVE_FAILED"
            _write(result)
            return 1
        step("solved")

        eqv.Activate()
        tot.Activate()
        result["peak_equivalent_stress"] = {
            "max": float(eqv.Maximum.Value),
            "unit": str(eqv.Maximum.Unit),
        }
        result["max_total_deformation"] = {
            "max": float(tot.Maximum.Value),
            "unit": str(tot.Maximum.Unit),
        }
        step("results-read")

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


# Run on import/exec: the embedded-App and `-script` entry points do not set
# __name__ == "__main__".  Safe to re-run (each call rebuilds the model and
# rewrites the sentinel); the runner resets the project with App.new() between
# calls.
_SD_RC = main()
try:
    ExtAPI.Log.WriteMessage("[milestone0] main() returned rc=%d" % _SD_RC)  # noqa: F821
except Exception:
    print("[milestone0] main() returned rc=%d" % _SD_RC)

if __name__ == "__main__":
    try:
        sys.exit(_SD_RC)
    except SystemExit:
        raise
    except Exception:
        pass
