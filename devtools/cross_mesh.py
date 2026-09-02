"""Cross-mesh stress series (Milestone 3).

External automation layer -> CPython 3.

Given the per-level ``file.rst`` a convergence study preserved, build, for every
reference (finest-mesh) location x, the series

    sigma_vm(x, h_0), sigma_vm(x, h_1), ... , sigma_vm(x, h_L-1)

by mapping each coarser level's nodal von Mises field onto the reference
coordinates (spec S24).  Node IDs are never compared across levels.

The mapping mechanism (official DPF ``mapping.on_coordinates`` vs the validated
scipy fallback) is chosen by ``compatibility.probe_dpf`` and recorded per level.
Independent accuracy is checked by ``field_mapping.analytic_validation`` /
``dpf_adapter`` smoke paths before this feeds any classifier.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np

from devtools import dpf_adapter


@dataclasses.dataclass
class CrossMeshSeries:
    ref_coords: np.ndarray        # (M, 3) finest-mesh node coordinates
    length_unit: str
    stress_unit: str
    sizes_m: list[float | None]   # (L,) actual char element size per level (metres), coarse->fine
    matrix: np.ndarray            # (M, L) von Mises at ref points, per level
    methods: list[str]            # (L,) mapping method used to fill each column
    outside_any: np.ndarray       # (M,) bool: point fell outside some source hull
    ref_index: int                # column index that is the reference (identity) level

    @property
    def m(self) -> int:
        return self.ref_coords.shape[0]

    @property
    def n_levels(self) -> int:
        return self.matrix.shape[1]

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.matrix).all(axis=1) & ~self.outside_any

    def increments(self) -> np.ndarray:
        """(M, L-1) absolute change between consecutive levels."""
        return np.abs(np.diff(self.matrix, axis=1))

    def divergence_indicator(self) -> np.ndarray:
        """Per point: ratio of the last increment to the previous one.
        >1 grows (candidate singular), <1 shrinks (converging). NaN if <3 levels
        or a zero previous increment. This is an INPUT to Milestone 4, not a
        verdict."""
        if self.n_levels < 3:
            return np.full(self.m, np.nan)
        inc = self.increments()
        prev, last = inc[:, -2], inc[:, -1]
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(prev > 0, last / prev, np.nan)
        return r

    def summary(self) -> dict:
        v = self.valid
        cols = []
        for j in range(self.n_levels):
            c = self.matrix[v, j]
            cols.append({
                "level": j,
                "size_m": self.sizes_m[j],
                "method": self.methods[j],
                "peak": float(np.nanmax(c)) if c.size else None,
                "mean": float(np.nanmean(c)) if c.size else None,
            })
        r = self.divergence_indicator()[v]
        return {
            "n_ref_points": self.m,
            "n_valid": int(v.sum()),
            "valid_fraction": float(v.mean()),
            "ref_index": self.ref_index,
            "levels": cols,
            "median_divergence_ratio": float(np.nanmedian(r)) if r.size else None,
            "frac_points_growing": float(np.nanmean(r > 1.0)) if r.size else None,
        }

    def save_npz(self, path: str) -> str:
        np.savez_compressed(
            path,
            ref_coords=self.ref_coords, matrix=self.matrix,
            sizes_m=np.array([np.nan if s is None else s for s in self.sizes_m], dtype=float),
            outside_any=self.outside_any, ref_index=self.ref_index,
            methods=np.array(self.methods, dtype=object),
            length_unit=self.length_unit, stress_unit=self.stress_unit,
        )
        return path

    def write_csv(self, path: str, max_rows: int | None = 5000) -> str:
        import csv

        idx = np.where(self.valid)[0]
        if max_rows and idx.size > max_rows:
            step = int(np.ceil(idx.size / max_rows))
            idx = idx[::step]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            head = ["x", "y", "z"] + ["vm_L{0}".format(j) for j in range(self.n_levels)] + ["div_ratio"]
            w.writerow(head)
            dr = self.divergence_indicator()
            for i in idx:
                w.writerow(list(self.ref_coords[i]) + list(self.matrix[i]) +
                           [None if not np.isfinite(dr[i]) else dr[i]])
        return path


def _actual_sizes_from_study(study_dir: Path, n_levels: int) -> list[float | None]:
    summ = study_dir / "study_result.json"
    if not summ.is_file():
        return [None] * n_levels
    data = json.loads(summ.read_text(encoding="utf-8"))
    out: list[float | None] = [None] * n_levels
    for lv in data.get("levels", []):
        i = lv.get("index")
        if isinstance(i, int) and 0 <= i < n_levels:
            out[i] = lv.get("actual_char_size_m")
    return out


def build_series(
    level_rst_paths: list[str],
    *,
    sizes_m: list[float | None] | None = None,
    prefer: str | None = None,
) -> CrossMeshSeries:
    if len(level_rst_paths) < 2:
        raise ValueError("need >= 2 level result files")

    fields = [dpf_adapter.read_von_mises_nodal(p) for p in level_rst_paths]
    ref_index = int(np.argmax([f.n_nodes for f in fields]))
    ref = fields[ref_index]
    ref_coords = ref.coords
    m = ref_coords.shape[0]

    matrix = np.full((m, len(fields)), np.nan)
    methods: list[str] = []
    outside_any = np.zeros(m, dtype=bool)

    for j, lf in enumerate(fields):
        if j == ref_index:
            matrix[:, j] = ref.von_mises
            methods.append("identity")
            continue
        rep = dpf_adapter.map_level_on_coordinates(lf, ref_coords, prefer=prefer)
        matrix[:, j] = rep.values
        methods.append(rep.method)
        if rep.outside_hull is not None:
            outside_any |= rep.outside_hull

    return CrossMeshSeries(
        ref_coords=ref_coords,
        length_unit=ref.length_unit,
        stress_unit=ref.unit,
        sizes_m=sizes_m if sizes_m is not None else [None] * len(fields),
        matrix=matrix,
        methods=methods,
        outside_any=outside_any,
        ref_index=ref_index,
    )


def from_study_dir(study_dir: str, *, prefer: str | None = None) -> CrossMeshSeries:
    d = Path(study_dir)
    rsts = sorted(str(p / "file.rst") for p in d.glob("level_*") if (p / "file.rst").is_file())
    if len(rsts) < 2:
        raise ValueError("study dir {0} has < 2 preserved level rst files".format(study_dir))
    sizes = _actual_sizes_from_study(d, len(rsts))
    return build_series(rsts, sizes_m=sizes, prefer=prefer)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build a cross-mesh von Mises series from a study dir.")
    p.add_argument("study_dir")
    p.add_argument("--prefer", choices=["dpf_on_coordinates", "scipy_linear"], default=None)
    p.add_argument("--npz", default=None)
    p.add_argument("--csv", default=None)
    args = p.parse_args()

    s = from_study_dir(args.study_dir, prefer=args.prefer)
    summ = s.summary()
    print(json.dumps(summ, indent=2))
    if args.npz:
        print("npz:", s.save_npz(args.npz))
    if args.csv:
        print("csv:", s.write_csv(args.csv))
