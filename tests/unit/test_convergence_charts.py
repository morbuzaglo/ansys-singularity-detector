"""Unit tests for devtools.convergence_charts -- global charts, no Ansys/DPF.

The per-region charts need the DPF cross-mesh series; they are covered by
tests/mechanical/test_milestone8.py.  Here we build a synthetic study_result.json
(no singularity_analysis.json) so only the global path runs.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import convergence_charts as ch  # noqa: E402


def _study(tmp_path, diverging=True):
    d = tmp_path / "study"
    d.mkdir()
    h = [0.005, 0.004, 0.003, 0.0022, 0.0016]
    ne = [200, 400, 900, 2000, 5000]
    if diverging:
        peak = [10.0, 11.5, 13.5, 16.0, 19.0]
    else:
        peak = [30.0, 29.2, 29.0, 28.95, 28.94]
    levels = []
    for i in range(5):
        levels.append({
            "index": i, "status": "ok",
            "actual_char_size_m": h[i], "elements": ne[i], "nodes": ne[i] * 8,
            "results": {
                "peak_equivalent_stress": {"value": peak[i], "unit": "MPa"},
                "max_total_deformation": {"value": 5.0 + 0.01 * i, "unit": "mm"},
                "reaction_at_fixed_support": {"value": 1000.0 + (i - 2) * 0.5, "unit": "N"},
                "strain_energy": None,
            },
        })
    (d / "study_result.json").write_text(json.dumps(
        {"force_newtons": 1000.0, "levels": levels}), encoding="utf-8")
    return d


def test_global_charts_and_csv_written(tmp_path):
    d = _study(tmp_path)
    idx = ch.build(str(d))
    charts = d / "charts"
    assert (charts / "charts_index.json").is_file()
    assert (charts / "global_convergence.csv").is_file()
    for name in ("global_peak_stress_vs_h.png", "global_peak_stress_vs_nelem.png",
                 "global_max_deformation_vs_h.png", "global_reaction_imbalance_vs_h.png"):
        assert (charts / name).is_file(), name
    assert idx["n_regions"] == 0            # no singularity_analysis.json


def test_global_csv_content_matches_levels(tmp_path):
    d = _study(tmp_path)
    ch.build(str(d))
    rows = list(csv.DictReader(open(d / "charts" / "global_convergence.csv", encoding="utf-8")))
    assert len(rows) == 5
    # coarse -> fine ordering
    hs = [float(r["char_elem_size_m"]) for r in rows]
    assert hs == sorted(hs, reverse=True)
    assert float(rows[0]["peak_eqv_stress"]) == pytest.approx(10.0)
    assert float(rows[-1]["peak_eqv_stress"]) == pytest.approx(19.0)
    assert int(float(rows[-1]["n_elements"])) == 5000


def test_strain_energy_chart_skipped_when_all_null(tmp_path):
    d = _study(tmp_path)
    ch.build(str(d))
    assert not (d / "charts" / "global_strain_energy_vs_h.png").is_file()


def test_reaction_imbalance_is_small_for_valid_solve(tmp_path):
    d = _study(tmp_path)
    ch.build(str(d))
    # imbalance column isn't in the CSV, but the chart exists and the raw
    # reaction is ~1000 -> imbalance < 1%
    rows = list(csv.DictReader(open(d / "charts" / "global_convergence.csv", encoding="utf-8")))
    reac = np.array([float(r["reaction_at_support"]) for r in rows])
    assert np.all(np.abs(reac - 1000.0) / 1000.0 < 0.01)
