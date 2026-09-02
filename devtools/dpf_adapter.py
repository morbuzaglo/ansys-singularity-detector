"""DPF boundary for cross-mesh stress mapping (Milestone 3).

External automation layer -> CPython 3.

Wraps the slice of DPF this project needs, behind the compatibility probe
(``compatibility.probe_dpf``):

  * open a preserved ``file.rst``
  * read nodal von Mises stress + node coordinates
  * resample a level's stress field onto arbitrary target coordinates, using the
    official ``mapping.on_coordinates`` operator (which uses the source mesh
    shape functions) when present, else the validated scipy fallback in
    ``field_mapping`` -- the choice is recorded, never silent (spec S25).

Coordinates come back in the result file's length unit (mm for a standard
Mechanical NMM model).
"""

from __future__ import annotations

import dataclasses
import os

import numpy as np

from devtools import compatibility, field_mapping


class DpfUnavailable(RuntimeError):
    pass


def _dpf():
    caps = compatibility.probe_dpf()
    if not caps.available:
        raise DpfUnavailable("; ".join(caps.notes) or "DPF not available")
    from ansys.dpf import core as dpf
    return dpf, caps


@dataclasses.dataclass
class LevelField:
    """Nodal von Mises field of one solved level, plus the live DPF handle so the
    official mapping operator can use the real mesh support."""
    rst_path: str
    coords: np.ndarray          # (N, 3) in `length_unit`
    von_mises: np.ndarray       # (N,) in `unit`
    unit: str
    length_unit: str
    _dpf_field: object = None    # ansys.dpf.core Field (carries its meshed_region)

    @property
    def n_nodes(self) -> int:
        return self.coords.shape[0]

    def point_field(self) -> field_mapping.PointField:
        return field_mapping.PointField(self.coords, self.von_mises,
                                        name="von_mises", unit=self.unit, location="nodal")


def read_von_mises_nodal(rst_path: str) -> LevelField:
    dpf, _ = _dpf()
    rst_path = os.path.abspath(rst_path)
    if not os.path.isfile(rst_path):
        raise FileNotFoundError(rst_path)
    from ansys.dpf.core import operators as ops

    model = dpf.Model(rst_path)
    mesh = model.metadata.meshed_region
    coords = np.array(mesh.nodes.coordinates_field.data, dtype=float).reshape(-1, 3)
    node_ids = np.array(mesh.nodes.scoping.ids, dtype=np.int64)

    stress = model.results.stress()
    stress.inputs.requested_location.connect(dpf.locations.nodal)
    vm_field = ops.invariant.von_mises_eqv_fc(fields_container=stress.eval()).eval()[0]
    vm = np.array(vm_field.data, dtype=float).reshape(-1)
    unit = getattr(vm_field, "unit", "") or ""

    # align field values to mesh node order (DPF scoping may differ)
    try:
        fids = np.array(vm_field.scoping.ids, dtype=np.int64)
        if not np.array_equal(fids, node_ids):
            pos = {nid: k for k, nid in enumerate(node_ids)}
            keep = np.array([pos.get(int(i), -1) for i in fids])
            good = keep >= 0
            coords = coords[keep[good]]
            vm = vm[good]
    except Exception:
        pass

    length_unit = "mm"
    try:
        us = str(model.metadata.result_info.unit_system_name or "").lower()
        if us.startswith("mks") or (" m," in us) or us.strip().startswith("si"):
            length_unit = "m"
    except Exception:
        pass

    return LevelField(rst_path=rst_path, coords=coords, von_mises=vm, unit=unit,
                      length_unit=length_unit, _dpf_field=vm_field)


@dataclasses.dataclass
class MapReport:
    method: str                 # 'dpf_on_coordinates' | 'scipy_linear'
    values: np.ndarray          # (M,)
    finite_fraction: float
    outside_hull: np.ndarray | None
    notes: list[str]


def map_level_on_coordinates(
    level: LevelField,
    target_coords: np.ndarray,
    *,
    prefer: str | None = None,
) -> MapReport:
    """Resample ``level``'s von Mises field onto ``target_coords`` (M, 3, same
    length unit as ``level.coords``)."""
    caps = compatibility.probe_dpf()
    method = prefer or caps.choose_mapping()
    tgt = np.asarray(target_coords, dtype=float).reshape(-1, 3)
    notes = list(caps.notes)

    if method == "dpf_on_coordinates" and level._dpf_field is not None:
        try:
            vals = _dpf_map(level._dpf_field, tgt)
            if vals.shape[0] != tgt.shape[0]:
                raise RuntimeError("operator returned {0} of {1} values".format(
                    vals.shape[0], tgt.shape[0]))
            finite = np.isfinite(vals)
            if finite.mean() < 0.5:
                raise RuntimeError("only {0:.0%} finite -> distrust".format(finite.mean()))
            return MapReport("dpf_on_coordinates", vals, float(finite.mean()), None,
                             notes + ["mapping.on_coordinates ok"])
        except Exception as exc:  # noqa: BLE001
            notes.append("DPF mapping failed ({0}); scipy fallback".format(exc))
            method = "scipy_linear"

    mp = field_mapping.map_points_to_points(level.point_field(), tgt, fill_outside="nearest")
    return MapReport("scipy_linear", mp.values, 1.0 - mp.nan_count / max(mp.m, 1),
                     mp.outside_hull, notes + ["scipy LinearNDInterpolator + nearest fill"])


def _dpf_map(src_field, tgt: np.ndarray) -> np.ndarray:
    dpf, _ = _dpf()
    from ansys.dpf.core import operators as ops

    coord_field = dpf.fields_factory.create_3d_vector_field(
        tgt.shape[0], location=dpf.locations.nodal)
    coord_field.data = np.asarray(tgt, dtype=float)
    coord_field.scoping.ids = list(range(1, tgt.shape[0] + 1))

    op = ops.mapping.on_coordinates(
        fields_container=dpf.fields_container_factory.over_time_freq_fields_container([src_field]),
        coordinates=coord_field,
        create_support=True,
    )
    return np.array(op.eval()[0].data, dtype=float).reshape(-1)
