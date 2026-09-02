"""Unit tests for the IronPython-side helper modules -- fakes for the engine,
no Ansys.  Covers mesh_manager.restore_from_snapshot, result_extractor naming +
remove(), and visualization.add_singularity_contours.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "extension"
sys.path.insert(0, str(EXT))

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
    assert [c[1] for c in an.calls] == ["EXT", "EXT", "EXT"]


def test_add_contours_falls_back_to_one_arg():
    viz = _load("visualization")
    an = _AnCreate(fail_two_arg=True)
    made = viz.add_singularity_contours(an, ext="EXT")
    assert made == list(viz._RESULT_NAMES)
    assert [c[1] for c in an.calls] == [None, None, None]      # 1-arg form used


def test_add_contours_raises_if_nothing_created():
    viz = _load("visualization")

    class _Bad(object):
        def CreateResultObject(self, *a, **k):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        viz.add_singularity_contours(_Bad(), ext=None)
