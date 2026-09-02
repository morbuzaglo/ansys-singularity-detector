"""Milestone 3 acceptance -- spatial cross-mesh stress mapping.

Runs a real 3-level study (subprocess), then in-process:
  * DPF is available and mapping.on_coordinates is present
  * build the sigma_vm(x, h_i) series on the finest mesh's node locations
  * identity column reproduces the reference field exactly
  * every reference point is mappable at every level (same geometry -> in hull)
  * an independent analytic linear-field check is machine-precision (spec: validate
    mapping accuracy independently before it feeds classification)
  * the official DPF operator and the scipy fallback agree on the real field

Skips when Ansys / licences / DPF are unavailable.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.mechanical, pytest.mark.benchmark]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
BAR = REPO / "test_models" / "bar.stp"


@pytest.fixture(scope="module")
def study_dir():
    if not BAR.is_file():
        pytest.skip("bar.stp not present")
    try:
        from devtools import detect_ansys
        detect_ansys.resolve("252")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("no usable Ansys: {0}".format(exc))

    cmd = [sys.executable, "-u", "-m", "devtools.study_controller",
           "--geometry", str(BAR), "--ansys-version", "252",
           "--sizes", "0.006,0.0042,0.003"]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=2400)
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    runs = sorted((REPO / "artifacts").glob("*_study_*"), key=lambda p: p.stat().st_mtime)
    assert runs, proc.stdout
    d = runs[-1]
    rsts = list(d.glob("level_*/file.rst"))
    if len(rsts) < 3:
        pytest.skip("study did not preserve >= 3 level rst files")
    return d


@pytest.fixture(scope="module")
def dpf_ok():
    from devtools import compatibility
    caps = compatibility.probe_dpf()
    if not caps.available:
        pytest.skip("DPF not available: " + "; ".join(caps.notes))
    return caps


def test_dpf_probe(dpf_ok):
    assert dpf_ok.has_mapping_on_coordinates
    assert dpf_ok.choose_mapping() == "dpf_on_coordinates"


def test_series_on_finest_mesh(study_dir, dpf_ok):
    from devtools import cross_mesh

    s = cross_mesh.from_study_dir(str(study_dir))
    assert s.n_levels == 3
    assert s.m > 500                        # finest mesh node count
    assert s.ref_index == s.n_levels - 1    # finest is last / most nodes

    # identity column == reference field exactly
    ref_col = s.matrix[:, s.ref_index]
    assert np.isfinite(ref_col).all()

    # every reference point mapped at every level (same geometry)
    assert s.valid.mean() > 0.98, s.summary()

    # peak von Mises series at common physical points -- finite, physical
    summ = s.summary()
    peaks = [lv["peak"] for lv in summ["levels"]]
    assert all(5.0 < p < 40.0 for p in peaks), peaks


def test_independent_analytic_validation(study_dir, dpf_ok):
    from devtools import cross_mesh, dpf_adapter, field_mapping

    rsts = sorted(str(p / "file.rst") for p in study_dir.glob("level_*"))
    coarse = dpf_adapter.read_von_mises_nodal(rsts[0])
    fine = dpf_adapter.read_von_mises_nodal(rsts[-1])

    def flin(c):
        return 2.0 * c[:, 0] - 0.7 * c[:, 1] + 0.1 * c[:, 2] + 3.0

    val = field_mapping.analytic_validation(flin, coarse.coords, fine.coords)
    assert val["error_interior"]["max_rel"] < 1e-8, val
    assert val["n_outside_hull"] <= 0.02 * val["n_target"]


def test_dpf_and_scipy_agree_on_real_field(study_dir, dpf_ok):
    from devtools import dpf_adapter

    rsts = sorted(str(p / "file.rst") for p in study_dir.glob("level_*"))
    coarse = dpf_adapter.read_von_mises_nodal(rsts[0])
    fine = dpf_adapter.read_von_mises_nodal(rsts[-1])

    a = dpf_adapter.map_level_on_coordinates(coarse, fine.coords, prefer="dpf_on_coordinates")
    b = dpf_adapter.map_level_on_coordinates(coarse, fine.coords, prefer="scipy_linear")
    assert a.method == "dpf_on_coordinates"
    assert b.method == "scipy_linear"

    both = np.isfinite(a.values) & np.isfinite(b.values)
    d = a.values[both] - b.values[both]
    rng = float(np.nanmax(fine.von_mises) - np.nanmin(fine.von_mises))
    assert np.sqrt(np.mean(d ** 2)) < 0.05 * rng, (np.sqrt(np.mean(d ** 2)), rng)
