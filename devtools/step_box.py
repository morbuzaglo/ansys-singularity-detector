"""Emit a valid ISO-10303-21 (STEP AP214) B-rep for an axis-aligned box.

External automation layer -> CPython 3. No CAD kernel, no Ansys -- deterministic,
instant, version-independent. Good enough for the 'bar' benchmark and any other
rectangular-prism fixture. For holes / notches / fillets use a real kernel
(cadquery/build123d) or SpaceClaim later.

    python devtools/step_box.py --out test_models/bar.stp --lx 0.100 --ly 0.010 --lz 0.010

Verify by importing the result in Mechanical (tests/mechanical/test_milestone0.py).
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path


def _step_box(lx: float, ly: float, lz: float, name: str = "box") -> str:
    # 8 corner vertices of the box [0,lx] x [0,ly] x [0,lz]
    P = {
        0: (0.0, 0.0, 0.0), 1: (lx, 0.0, 0.0), 2: (lx, ly, 0.0), 3: (0.0, ly, 0.0),
        4: (0.0, 0.0, lz), 5: (lx, 0.0, lz), 6: (lx, ly, lz), 7: (0.0, ly, lz),
    }
    # 12 edges as ordered vertex pairs
    E = {
        0: (0, 1), 1: (1, 2), 2: (2, 3), 3: (3, 0),      # bottom z=0
        4: (4, 5), 5: (5, 6), 6: (6, 7), 7: (7, 4),      # top z=lz
        8: (0, 4), 9: (1, 5), 10: (2, 6), 11: (3, 7),    # verticals
    }
    # 6 faces: (list of (edge_index, forward?), plane origin, plane normal, plane ref-dir)
    F = [
        # bottom z=0, normal -Z
        ([(0, False), (3, False), (2, False), (1, False)], P[0], (0, 0, -1), (1, 0, 0)),
        # top z=lz, normal +Z
        ([(4, True), (5, True), (6, True), (7, True)], P[4], (0, 0, 1), (1, 0, 0)),
        # front y=0, normal -Y
        ([(0, True), (9, True), (4, False), (8, False)], P[0], (0, -1, 0), (1, 0, 0)),
        # back y=ly, normal +Y
        ([(2, True), (11, True), (6, False), (10, False)], P[2], (0, 1, 0), (-1, 0, 0)),
        # left x=0, normal -X
        ([(3, True), (8, True), (7, False), (11, False)], P[3], (-1, 0, 0), (0, 1, 0)),
        # right x=lx, normal +X
        ([(1, True), (10, True), (5, False), (9, False)], P[1], (1, 0, 0), (0, 1, 0)),
    ]

    lines: list[str] = []
    _id = [0]

    def nid() -> int:
        _id[0] += 1
        return _id[0]

    def add(s: str) -> int:
        i = nid()
        lines.append("#{0}={1};".format(i, s))
        return i

    def pt(xyz) -> int:
        return add("CARTESIAN_POINT('',({0:.10g},{1:.10g},{2:.10g}))".format(*xyz))

    def direction(xyz) -> int:
        return add("DIRECTION('',({0:.10g},{1:.10g},{2:.10g}))".format(*xyz))

    # --- units + geometric representation context (classic combined entity) ---
    len_unit = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.))")
    ang_unit = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    sa_unit = add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{0},"
              "'distance_accuracy_value','confusion accuracy')".format(len_unit))
    geo_ctx = add(
        "(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
        "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{0}))"
        "GLOBAL_UNIT_ASSIGNED_CONTEXT((#{1},#{2},#{3}))"
        "REPRESENTATION_CONTEXT('',''))".format(unc, len_unit, ang_unit, sa_unit)
    )

    # --- vertices ---
    vpt = {k: add("VERTEX_POINT('',#{0})".format(pt(v))) for k, v in P.items()}

    # --- edge curves (one LINE per edge, shared by the two adjacent faces) ---
    edge_curve = {}
    for k, (a, b) in E.items():
        pa, pb = P[a], P[b]
        d = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        mag = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
        u = (d[0] / mag, d[1] / mag, d[2] / mag)
        line = add("LINE('',#{0},#{1})".format(
            pt(pa), add("VECTOR('',#{0},{1:.10g})".format(direction(u), mag))))
        edge_curve[k] = add("EDGE_CURVE('',#{0},#{1},#{2},.T.)".format(vpt[a], vpt[b], line))

    # --- faces ---
    face_ids = []
    for loop, origin, normal, refdir in F:
        oriented = []
        for eidx, fwd in loop:
            oriented.append(add("ORIENTED_EDGE('',*,*,#{0},.{1}.)".format(
                edge_curve[eidx], "T" if fwd else "F")))
        edge_loop = add("EDGE_LOOP('',({0}))".format(",".join("#{0}".format(x) for x in oriented)))
        bound = add("FACE_OUTER_BOUND('',#{0},.T.)".format(edge_loop))
        a2p = add("AXIS2_PLACEMENT_3D('',#{0},#{1},#{2})".format(
            pt(origin), direction(normal), direction(refdir)))
        plane = add("PLANE('',#{0})".format(a2p))
        face_ids.append(add("ADVANCED_FACE('',(#{0}),#{1},.T.)".format(bound, plane)))

    shell = add("CLOSED_SHELL('',({0}))".format(",".join("#{0}".format(x) for x in face_ids)))
    brep = add("MANIFOLD_SOLID_BREP('{0}',#{1})".format(name, shell))

    # --- product / shape wiring ---
    app_ctx = add("APPLICATION_CONTEXT("
                  "'core data for automotive mechanical design processes')")
    add("APPLICATION_PROTOCOL_DEFINITION('international standard',"
        "'automotive_design',2010,#{0})".format(app_ctx))
    product = add("PRODUCT('{0}','{0}','',(#{1}))".format(
        name, add("PRODUCT_CONTEXT('',#{0},'mechanical')".format(app_ctx))))
    pdf = add("PRODUCT_DEFINITION_FORMATION('','',#{0})".format(product))
    pd_ctx = add("PRODUCT_DEFINITION_CONTEXT('part definition',#{0},'design')".format(app_ctx))
    pd = add("PRODUCT_DEFINITION('design','',#{0},#{1})".format(pdf, pd_ctx))
    pds = add("PRODUCT_DEFINITION_SHAPE('','',#{0})".format(pd))

    add("PRODUCT_RELATED_PRODUCT_CATEGORY('part','',(#{0}))".format(product))

    brep_srr = add("ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{0}),#{1})".format(brep, geo_ctx))
    add("SHAPE_DEFINITION_REPRESENTATION(#{0},#{1})".format(pds, brep_srr))

    ts = _dt.datetime.now().replace(microsecond=0).isoformat()
    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION((''),'2;1');\n"
        "FILE_NAME('{name}.stp','{ts}',(''),(''),"
        "'devtools/step_box.py','',' ');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));\n"
        "ENDSEC;\n"
    ).format(name=name, ts=ts)

    return header + "DATA;\n" + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="output .stp path")
    p.add_argument("--lx", type=float, default=0.100, help="length X, metres")
    p.add_argument("--ly", type=float, default=0.010, help="length Y, metres")
    p.add_argument("--lz", type=float, default=0.010, help="length Z, metres")
    p.add_argument("--name", default=None)
    args = p.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    name = args.name or out.stem
    out.write_text(_step_box(args.lx, args.ly, args.lz, name), encoding="ascii", newline="\n")
    print("wrote {0}  ({1:.0f} bytes)  box {2}x{3}x{4} m".format(
        out, out.stat().st_size, args.lx, args.ly, args.lz))
    sidecar = out.with_suffix(out.suffix + ".md")
    sidecar.write_text(
        "# {0}\n\nAxis-aligned box {1} x {2} x {3} m (STEP AP214), generated by\n"
        "`devtools/step_box.py`. Corner at origin; +X is the long axis.\n"
        "Faces: min-X (x=0) and max-X (x={1}) are the load/support faces for the\n"
        "Milestone 0 script.\n".format(name, args.lx, args.ly, args.lz),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
