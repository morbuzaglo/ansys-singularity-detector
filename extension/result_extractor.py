# -*- coding: utf-8 -*-
# ==========================================================================
# result_extractor -- Mechanical runtime layer (IronPython 2.7).
#
# Pulls the reduced per-level diagnostics the convergence engine needs
# (spec S22 / S23 / S32).  Heavy field processing stays in DPF / the
# external layer; this only returns scalars.
#
# NO f-strings / dataclasses / type hints.
# ==========================================================================


class ResultSet(object):
    """Holds result objects added to the tree ONCE, re-read after each solve."""

    def __init__(self, analysis, fixed_support=None):
        self.analysis = analysis
        self.solution = analysis.Solution
        self.fixed_support = fixed_support
        self.eqv = None
        self.total_def = None
        self.reaction = None
        self.strain_energy = None
        self._build()

    PREFIX = "SD_"      # spec S9: temporary objects are clearly marked

    def _name(self, obj, tag):
        try:
            obj.Name = self.PREFIX + tag
        except Exception:
            pass
        return obj

    def _build(self):
        sol = self.solution
        self.eqv = self._name(sol.AddEquivalentStress(), "EquivalentStress")
        self.total_def = self._name(sol.AddTotalDeformation(), "TotalDeformation")
        try:
            if self.fixed_support is not None:
                self.reaction = self._name(sol.AddForceReaction(), "ForceReaction")
                self.reaction.BoundaryConditionSelection = self.fixed_support
        except Exception:
            self.reaction = None
        try:
            self.strain_energy = self._name(sol.AddStrainEnergy(), "StrainEnergy")
        except Exception:
            self.strain_energy = None

    def remove(self):
        """Delete the temporary SD_ result objects (call after the study loop)."""
        for obj in (self.eqv, self.total_def, self.reaction, self.strain_energy):
            if obj is None:
                continue
            try:
                obj.Delete()
            except Exception:
                pass
        self.eqv = self.total_def = self.reaction = self.strain_energy = None

    def _max(self, obj):
        if obj is None:
            return None
        try:
            obj.Activate()
        except Exception:
            pass
        try:
            return {"value": float(obj.Maximum.Value), "unit": str(obj.Maximum.Unit)}
        except Exception:
            return None

    def _minmax(self, obj):
        if obj is None:
            return None
        try:
            obj.Activate()
        except Exception:
            pass
        out = {}
        try:
            out["max"] = float(obj.Maximum.Value)
            out["min"] = float(obj.Minimum.Value)
            out["unit"] = str(obj.Maximum.Unit)
        except Exception:
            return None
        return out

    def _reaction_total(self):
        r = self.reaction
        if r is None:
            return None
        try:
            r.Activate()
        except Exception:
            pass
        for chain in (("Maximum", "Value"),):     # scalar magnitude probe
            try:
                return {"value": float(r.Maximum.Value), "unit": str(r.Maximum.Unit)}
            except Exception:
                pass
        # component form
        try:
            fx = float(r.XAxis.Value)
            fy = float(r.YAxis.Value)
            fz = float(r.ZAxis.Value)
            mag = (fx * fx + fy * fy + fz * fz) ** 0.5
            return {"value": mag, "components": [fx, fy, fz], "unit": str(r.XAxis.Unit)}
        except Exception:
            return None

    def read(self):
        """Return the reduced diagnostics dict for the current solved state."""
        d = {
            "peak_equivalent_stress": self._max(self.eqv),
            "equivalent_stress_minmax": self._minmax(self.eqv),
            "max_total_deformation": self._max(self.total_def),
            "reaction_at_fixed_support": self._reaction_total(),
            "strain_energy": self._max(self.strain_energy),
            "solve_status": str(self.solution.Status),
        }
        try:
            d["solve_seconds"] = float(self.solution.ElapsedRunTime)
        except Exception:
            d["solve_seconds"] = None
        return d
