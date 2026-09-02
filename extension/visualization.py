# -*- coding: utf-8 -*-
# ==========================================================================
# visualization -- Mechanical runtime layer (IronPython 2.7).
#
# Milestone 7: fill the three custom result contours from the fields the
# external pipeline precomputed (devtools/build_contours.py -> contour_fields.csv
# in the analysis working dir):
#
#   "Raw Stress -- Original FE Solution"   (spec S36 -- untouched solver result)
#   "Singularity Confidence [%]"           (spec S35 -- 0..100)
#   "Singularity-Filtered Stress"          (spec S37 -- estimate, NOT true stress)
#
# The <evaluate> callbacks in the extension XML delegate here.  No numpy: a
# uniform grid bucket gives O(1) nearest-node lookup by physical coordinate
# (node ids are not comparable across the analysis reruns, spec S24).
#
# NO f-strings / dataclasses / type hints.
# ==========================================================================

import os
import sys
import csv
import math

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else None
if _HERE and _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from mech_env import G as _G       # engine names bound by the <script> module
except Exception:
    _G = None

_CACHE = {}          # path -> _Field


def _ext_api():
    """ACT injects ExtAPI only into the <script> module (main.py). Imported
    modules get it via mech_env.G, which main.py binds on load / per callback."""
    if _G is not None and getattr(_G, "ExtAPI", None) is not None:
        return _G.ExtAPI
    return None


def _log(msg):
    e = _ext_api()
    try:
        if e is not None:
            e.Log.WriteMessage("[SingularityDetector] " + str(msg))
            return
    except Exception:
        pass
    print("[SingularityDetector] " + str(msg))

# Set this (e.g. from a wizard / a preference) to point the callbacks at a
# specific contour_fields.csv; otherwise they search the analysis working dir.
CONTOUR_CSV_PATH = None

_CSV_NAME = "contour_fields.csv"


def _candidate_paths(working_dir=None):
    seen = []
    if CONTOUR_CSV_PATH:
        seen.append(CONTOUR_CSV_PATH)
    env = os.environ.get("SD_CONTOUR_CSV")
    if env:
        seen.append(env)
    for base in (working_dir, os.environ.get("SD_RUN_DIR"), os.getcwd()):
        if base:
            seen.append(os.path.join(base, _CSV_NAME))
            # a study writes it one level up sometimes; also try ../
            seen.append(os.path.join(base, "..", _CSV_NAME))
    out = []
    for p in seen:
        if p and p not in out:
            out.append(p)
    return out


class _Field(object):
    """Uniform-grid nearest lookup over the precomputed contour nodes."""

    COLS = ("raw_stress", "confidence_pct", "filtered_stress",
            "mesh_divergence", "lambda_local", "geometry_prior")

    def __init__(self, csv_path):
        self.path = csv_path
        self.pts = []            # list of (x, y, z)
        self.vals = {c: [] for c in self.COLS}
        self.status = []
        self._load()
        self._build_grid()

    def _load(self):
        f = open(self.path, "r")
        try:
            r = csv.DictReader(f)
            for row in r:
                self.pts.append((float(row["x"]), float(row["y"]), float(row["z"])))
                for c in self.COLS:
                    v = row.get(c, "")
                    self.vals[c].append(float(v) if v not in ("", None) else float("nan"))
                self.status.append(row.get("recovery_status", ""))
        finally:
            f.close()

    def _build_grid(self):
        n = len(self.pts)
        if n == 0:
            self.cell = 1.0
            self.grid = {}
            self.lo = (0.0, 0.0, 0.0)
            return
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        zs = [p[2] for p in self.pts]
        self.lo = (min(xs), min(ys), min(zs))
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-9)
        # aim for ~1 point per cell on average
        self.cell = span / max(int(round(n ** (1.0 / 3.0))), 1)
        self.grid = {}
        for i, p in enumerate(self.pts):
            key = self._key(p)
            self.grid.setdefault(key, []).append(i)

    def _key(self, p):
        return (int((p[0] - self.lo[0]) / self.cell),
                int((p[1] - self.lo[1]) / self.cell),
                int((p[2] - self.lo[2]) / self.cell))

    def nearest_index(self, xyz):
        if not self.pts:
            return -1
        kx, ky, kz = self._key(xyz)
        best_i, best_d2 = -1, None
        ring = 0
        while best_i < 0 or ring <= 2:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                            p = self.pts[i]
                            d2 = ((p[0] - xyz[0]) ** 2 + (p[1] - xyz[1]) ** 2
                                  + (p[2] - xyz[2]) ** 2)
                            if best_d2 is None or d2 < best_d2:
                                best_d2, best_i = d2, i
            ring += 1
            if ring > 40:
                break
        return best_i

    def value_at(self, xyz, column):
        i = self.nearest_index(xyz)
        if i < 0:
            return None
        return self.vals[column][i]


_META_CACHE = {}


def _meta_for(field_path):
    """Read contour_meta.json sitting next to a resolved contour_fields.csv.
    Returns {} if absent."""
    try:
        d = os.path.dirname(os.path.abspath(field_path))
    except Exception:
        return {}
    if d in _META_CACHE:
        return _META_CACHE[d]
    meta = {}
    p = os.path.join(d, "contour_meta.json")
    try:
        if os.path.isfile(p):
            import json as _json
            f = open(p, "r")
            try:
                meta = _json.load(f) or {}
            finally:
                f.close()
    except Exception:
        meta = {}
    _META_CACHE[d] = meta
    return meta


def load_field(csv_path=None, working_dir=None):
    """Cache and return the _Field for contour_fields.csv.

    If ``csv_path`` is given it is used directly; otherwise search
    CONTOUR_CSV_PATH, $SD_CONTOUR_CSV, the analysis working dir, $SD_RUN_DIR and
    the cwd (see _candidate_paths)."""
    if csv_path is not None:
        cands = [csv_path]
    else:
        cands = _candidate_paths(working_dir)
    for p in cands:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            if p not in _CACHE:
                _CACHE[p] = _Field(p)
            return _CACHE[p]
    raise IOError("contour field file not found; looked in: " + "; ".join(cands))


_MISSING_LOGGED = [False]


def fill_collector(result, collector, column, csv_path=None, mask_below=None):
    """ACT <evaluate> helper: for each node id the collector asks about, look up
    the precomputed value by that node's physical coordinate and store it.

    ``column`` is one of the contour_fields.csv value columns: 'raw_stress',
    'confidence_pct', 'filtered_stress', 'mesh_divergence', 'lambda_local',
    'geometry_prior'.
    A missing / Not-Recoverable value -- and the whole file being absent (the
    study has not been run yet, or Mechanical is re-evaluating mid-study) --
    stores System.Double.MaxValue rather than raising, so the result just shows
    "no data" instead of erroring on every solve.

    ``mask_below`` (float): if set, values strictly below it are stored as the
    no-data sentinel -- used by the "flagged only" confidence contour so it
    lights up only where a singularity is actually indicated.
    """
    from System import Double        # noqa: F401  (.NET, present in Mechanical)

    wd = None
    try:
        wd = result.Analysis.WorkingDir
    except Exception:
        wd = None
    try:
        fld = load_field(csv_path, working_dir=wd)
    except Exception:
        if not _MISSING_LOGGED[0]:
            _log("contour_fields.csv not available yet -- results show no data "
                 "until a Singularity Study has finished")
            _MISSING_LOGGED[0] = True
        for nid in collector.Ids:
            collector.SetValues(nid, [Double.MaxValue])
        return
    try:
        mesh = result.Analysis.MeshData
    except Exception:
        mesh = None

    if mask_below == "threshold":
        try:
            mask_below = float(_meta_for(fld.path).get("threshold"))
        except Exception:
            mask_below = None

    for nid in collector.Ids:
        xyz = None
        if mesh is not None:
            try:
                node = mesh.NodeById(nid)
                xyz = (node.X, node.Y, node.Z)
            except Exception:
                xyz = None
        if xyz is None:
            collector.SetValues(nid, [Double.MaxValue])
            continue
        v = fld.value_at(xyz, column)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            collector.SetValues(nid, [Double.MaxValue])
        elif mask_below is not None and float(v) < mask_below:
            collector.SetValues(nid, [Double.MaxValue])
        else:
            collector.SetValues(nid, [float(v)])


# --- toolbar callback ---------------------------------------------------
# every name here MUST match a <result name="..."> in SingularityDetector.xml.
# order = the order "Add Contours" inserts them.
_RESULT_NAMES = ("Singularity Confidence",
                 "Singularity Confidence (flagged)",
                 "Mesh Divergence Evidence",
                 "Local Divergence Exponent",
                 "Geometry Prior",
                 "Raw Stress (Original FE Solution)",
                 "Singularity-Filtered Stress")


def add_singularity_contours(analysis, ext=None, names=None):
    """Drop the custom Singularity Detector results into the tree.  They evaluate
    from contour_fields.csv (produced by the external pipeline); absent it, they
    show the no-value sentinel until the study has been run.

    ``names`` -- subset of _RESULT_NAMES to add (default: all of them).
    ``ext`` = the current extension handle (main.py passes
    ExtAPI.ExtensionManager.CurrentExtension).  If not given we fall back to the
    1-argument CreateResultObject form (valid per the ACTResults sample)."""
    if ext is None:
        e = _ext_api()
        if e is not None:
            try:
                ext = e.ExtensionManager.CurrentExtension
            except Exception:
                ext = None
    want = list(names) if names else list(_RESULT_NAMES)
    made, errs = [], []
    for name in want:
        ok = False
        for attempt in ((name, ext), (name,)):
            if attempt[-1] is None and len(attempt) == 2:
                continue
            try:
                analysis.CreateResultObject(*attempt)
                ok = True
                break
            except Exception:
                exc = sys.exc_info()[1]
        if ok:
            made.append(name)
        else:
            errs.append("%s: %s" % (name, exc))
    _log("added results: " + ", ".join(made) + ("  |  errors: " + "; ".join(errs) if errs else ""))
    if errs and not made:
        raise RuntimeError("could not create any Singularity Detector result: " + "; ".join(errs))
    return made


def remove_singularity_contours(analysis):
    """Delete our result objects from the tree (used at the start of a study run
    so Mechanical doesn't try to evaluate them on every level's solve, before
    contour_fields.csv exists)."""
    removed = []
    try:
        kids = list(analysis.Solution.Children)
    except Exception:
        kids = []
    for k in kids:
        try:
            if str(k.Name) in _RESULT_NAMES:
                k.Delete()
                removed.append(str(k.Name))
        except Exception:
            pass
    if removed:
        _log("removed stale results: " + ", ".join(removed))
    return removed


# --- the <evaluate> callbacks the extension XML points at ---------------
def evaluate_raw_stress(result, stepInfo, collector):
    fill_collector(result, collector, "raw_stress")


def evaluate_singularity_confidence(result, stepInfo, collector):
    # full field: every node coloured by its 0-100 score
    fill_collector(result, collector, "confidence_pct")


def evaluate_singularity_confidence_flagged(result, stepInfo, collector):
    # only nodes at/above the study confidence threshold (rest = no data)
    fill_collector(result, collector, "confidence_pct", mask_below="threshold")


def evaluate_singularity_filtered_stress(result, stepInfo, collector):
    fill_collector(result, collector, "filtered_stress")


def evaluate_mesh_divergence(result, stepInfo, collector):
    # S28/S33 primary term: does stress diverge with mesh refinement here (0..1)
    fill_collector(result, collector, "mesh_divergence")


def evaluate_lambda_local(result, stepInfo, collector):
    # local divergence exponent estimate; sparse (no data away from hot spots)
    fill_collector(result, collector, "lambda_local")


def evaluate_geometry_prior(result, stepInfo, collector):
    # S31: proximity to a re-entrant edge / restrained face (0..1)
    fill_collector(result, collector, "geometry_prior")
