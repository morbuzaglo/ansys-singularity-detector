# -*- coding: utf-8 -*-
# ==========================================================================
# mesh_manager -- Mechanical runtime layer (IronPython 2.7).
#
# NO f-strings / dataclasses / type hints / numpy.  Keep it conservative.
#
# Abstracts the mesh-refinement STRATEGY (spec S8) behind an interface so
# global element sizing (v1) can later be swapped for sphere-of-influence /
# body / face / edge / named-selection sizing without touching the study loop.
#
# Also captures + restores the user's original mesh settings (spec S9) and
# measures the ACTUAL characteristic element size after meshing (spec S7:
# "do not assume the requested global element size equals the actual local
# mesh size").
# ==========================================================================

import time

from mech_env import G


def _q(value_m):
    """metres -> Quantity Mechanical accepts."""
    return G.require("Quantity")(repr(float(value_m)) + " [m]")


class RefinementStrategy(object):
    """Interface. apply(size_m) configures controls; describe() -> str."""
    name = "abstract"

    def apply(self, mesh, size_m):
        raise NotImplementedError

    def describe(self):
        return self.name


class GlobalElementSize(RefinementStrategy):
    name = "global_element_size"

    def apply(self, mesh, size_m):
        mesh.ElementSize = _q(size_m)


class MeshManager(object):
    """Owns mesh generation + measurement + original-state restore for one model."""

    TEMP_PREFIX = "SD_"          # spec S9: temporary objects get this prefix

    def __init__(self, model, strategy=None):
        self.model = model
        self.mesh = model.Mesh
        self.strategy = strategy or GlobalElementSize()
        self._original = None

    # -- original state -----------------------------------------------------
    def capture_original(self):
        m = self.mesh
        snap = {}
        for attr in ("ElementSize", "ElementOrder", "Resolution",
                     "Defeaturing", "DefeatureSize", "UseAdaptiveSizing",
                     "MeshDefeaturing", "TransitionRatio", "GrowthRate"):
            try:
                snap[attr] = getattr(m, attr)
            except Exception:
                pass
        self._original = snap
        return dict((k, str(v)) for k, v in snap.items())

    def restore_original(self):
        if not self._original:
            return False
        ok = True
        for attr, value in self._original.items():
            try:
                setattr(self.mesh, attr, value)
            except Exception:
                ok = False
        try:
            self.model.Mesh.ClearGeneratedData()
        except Exception:
            pass
        return ok

    # -- generation -------------------------------------------------------
    def apply_size(self, size_m):
        self.strategy.apply(self.mesh, size_m)

    def generate(self):
        # Clear only the MESH (not model-wide generated data -- clearing the
        # latter in an embedded session was leaving Solution in 'SolveRequired').
        try:
            self.mesh.ClearGeneratedData()
        except Exception:
            pass
        t0 = time.time()
        self.mesh.GenerateMesh()
        return time.time() - t0

    # -- measurement ----------------------------------------------------
    def _total_volume_m3(self):
        """Sum of body volumes, converted to m^3.  body.Volume is in the active
        unit system (often mm^3 in Mechanical), so convert explicitly."""
        raw = 0.0
        try:
            geo = G.require("ExtAPI").DataModel.GeoData
            for asm in geo.Assemblies:
                for part in asm.Parts:
                    for body in part.Bodies:
                        try:
                            raw += float(body.Volume)
                        except Exception:
                            pass
        except Exception:
            pass
        if raw <= 0.0:
            return 0.0
        # try the ACT units helper
        try:
            import units
            vol_unit = G.require("ExtAPI").DataModel.CurrentUnitFromQuantityName("Volume")
            return float(units.ConvertUnit(raw, vol_unit, "m^3", "Volume"))
        except Exception:
            pass
        # fallback heuristic: a real FE body is ~1e-6..1e2 m^3; if 'raw' is far
        # bigger it is almost certainly mm^3 (factor 1e9) or cm^3 (1e6).
        if raw > 1.0e4:
            return raw * 1.0e-9
        if raw > 1.0:
            return raw * 1.0e-6
        return raw

    def actual_char_size_m(self, n_elements):
        """Best estimate of the real characteristic element size.

        1) mean element edge length from a mesh metric if available;
        2) else (total volume / element count) ** (1/3).
        """
        # (1) metric (best-effort; not required)
        try:
            MeshMetricType = G.require("Ansys").Mechanical.DataModel.Enums.MeshMetricType
            self.mesh.MeshMetric = MeshMetricType.Aspectratio
        except Exception:
            pass
        # (2) volumetric estimate -- robust, no API dependency
        if n_elements and n_elements > 0:
            v = self._total_volume_m3()
            if v > 0.0:
                return (v / float(n_elements)) ** (1.0 / 3.0)
        return None

    def stats(self, generate_seconds=None):
        n_nodes = int(self.mesh.Nodes)
        n_elems = int(self.mesh.Elements)
        out = {
            "strategy": self.strategy.describe(),
            "nodes": n_nodes,
            "elements": n_elems,
            "actual_char_size_m": self.actual_char_size_m(n_elems),
            "generate_seconds": None if generate_seconds is None else round(generate_seconds, 2),
        }
        return out
