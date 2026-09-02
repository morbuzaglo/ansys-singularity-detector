"""Mesh-refinement plan: the sequence of characteristic element sizes to solve at.

External automation layer -> CPython 3.  Pure + unit-tested; the Mechanical-side
`mesh_manager` just applies whatever sizes this produces.

Spec S7 defaults:
  * start from the user's initial mesh size h0
  * 3 additional refinement levels  -> 4 levels total
  * ratio r = 0.75  (each level 25 % smaller)
  * allowed 0.50 <= r <= 0.90
  * warn if r > 0.85 (levels too similar) or, for global 3D refinement,
    r < 0.65 (cost explodes)

Design S8: the *strategy* is abstracted -- this module only knows "global element
size".  Local-refinement plans (sphere of influence, body/face sizing, …) will be
other builders returning the same MeshPlan shape.
"""

from __future__ import annotations

import dataclasses

R_MIN, R_MAX = 0.50, 0.90
R_WARN_HIGH = 0.85          # levels too similar above this
R_WARN_LOW_GLOBAL_3D = 0.65  # global 3D cost grows very fast below this
DEFAULT_RATIO = 0.75
DEFAULT_REFINEMENTS = 3


@dataclasses.dataclass(frozen=True)
class MeshLevel:
    index: int
    requested_size: float   # characteristic element size to request (same units as h0)


@dataclasses.dataclass
class MeshPlan:
    strategy: str                 # "global_element_size" for v1
    levels: list[MeshLevel]
    ratio: float
    warnings: list[str]

    @property
    def sizes(self) -> list[float]:
        return [lv.requested_size for lv in self.levels]

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "ratio": self.ratio,
            "sizes": self.sizes,
            "warnings": self.warnings,
        }


def global_size_plan(
    h0: float,
    *,
    refinements: int = DEFAULT_REFINEMENTS,
    ratio: float = DEFAULT_RATIO,
    is_global_3d: bool = True,
) -> MeshPlan:
    """Geometric size sequence h0, r*h0, r^2*h0, ... with `refinements` extra levels."""
    if h0 <= 0:
        raise ValueError("h0 must be > 0, got {0!r}".format(h0))
    if refinements < 1:
        raise ValueError("need at least 1 refinement level, got {0!r}".format(refinements))
    if not (R_MIN <= ratio <= R_MAX):
        raise ValueError(
            "ratio {0} outside allowed [{1}, {2}]".format(ratio, R_MIN, R_MAX)
        )

    warnings: list[str] = []
    if ratio > R_WARN_HIGH:
        warnings.append(
            "ratio {0:.2f} > {1}: successive meshes may be too similar to resolve a trend"
            .format(ratio, R_WARN_HIGH)
        )
    if is_global_3d and ratio < R_WARN_LOW_GLOBAL_3D:
        warnings.append(
            "ratio {0:.2f} < {1} with global 3D refinement: element count / solve cost "
            "grows very rapidly".format(ratio, R_WARN_LOW_GLOBAL_3D)
        )

    levels = [MeshLevel(0, float(h0))]
    size = float(h0)
    for i in range(1, refinements + 1):
        size *= ratio
        levels.append(MeshLevel(i, size))

    return MeshPlan("global_element_size", levels, ratio, warnings)


def plan_from_env(h0: float, env: dict | None = None) -> MeshPlan:
    """Build a plan honouring SD_ELEM_SIZES (explicit csv, metres) or
    SD_MESH_REFINEMENTS / SD_MESH_RATIO overrides."""
    import os

    env = env if env is not None else os.environ
    explicit = (env.get("SD_ELEM_SIZES") or "").strip()
    if explicit:
        sizes = [float(x) for x in explicit.replace(";", ",").split(",") if x.strip()]
        if len(sizes) < 2:
            raise ValueError("SD_ELEM_SIZES needs >= 2 sizes, got {0!r}".format(explicit))
        levels = [MeshLevel(i, s) for i, s in enumerate(sizes)]
        ratios = [sizes[i + 1] / sizes[i] for i in range(len(sizes) - 1)]
        warn = []
        if any(r >= 1.0 for r in ratios):
            warn.append("SD_ELEM_SIZES is not monotonically decreasing")
        return MeshPlan("explicit_sizes", levels, sum(ratios) / len(ratios), warn)

    refinements = int(env.get("SD_MESH_REFINEMENTS", DEFAULT_REFINEMENTS))
    ratio = float(env.get("SD_MESH_RATIO", DEFAULT_RATIO))
    return global_size_plan(h0, refinements=refinements, ratio=ratio)
