"""Emit a valid STEP AP214 B-rep for a straight-edged prism (extruded polygon).

External automation layer -> CPython 3.  No CAD kernel: deterministic, instant,
version-independent.  Covers every fixture whose cross-section is a simple
polygon with straight edges -- the plain bar, the L-bracket (re-entrant corner),
sharp V-notches -- which are exactly the Milestone 4 divergent benchmarks.
Curved features (holes, fillets) need `make_geometry` (cadquery).

    from devtools.step_prism import write_prism
    write_prism("test_models/lbracket.stp",
                polygon=[(0,0),(60,0),(60,20),(20,20),(20,60),(0,60)],  # mm, CCW
                thickness_mm=10)

The polygon is given in millimetres and CCW; output STEP is in metres.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


def _signed_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def step_prism(polygon_mm, thickness_mm, name="prism"):
    poly = [(float(x) / 1000.0, float(y) / 1000.0) for x, y in polygon_mm]
    if len(poly) < 3:
        raise ValueError("polygon needs >= 3 vertices")
    if _signed_area(poly) < 0:                     # ensure CCW (normal +Z on top)
        poly = poly[::-1]
    t = float(thickness_mm) / 1000.0
    n = len(poly)

    # vertex coords: 0..n-1 bottom ring (z=0), n..2n-1 top ring (z=t)
    P = {}
    for i, (x, y) in enumerate(poly):
        P[i] = (x, y, 0.0)
        P[i + n] = (x, y, t)

    lines: list[str] = []
    _id = [0]

    def nid():
        _id[0] += 1
        return _id[0]

    def add(s):
        i = nid()
        lines.append("#{0}={1};".format(i, s))
        return i

    def pt(xyz):
        return add("CARTESIAN_POINT('',({0:.10g},{1:.10g},{2:.10g}))".format(*xyz))

    def dr(xyz):
        return add("DIRECTION('',({0:.10g},{1:.10g},{2:.10g}))".format(*xyz))

    # units + geometric context (classic combined entity)
    lu = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.))")
    au = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    su = add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{0},"
              "'distance_accuracy_value','confusion accuracy')".format(lu))
    geo_ctx = add("(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
                  "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{0}))"
                  "GLOBAL_UNIT_ASSIGNED_CONTEXT((#{1},#{2},#{3}))"
                  "REPRESENTATION_CONTEXT('',''))".format(unc, lu, au, su))

    vpt = {k: add("VERTEX_POINT('',#{0})".format(pt(v))) for k, v in P.items()}

    def edge(a, b):
        pa, pb = P[a], P[b]
        d = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        mag = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
        u = (d[0] / mag, d[1] / mag, d[2] / mag)
        line = add("LINE('',#{0},#{1})".format(
            pt(pa), add("VECTOR('',#{0},{1:.10g})".format(dr(u), mag))))
        return add("EDGE_CURVE('',#{0},#{1},#{2},.T.)".format(vpt[a], vpt[b], line))

    bot = {i: edge(i, (i + 1) % n) for i in range(n)}            # z=0 ring
    top = {i: edge(n + i, n + (i + 1) % n) for i in range(n)}    # z=t ring
    ver = {i: edge(i, n + i) for i in range(n)}                  # verticals

    def oriented(ec, fwd):
        return add("ORIENTED_EDGE('',*,*,#{0},.{1}.)".format(ec, "T" if fwd else "F"))

    def face(loop_oe, origin, normal, refdir):
        el = add("EDGE_LOOP('',({0}))".format(",".join("#{0}".format(x) for x in loop_oe)))
        fb = add("FACE_OUTER_BOUND('',#{0},.T.)".format(el))
        a2 = add("AXIS2_PLACEMENT_3D('',#{0},#{1},#{2})".format(pt(origin), dr(normal), dr(refdir)))
        pl = add("PLANE('',#{0})".format(a2))
        return add("ADVANCED_FACE('',(#{0}),#{1},.T.)".format(fb, pl))

    faces = []
    # side faces: for CCW polygon, outward normal of edge (vi->vi+1) is (dy,-dx)
    for i in range(n):
        a, b = i, (i + 1) % n
        dx = poly[b][0] - poly[a][0]
        dy = poly[b][1] - poly[a][1]
        L = (dx * dx + dy * dy) ** 0.5
        nrm = (dy / L, -dx / L, 0.0)
        loop = [oriented(bot[i], True), oriented(ver[b], True),
                oriented(top[i], False), oriented(ver[a], False)]
        faces.append(face(loop, P[a], nrm, (0.0, 0.0, 1.0)))

    # bottom cap: normal -Z, loop clockwise seen from +Z -> reverse ring
    loop_b = [oriented(bot[(n - 1 - k) % n], False) for k in range(n)]
    faces.append(face(loop_b, P[0], (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)))
    # top cap: normal +Z, CCW
    loop_t = [oriented(top[k], True) for k in range(n)]
    faces.append(face(loop_t, P[n], (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)))

    shell = add("CLOSED_SHELL('',({0}))".format(",".join("#{0}".format(x) for x in faces)))
    brep = add("MANIFOLD_SOLID_BREP('{0}',#{1})".format(name, shell))

    app_ctx = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2010,#{0})"
        .format(app_ctx))
    product = add("PRODUCT('{0}','{0}','',(#{1}))".format(
        name, add("PRODUCT_CONTEXT('',#{0},'mechanical')".format(app_ctx))))
    pdf = add("PRODUCT_DEFINITION_FORMATION('','',#{0})".format(product))
    pdc = add("PRODUCT_DEFINITION_CONTEXT('part definition',#{0},'design')".format(app_ctx))
    pd = add("PRODUCT_DEFINITION('design','',#{0},#{1})".format(pdf, pdc))
    pds = add("PRODUCT_DEFINITION_SHAPE('','',#{0})".format(pd))
    add("PRODUCT_RELATED_PRODUCT_CATEGORY('part','',(#{0}))".format(product))
    absr = add("ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{0}),#{1})".format(brep, geo_ctx))
    add("SHAPE_DEFINITION_REPRESENTATION(#{0},#{1})".format(pds, absr))

    ts = _dt.datetime.now().replace(microsecond=0).isoformat()
    header = (
        "ISO-10303-21;\nHEADER;\n"
        "FILE_DESCRIPTION((''),'2;1');\n"
        "FILE_NAME('{name}.stp','{ts}',(''),(''),'devtools/step_prism.py','',' ');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));\nENDSEC;\n"
    ).format(name=name, ts=ts)
    return header + "DATA;\n" + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


def write_prism(out_path, polygon, thickness_mm, name=None):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    name = name or out.stem
    out.write_text(step_prism(polygon, thickness_mm, name), encoding="ascii", newline="\n")
    return out


# named benchmark cross-sections (mm, CCW) -----------------------------------
FIXTURES = {
    "bar":       (lambda: ([(0, 0), (100, 0), (100, 10), (0, 10)], 10),
                  "Plain 100x10x10 bar (control -- converges)."),
    "lbracket":  (lambda: ([(0, 0), (60, 0), (60, 20), (20, 20), (20, 60), (0, 60)], 10),
                  "L-bracket, sharp 90deg re-entrant corner at (20,20) -- SINGULAR."),
    "vnotch":    (lambda: ([(0, 0), (80, 0), (80, 40), (44, 40), (40, 20),
                            (36, 40), (0, 40)], 8),
                  "Plate with a sharp V-notch to mid-depth -- SINGULAR / strongly divergent."),
}


def _cli(argv=None):
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fixture", nargs="?", choices=sorted(FIXTURES))
    p.add_argument("--out-dir", default="test_models")
    p.add_argument("--list", action="store_true")
    a = p.parse_args(argv)
    if a.list or not a.fixture:
        for k, (mk, desc) in sorted(FIXTURES.items()):
            print("{0:12s} {1}".format(k, desc))
        return 0
    poly, thk = FIXTURES[a.fixture][0]()
    out = write_prism(Path(a.out_dir) / (a.fixture + ".stp"), poly, thk, a.fixture)
    out.with_suffix(".stp.md").write_text(
        "# {0}\n\n{1}\n\nGenerated by `devtools/step_prism.py {0}`. STEP units metres.\n"
        .format(a.fixture, FIXTURES[a.fixture][1]), encoding="utf-8")
    print("wrote {0} ({1} bytes)".format(out, out.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
