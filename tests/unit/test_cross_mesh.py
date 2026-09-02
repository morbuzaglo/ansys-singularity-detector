"""Unit tests for devtools.cross_mesh -- no Ansys, no DPF.

The series MATH (increments / divergence ratio / valid mask / summary / csv) is
tested directly; ``build_series`` is tested with ``dpf_adapter`` monkey-patched
so the reference-selection + per-level mapping wiring is covered without a
server.
"""

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import cross_mesh, dpf_adapter, field_mapping  # noqa: E402

RNG = np.random.default_rng(7)


def _series(matrix, coords=None, sizes=None, outside=None):
    matrix = np.asarray(matrix, float)
    m, L = matrix.shape
    return cross_mesh.CrossMeshSeries(
        ref_coords=coords if coords is not None else RNG.random((m, 3)),
        length_unit="mm", stress_unit="MPa",
        sizes_m=sizes if sizes is not None else [None] * L,
        matrix=matrix,
        methods=["identity"] + ["dpf_on_coordinates"] * (L - 1),
        outside_any=outside if outside is not None else np.zeros(m, bool),
        ref_index=0,
    )


def test_increments_and_valid():
    s = _series([[10, 11, 11.4, 11.5],
                [np.nan, 2, 3, 4],
                [5, 6, 7, 8]])
    inc = s.increments()
    assert inc.shape == (3, 3)
    assert np.allclose(inc[0], [1.0, 0.4, 0.1])
    assert list(s.valid) == [True, False, True]


def test_divergence_indicator_converging_vs_growing():
    s = _series([[10, 11, 11.4, 11.5],     # decel -> ratio 0.1/0.4 = 0.25
                 [10, 11, 13, 17]])        # accel -> ratio 4/2 = 2.0
    r = s.divergence_indicator()
    assert r[0] == pytest.approx(0.25)
    assert r[1] == pytest.approx(2.0)


def test_divergence_indicator_needs_three_levels():
    s = _series([[10, 12], [10, 11]])
    assert np.isnan(s.divergence_indicator()).all()


def test_summary_uses_only_valid_points():
    s = _series([[10, 11, 12, 13],
                 [np.nan, 1, 1, 99]],
                sizes=[5e-3, 4e-3, 3e-3, 2.5e-3])
    summ = s.summary()
    assert summ["n_valid"] == 1
    assert summ["levels"][3]["peak"] == pytest.approx(13.0)  # the nan row excluded
    assert summ["levels"][0]["size_m"] == pytest.approx(5e-3)


def test_write_csv_subsamples(tmp_path):
    m = _series(RNG.random((400, 4)) + 5.0)
    p = tmp_path / "cm.csv"
    m.write_csv(str(p), max_rows=50)
    rows = list(csv.reader(open(p, encoding="utf-8")))
    assert rows[0][:3] == ["x", "y", "z"]
    assert 2 <= len(rows) - 1 <= 60
    assert rows[0][-1] == "div_ratio"


def test_save_npz_roundtrip(tmp_path):
    s = _series([[10, 11, 12], [10.5, 11.5, 12.5]], sizes=[5e-3, 4e-3, 3e-3])
    p = tmp_path / "s.npz"
    s.save_npz(str(p))
    d = np.load(p, allow_pickle=True)
    assert d["matrix"].shape == (2, 3)
    assert int(d["ref_index"]) == 0
    assert np.allclose(d["sizes_m"], [5e-3, 4e-3, 3e-3])


class _FakeLevel:
    def __init__(self, coords, vm):
        self.coords = np.asarray(coords, float)
        self.von_mises = np.asarray(vm, float)
        self.unit = "MPa"
        self.length_unit = "mm"
        self._dpf_field = None

    @property
    def n_nodes(self):
        return self.coords.shape[0]

    def point_field(self):
        return field_mapping.PointField(self.coords, self.von_mises, name="von_mises")


def test_build_series_picks_finest_and_maps(monkeypatch):
    # analytic linear field so mapping is exact
    def f(c):
        return 4.0 * c[:, 0] - c[:, 1] + 2.0

    # coarse cloud strictly encloses the fine one so (almost) every fine point
    # is inside the coarse hull -> linear interpolation is exact there.
    coarse = _FakeLevel(RNG.random((400, 3)), None)         # [0,1]^3
    coarse.von_mises = f(coarse.coords)
    fine = _FakeLevel(0.3 + 0.4 * RNG.random((500, 3)), None)  # [0.3,0.7]^3
    fine.von_mises = f(fine.coords)

    levels = {"c.rst": coarse, "f.rst": fine}
    monkeypatch.setattr(dpf_adapter, "read_von_mises_nodal", lambda p: levels[Path(p).name])
    # force the scipy path (no server in unit tests)
    monkeypatch.setattr(cross_mesh.dpf_adapter, "map_level_on_coordinates",
                        lambda lf, tgt, prefer=None: dpf_adapter.MapReport(
                            "scipy_linear",
                            field_mapping.map_points_to_points(lf.point_field(), tgt).values,
                            1.0, np.zeros(len(tgt), bool), []))

    s = cross_mesh.build_series(["c.rst", "f.rst"])
    assert s.ref_index == 1                      # 'fine' has more nodes
    assert s.m == 500
    # column 1 is the identity (reference) column
    assert np.allclose(s.matrix[:, 1], fine.von_mises)
    # column 0: coarse mapped onto fine coords reproduces the linear field for
    # interior points to machine precision (a few hull-edge points may be nearest)
    err = np.abs(s.matrix[:, 0] - fine.von_mises)
    assert np.median(err) < 1e-9
    assert np.mean(err < 1e-9) > 0.9
    assert err.max() < 0.5
    assert s.valid.all()
