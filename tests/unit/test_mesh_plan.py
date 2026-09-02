"""Unit tests for devtools.mesh_plan -- pure, no Ansys."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import mesh_plan  # noqa: E402


def test_default_plan_shape():
    p = mesh_plan.global_size_plan(0.01)
    assert p.strategy == "global_element_size"
    assert len(p.levels) == 4                     # h0 + 3 refinements
    assert p.sizes[0] == pytest.approx(0.01)
    assert p.sizes[1] == pytest.approx(0.0075)
    assert p.sizes[2] == pytest.approx(0.005625)
    assert p.sizes[3] == pytest.approx(0.00421875)
    assert p.warnings == []
    # strictly decreasing
    assert all(p.sizes[i] > p.sizes[i + 1] for i in range(3))


def test_custom_refinements_and_ratio():
    p = mesh_plan.global_size_plan(1.0, refinements=5, ratio=0.8)
    assert len(p.levels) == 6
    assert p.sizes[-1] == pytest.approx(0.8 ** 5)


def test_warn_ratio_too_high():
    p = mesh_plan.global_size_plan(1.0, ratio=0.88)
    assert any("too similar" in w for w in p.warnings)


def test_warn_ratio_too_low_for_global_3d():
    p = mesh_plan.global_size_plan(1.0, ratio=0.55, is_global_3d=True)
    assert any("grows very rapidly" in w for w in p.warnings)
    # ...but not when it's not a global 3D refinement
    p2 = mesh_plan.global_size_plan(1.0, ratio=0.55, is_global_3d=False)
    assert p2.warnings == []


@pytest.mark.parametrize("bad_ratio", [0.4, 0.95, 1.0, 0.0])
def test_ratio_out_of_range_rejected(bad_ratio):
    with pytest.raises(ValueError):
        mesh_plan.global_size_plan(1.0, ratio=bad_ratio)


@pytest.mark.parametrize("bad_h0", [0.0, -1.0])
def test_bad_h0_rejected(bad_h0):
    with pytest.raises(ValueError):
        mesh_plan.global_size_plan(bad_h0)


def test_too_few_refinements_rejected():
    with pytest.raises(ValueError):
        mesh_plan.global_size_plan(1.0, refinements=0)


def test_plan_from_env_explicit_sizes():
    p = mesh_plan.plan_from_env(0.01, {"SD_ELEM_SIZES": "0.02, 0.01, 0.005"})
    assert p.strategy == "explicit_sizes"
    assert p.sizes == [0.02, 0.01, 0.005]
    assert p.warnings == []


def test_plan_from_env_explicit_non_monotone_warns():
    p = mesh_plan.plan_from_env(0.01, {"SD_ELEM_SIZES": "0.01,0.02"})
    assert any("not monoton" in w for w in p.warnings)


def test_plan_from_env_overrides():
    p = mesh_plan.plan_from_env(2.0, {"SD_MESH_REFINEMENTS": "2", "SD_MESH_RATIO": "0.7"})
    assert len(p.levels) == 3
    assert p.ratio == pytest.approx(0.7)


def test_plan_from_env_defaults():
    p = mesh_plan.plan_from_env(0.01, {})
    assert len(p.levels) == 4
    assert p.ratio == pytest.approx(mesh_plan.DEFAULT_RATIO)


def test_as_dict_roundtrip_keys():
    d = mesh_plan.global_size_plan(0.01).as_dict()
    assert set(d) == {"strategy", "ratio", "sizes", "warnings"}
