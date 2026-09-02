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
import csv
import math

_CACHE = {}          # path -> _Field


def _run_dir_default():
    return os.environ.get("SD_RUN_DIR") or os.getcwd()


class _Field(object):
    """Uniform-grid nearest lookup over the precomputed contour nodes."""

    COLS = ("raw_stress", "confidence_pct", "filtered_stress")

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


def load_field(csv_path=None):
    """Cache and return the _Field for contour_fields.csv (default: run dir)."""
    if csv_path is None:
        csv_path = os.path.join(_run_dir_default(), "contour_fields.csv")
    if csv_path not in _CACHE:
        if not os.path.isfile(csv_path):
            raise IOError("contour field file not found: " + csv_path)
        _CACHE[csv_path] = _Field(csv_path)
    return _CACHE[csv_path]


def fill_collector(result, collector, column, csv_path=None):
    """ACT <evaluate> helper: for each node id the collector asks about, look up
    the precomputed value by that node's physical coordinate and store it.

    ``column`` is one of 'raw_stress', 'confidence_pct', 'filtered_stress'.
    A missing / Not-Recoverable value stores System.Double.MaxValue.
    """
    from System import Double        # noqa: F401  (.NET, present in Mechanical)

    fld = load_field(csv_path)
    try:
        mesh = result.Analysis.MeshData
    except Exception:
        mesh = None

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
        else:
            collector.SetValues(nid, [float(v)])


# --- toolbar callback ---------------------------------------------------
_RESULT_NAMES = ("Raw Stress -- Original FE Solution",
                 "Singularity Confidence [%]",
                 "Singularity-Filtered Stress")


def add_singularity_contours(analysis):
    """Toolbar button: drop the three custom results into the tree.  They
    evaluate from contour_fields.csv in the analysis working dir (produced by
    the external pipeline); if it is absent the results show the no-value
    sentinel until the study has been run."""
    ext = ExtAPI.ExtensionManager.CurrentExtension          # noqa: F821
    made = []
    for name in _RESULT_NAMES:
        try:
            analysis.CreateResultObject(name, ext)
            made.append(name)
        except Exception:
            pass
    try:
        ExtAPI.Log.WriteMessage("[SingularityDetector] added results: " + ", ".join(made))  # noqa: F821
    except Exception:
        print("[SingularityDetector] added results: " + ", ".join(made))


# --- the three <evaluate> callbacks the extension XML points at ----------
def evaluate_raw_stress(result, stepInfo, collector):
    fill_collector(result, collector, "raw_stress")


def evaluate_singularity_confidence(result, stepInfo, collector):
    fill_collector(result, collector, "confidence_pct")


def evaluate_singularity_filtered_stress(result, stepInfo, collector):
    fill_collector(result, collector, "filtered_stress")
