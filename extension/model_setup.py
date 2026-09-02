# -*- coding: utf-8 -*-
# ==========================================================================
# model_setup -- Mechanical runtime layer (IronPython 2.7).
#
# Shared "build a simple Static Structural analysis around an imported solid"
# used by the milestone drivers.  NOT the final architecture -- the real
# extension attaches to the user's existing analysis, it does not create one.
#
# Engine-injected names come via mech_env.G (see mech_env.py).
# NO f-strings / dataclasses / type hints.
# ==========================================================================

import os

from mech_env import G


def import_geometry(model, geo_path):
    gi = model.GeometryImportGroup.AddGeometryImport()
    try:
        pref = G.require("Ansys").Mechanical.DataModel.Enums.GeometryImportPreference
        settings = pref()
        settings.ProcessNamedSelections = True
        gi.Import(geo_path, True, settings)
    except Exception:
        gi.Import(geo_path)
    return len(model.Geometry.Children) > 0


def faces_sorted_by_x(model):
    """(min_x_face_id, max_x_face_id) GeoData ids, or (None, None). Raises nothing."""
    geo = G.require("ExtAPI").DataModel.GeoData
    faces = []
    for asm in geo.Assemblies:
        for part in asm.Parts:
            for body in part.Bodies:
                for face in body.Faces:
                    c = face.Centroid
                    faces.append((c[0], face.Id))
    if len(faces) < 2:
        return (None, None)
    faces.sort()
    return (faces[0][1], faces[-1][1])


def selection(face_id):
    ExtAPI = G.require("ExtAPI")
    Ansys = G.require("Ansys")
    sm = ExtAPI.SelectionManager
    info = sm.CreateSelectionInfo(
        Ansys.ACT.Interfaces.Common.SelectionTypeEnum.GeometryEntities
    )
    info.Ids = [face_id]
    return info


def _qty(text):
    return G.require("Quantity")(text)


def build_axial_bar_analysis(model, geo_path, force_newtons=1000.0):
    """Import solid, add Static Structural, fix min-X face, axial force on max-X.

    Returns (analysis, fixed_support, {"min_face":id, "max_face":id}).
    Raises RuntimeError on a setup problem.
    """
    if not geo_path or not os.path.isfile(geo_path):
        raise RuntimeError("geometry file not found: %r" % geo_path)
    if not import_geometry(model, geo_path):
        raise RuntimeError("geometry import produced no bodies")

    analysis = model.AddStaticStructuralAnalysis()
    min_face, max_face = faces_sorted_by_x(model)
    if min_face is None:
        raise RuntimeError("could not find >= 2 faces to scope BCs")

    fixed = analysis.AddFixedSupport()
    fixed.Location = selection(min_face)

    force = analysis.AddForce()
    force.Location = selection(max_face)
    val = _qty(repr(float(force_newtons)) + " [N]")
    try:
        force.DefineBy = G.require("LoadDefineBy").Components
        force.XComponent.Output.DiscreteValues = [val]
    except Exception:
        force.Magnitude.Output.DiscreteValues = [val]

    return analysis, fixed, {"min_face": min_face, "max_face": max_face}
