"""Runtime capability detection for DPF / Ansys (spec S25, S47).

External automation layer -> CPython 3.

Prefer *probing* over assuming: ask whether an operator/result actually exists
in the running DPF build, and record which mechanism was chosen and why.  Never
wrap everything in a silent try/except -- when a capability is missing, log the
requested capability, the version, and the fallback taken.
"""

from __future__ import annotations

import dataclasses
import functools


@dataclasses.dataclass
class DpfCapabilities:
    available: bool
    core_version: str | None
    server_version: str | None
    has_mapping_on_coordinates: bool
    has_von_mises_fc: bool
    notes: list[str]

    def choose_mapping(self) -> str:
        """'dpf_on_coordinates' (official) or 'scipy_linear' (validated fallback)."""
        if self.available and self.has_mapping_on_coordinates:
            return "dpf_on_coordinates"
        return "scipy_linear"

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["chosen_mapping"] = self.choose_mapping()
        return d


@functools.lru_cache(maxsize=1)
def probe_dpf() -> DpfCapabilities:
    notes: list[str] = []
    try:
        from ansys.dpf import core as dpf
        core_version = getattr(dpf, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return DpfCapabilities(False, None, None, False, False,
                               ["ansys-dpf-core not importable: {0}".format(exc)])

    server_version = None
    try:
        srv = dpf.start_local_server(as_global=True)
        server_version = str(getattr(srv, "version", None) or getattr(dpf, "server_version", None) or "?")
    except Exception as exc:  # noqa: BLE001
        notes.append("could not start a local DPF server: {0}".format(exc))
        return DpfCapabilities(False, core_version, None, False, False, notes)

    from ansys.dpf.core import operators as ops

    has_map = hasattr(getattr(ops, "mapping", object()), "on_coordinates")
    if not has_map:
        notes.append("operator mapping.on_coordinates NOT present -> scipy fallback")
    has_vm = hasattr(getattr(ops, "invariant", object()), "von_mises_eqv_fc")
    if not has_vm:
        notes.append("operator invariant.von_mises_eqv_fc NOT present")

    return DpfCapabilities(
        available=True,
        core_version=core_version,
        server_version=server_version,
        has_mapping_on_coordinates=bool(has_map),
        has_von_mises_fc=bool(has_vm),
        notes=notes,
    )
