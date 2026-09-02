"""Shared pytest fixtures / lane gating.

Lanes (see pytest.ini markers):
  unit         -- always run; no Ansys.
  mechanical   -- needs a usable Ansys install + free licenses; launches Mechanical.
  regression   -- frozen metrics / fixed-bug guards.
  benchmark    -- slow full solves; only with --run-benchmarks.

`mechanical` and `benchmark` tests auto-skip when their prerequisites are absent,
so a plain `pytest` run is green on a machine with no Ansys.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def pytest_addoption(parser):
    parser.addoption("--run-benchmarks", action="store_true", default=False,
                     help="run the slow benchmark lane")
    parser.addoption("--ansys-version", action="store", default="auto",
                     help="Ansys version tag for the mechanical lane (default: auto)")


@pytest.fixture(scope="session")
def ansys_version(request):
    return request.config.getoption("--ansys-version")


@pytest.fixture(scope="session")
def ansys_install(ansys_version):
    from devtools import detect_ansys
    try:
        return detect_ansys.resolve(ansys_version)
    except RuntimeError as exc:
        pytest.skip("no usable Ansys install: {0}".format(exc))


@pytest.fixture(scope="session")
def license_ok(ansys_version):
    from devtools import licensing_preflight
    try:
        ok, _ = licensing_preflight.preflight(ansys_version, list(licensing_preflight.DEFAULT_NEEDED))
    except Exception as exc:  # noqa: BLE001
        pytest.skip("license preflight could not run: {0}".format(exc))
    if not ok:
        pytest.skip("required licenses not available")
    return True


@pytest.fixture(scope="session")
def pymechanical_available():
    from devtools import pymechanical_runner
    if not pymechanical_runner.available():
        pytest.skip("ansys-mechanical-core not installed")
    return True


def pytest_collection_modifyitems(config, items):
    run_bench = config.getoption("--run-benchmarks")
    skip_bench = pytest.mark.skip(reason="benchmark lane: pass --run-benchmarks to run")
    for item in items:
        # The mechanical / benchmark lanes launch Mechanical in a subprocess and
        # run several solves -- well past the default pytest-timeout. Give them
        # room (unit tests keep the short default from pytest.ini).
        if "mechanical" in item.keywords or "benchmark" in item.keywords:
            item.add_marker(pytest.mark.timeout(2400))
        if "benchmark" in item.keywords and not run_bench:
            item.add_marker(skip_bench)
