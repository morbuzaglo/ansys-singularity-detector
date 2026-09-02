"""Unit tests for the IronPython-side helper modules -- fakes for the engine,
no Ansys.  Covers mesh_manager.restore_from_snapshot, result_extractor naming +
remove(), and visualization.add_singularity_contours.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "extension"
sys.path.insert(0, str(EXT))

# The IronPython modules `from System import Double` -- stub the .NET namespace so
# the CPython test process can import them.
if "System" not in sys.modules:
    _sysmod = types.ModuleType("System")

    class _Double(object):
        MaxValue = 1.7976931348623157e308

    _sysmod.Double = _Double
    sys.modules["System"] = _sysmod

import mech_env  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location("sd_" + name, EXT / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- #
# mesh_manager                                                               #
# --------------------------------------------------------------------------- #
class _FakeMesh(object):
    __slots__ = ("ElementSize", "ElementOrder", "GrowthRate",
                 "UseAdaptiveSizing", "_cleared")

    def __init__(self):
        self.ElementSize = "0 [mm]"
        self.ElementOrder = "ProgramControlled"
        self.GrowthRate = 1.2
        self.UseAdaptiveSizing = False
        self._cleared = 0

    def ClearGeneratedData(self):
        self._cleared += 1


class _FakeModel(object):
    def __init__(self):
        self.Mesh = _FakeMesh()


def test_snapshot_and_restore_from_snapshot():
    mech_env.bind({"Quantity": lambda s: ("Q", s)})     # fake Quantity ctor
    mm_mod = _load("mesh_manager")
    model = _FakeModel()
    model.Mesh.ElementSize = "5 [mm]"
    mm = mm_mod.MeshManager(model)

    snap = mm.snapshot()
    assert snap["ElementSize"] == "5 [mm]"              # stringified
    assert snap["UseAdaptiveSizing"] == "False"

    model.Mesh.ElementSize = "0.2 [mm]"                 # user refined it
    ok = mm.restore_from_snapshot(snap)
    assert ok is True
    assert model.Mesh.ElementSize == ("Q", "5 [mm]")   # re-applied via Quantity()
    assert model.Mesh.UseAdaptiveSizing is False        # bool round-trip
    assert model.Mesh._cleared >= 1                     # generated mesh cleared


def test_restore_from_snapshot_is_best_effort():
    mech_env.bind({"Quantity": lambda s: s})
    mm_mod = _load("mesh_manager")
    mm = mm_mod.MeshManager(_FakeModel())
    # an attr the fake mesh doesn't have -> best-effort, still returns (ok False),
    # still clears
    ok = mm.restore_from_snapshot({"NoSuchAttr": "x", "ElementSize": "1 [mm]"})
    assert ok is False


# --------------------------------------------------------------------------- #
# result_extractor                                                           #
# --------------------------------------------------------------------------- #
class _Res(object):
    def __init__(self):
        self.Name = ""
        self.deleted = False

    def Delete(self):
        self.deleted = True


class _Sol(object):
    def __init__(self):
        self.made = []

    def _mk(self):
        r = _Res()
        self.made.append(r)
        return r

    def __getattr__(self, n):
        if n.startswith("Add"):
            return self._mk
        raise AttributeError(n)


class _An(object):
    def __init__(self):
        self.Solution = _Sol()


def test_result_objects_are_SD_prefixed_and_removable():
    re_mod = _load("result_extractor")
    an = _An()
    rs = re_mod.ResultSet(an, fixed_support=None)
    names = sorted(r.Name for r in an.Solution.made)
    assert all(n.startswith("SD_") for n in names), names
    assert "SD_EquivalentStress" in names and "SD_TotalDeformation" in names

    rs.remove()
    assert all(r.deleted for r in an.Solution.made)
    assert rs.eqv is None


# --------------------------------------------------------------------------- #
# visualization.add_singularity_contours                                     #
# --------------------------------------------------------------------------- #
class _AnCreate(object):
    def __init__(self, fail_two_arg=False):
        self.calls = []
        self.fail_two_arg = fail_two_arg

    def CreateResultObject(self, name, ext=None):
        if ext is not None and self.fail_two_arg:
            raise RuntimeError("2-arg not supported")
        self.calls.append((name, ext))


def test_add_contours_with_explicit_ext():
    viz = _load("visualization")
    an = _AnCreate()
    made = viz.add_singularity_contours(an, ext="EXT")
    assert made == list(viz._RESULT_NAMES)
    assert [c[1] for c in an.calls] == ["EXT"] * len(viz._RESULT_NAMES)


def test_add_contours_subset_by_name():
    viz = _load("visualization")
    an = _AnCreate()
    made = viz.add_singularity_contours(an, ext="EXT",
                                        names=["Mesh Divergence Evidence"])
    assert made == ["Mesh Divergence Evidence"]


def test_add_contours_falls_back_to_one_arg():
    viz = _load("visualization")
    an = _AnCreate(fail_two_arg=True)
    made = viz.add_singularity_contours(an, ext="EXT")
    assert made == list(viz._RESULT_NAMES)
    assert [c[1] for c in an.calls] == [None] * len(viz._RESULT_NAMES)  # 1-arg form


def test_add_contours_raises_if_nothing_created():
    viz = _load("visualization")

    class _Bad(object):
        def CreateResultObject(self, *a, **k):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        viz.add_singularity_contours(_Bad(), ext=None)


class _Named(object):
    def __init__(self, name):
        self.Name = name
        self.deleted = False

    def Delete(self):
        self.deleted = True


class _AnRemove(object):
    class Solution(object):
        pass

    def __init__(self, names):
        self.Solution = type("S", (), {})()
        self._kids = [_Named(n) for n in names]
        self.Solution.Children = self._kids


def test_remove_singularity_contours_deletes_only_ours():
    viz = _load("visualization")
    ours = list(viz._RESULT_NAMES)
    an = _AnRemove([ours[0], "User Equivalent Stress", ours[2],
                    "Total Deformation", ours[-1]])
    removed = viz.remove_singularity_contours(an)
    assert sorted(removed) == sorted([ours[0], ours[2], ours[-1]])
    deleted = [k.Name for k in an._kids if k.deleted]
    assert sorted(deleted) == sorted([ours[0], ours[2], ours[-1]])
    assert not [k for k in an._kids if k.Name == "Total Deformation"][0].deleted


class _Coll(object):
    def __init__(self, ids):
        self.Ids = ids
        self.set = {}

    def SetValues(self, nid, vals):
        self.set[nid] = vals


class _ResNoCsv(object):
    class Analysis(object):
        WorkingDir = "Z:/definitely/not/here"
        MeshData = None


def test_fill_collector_no_csv_fills_sentinel_and_does_not_raise(monkeypatch):
    viz = _load("visualization")
    monkeypatch.setattr(viz, "_MISSING_LOGGED", [False])
    coll = _Coll([1, 2, 3])
    r = _ResNoCsv()
    r.Analysis = _ResNoCsv.Analysis()
    # must NOT raise even though the file is absent
    viz.fill_collector(r, coll, "raw_stress")
    assert set(coll.set) == {1, 2, 3}
    # every value is the .NET Double.MaxValue sentinel
    import System
    assert all(v == [System.Double.MaxValue] for v in coll.set.values())


class _MeshXY(object):
    def __init__(self, pts):
        self._pts = pts

    def NodeById(self, nid):
        x, y, z = self._pts[nid]
        return type("N", (), {"X": x, "Y": y, "Z": z})()


def test_fill_collector_masks_confidence_below_threshold(tmp_path, monkeypatch):
    import System
    viz = _load("visualization")
    monkeypatch.setattr(viz, "_MISSING_LOGGED", [False])

    csv_p = tmp_path / "contour_fields.csv"
    csv_p.write_text(
        "x,y,z,raw_stress,confidence_pct,filtered_stress,recovery_status\n"
        "0,0,0,10,20,10,raw\n"
        "1,0,0,10,85,10,raw\n", encoding="utf-8")
    (tmp_path / "contour_meta.json").write_text('{"threshold": 70.0}', encoding="utf-8")

    class _An(object):
        WorkingDir = None
        MeshData = _MeshXY({1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0)})

    class _R(object):
        Analysis = _An()

    viz._CACHE.clear()
    viz._META_CACHE.clear()
    # mask_below="threshold" -> resolved from contour_meta.json (70)
    coll = _Coll([1, 2])
    viz.fill_collector(_R(), coll, "confidence_pct", csv_path=str(csv_p),
                       mask_below="threshold")
    assert coll.set[1] == [System.Double.MaxValue]        # 20 < 70 -> hidden
    assert coll.set[2] == [pytest.approx(85.0)]           # 85 >= 70 -> shown

    # no mask_below -> full field, every node coloured
    viz._CACHE.clear()
    coll_full = _Coll([1, 2])
    viz.fill_collector(_R(), coll_full, "confidence_pct", csv_path=str(csv_p))
    assert coll_full.set[1] == [pytest.approx(20.0)]
    assert coll_full.set[2] == [pytest.approx(85.0)]

    # explicit numeric mask_below
    viz._CACHE.clear()
    coll_n = _Coll([1, 2])
    viz.fill_collector(_R(), coll_n, "confidence_pct", csv_path=str(csv_p),
                       mask_below=50.0)
    assert coll_n.set[1] == [System.Double.MaxValue]
    assert coll_n.set[2] == [pytest.approx(85.0)]


def test_visualization_exposes_all_result_callbacks():
    viz = _load("visualization")
    assert len(viz._RESULT_NAMES) == 7
    for cb in ("evaluate_raw_stress", "evaluate_singularity_confidence",
               "evaluate_singularity_confidence_flagged",
               "evaluate_singularity_filtered_stress", "evaluate_mesh_divergence",
               "evaluate_lambda_local", "evaluate_geometry_prior"):
        assert callable(getattr(viz, cb))
    # per-criterion columns the callbacks read
    assert {"mesh_divergence", "lambda_local", "geometry_prior"} <= set(viz._Field.COLS)
