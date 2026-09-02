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


def _all_faces(model):
    geo = G.require("ExtAPI").DataModel.GeoData
    out = []
    for asm in geo.Assemblies:
        for part in asm.Parts:
            for body in part.Bodies:
                for face in body.Faces:
                    out.append(face)
    return out


def faces_sorted_by_x(model):
    """(min_x_face_id, max_x_face_id) GeoData ids, or (None, None). Raises nothing."""
    faces = [(f.Centroid[0], f.Id) for f in _all_faces(model)]
    if len(faces) < 2:
        return (None, None)
    faces.sort()
    return (faces[0][1], faces[-1][1])


def extremal_face_by_axis(model, axis, which):
    """GeoData face id whose centroid is the min/max along axis (0=x,1=y,2=z).
    'which' in ('min','max'). Picks the face whose centroid is closest to the
    body's extreme AND whose area is largest among near-ties."""
    faces = _all_faces(model)
    if not faces:
        return None
    vals = [f.Centroid[axis] for f in faces]
    target = min(vals) if which == "min" else max(vals)
    span = (max(vals) - min(vals)) or 1.0
    best, best_key = None, None
    for f in faces:
        near = abs(f.Centroid[axis] - target) <= 0.02 * span
        if not near:
            continue
        try:
            area = float(f.Area)
        except Exception:
            area = 0.0
        key = area
        if best is None or key > best_key:
            best, best_key = f, key
    return None if best is None else best.Id


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


def build_clean_tension_analysis(model, geo_path, force_newtons=1000.0):
    """Import solid, add Static Structural, apply a *clean* uniaxial tension:
    frictionless supports on the min-X / min-Y / min-Z faces (block translation +
    rigid-body modes without over-constraining), force on the max-X face.

    Avoids the fixed-face / free-edge restraint singularity that
    build_axial_bar_analysis introduces, so the FEATURE under study (hole, corner,
    fillet, notch) is the only stress raiser. Returns
    (analysis, support_for_reaction, faces_dict).
    """
    if not geo_path or not os.path.isfile(geo_path):
        raise RuntimeError("geometry file not found: %r" % geo_path)
    if not import_geometry(model, geo_path):
        raise RuntimeError("geometry import produced no bodies")

    analysis = model.AddStaticStructuralAnalysis()
    fx = {ax: extremal_face_by_axis(model, i, "min") for i, ax in enumerate("xyz")}
    max_x = extremal_face_by_axis(model, 0, "max")
    if None in fx.values() or max_x is None:
        raise RuntimeError("could not identify all bounding faces for clean tension")

    sx = analysis.AddFrictionlessSupport(); sx.Location = selection(fx["x"])
    sy = analysis.AddFrictionlessSupport(); sy.Location = selection(fx["y"])
    sz = analysis.AddFrictionlessSupport(); sz.Location = selection(fx["z"])

    force = analysis.AddForce()
    force.Location = selection(max_x)
    val = _qty(repr(float(force_newtons)) + " [N]")
    try:
        force.DefineBy = G.require("LoadDefineBy").Components
        force.XComponent.Output.DiscreteValues = [val]
    except Exception:
        force.Magnitude.Output.DiscreteValues = [val]

    return analysis, sx, {"min_x": fx["x"], "min_y": fx["y"], "min_z": fx["z"], "max_x": max_x}
