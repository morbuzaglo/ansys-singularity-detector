"""Milestone 0 orchestrator -- prove the autonomous Ansys loop.

    python devtools/run_milestone0.py [--ansys-version auto|252|...] [--geometry path]

Does, with no human in Mechanical:
  1. detect + resolve an Ansys install
  2. create a timestamped artifact run directory
  3. launch Mechanical batch, run extension/mechanical_scripts/milestone0_hello.py
  4. read back the machine-readable JSON sentinel
  5. print a verdict, keep all logs, return an exit code

Exit codes: 0 = sentinel says PASS or NO_GEOMETRY (plumbing proven);
            1 = ran but model/solve failed; 2 = infrastructure/launch failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import detect_ansys, mechanical_cli, pymechanical_runner  # noqa: E402

MECH_SCRIPT = REPO / "extension" / "mechanical_scripts" / "milestone0_hello.py"
PLUMBING_OK_STATUSES = {"PASS", "NO_GEOMETRY"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ansys-version", default="auto", help="version tag or 'auto' (oldest supported)")
    p.add_argument("--geometry", default=os.environ.get("SD_GEOMETRY"),
                   help="optional geometry fixture (.step/.stp/.x_t/.scdoc/.agdb)")
    p.add_argument("--element-size", type=float, default=0.01, help="global element size in metres")
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument("--backend", choices=["auto", "pymechanical", "native"], default="auto",
                   help="auto = try PyMechanical then native AnsysWBU batch")
    args = p.parse_args(argv)

    try:
        inst = detect_ansys.resolve(args.ansys_version)
    except RuntimeError as exc:
        print("INFRA FAIL: {0}".format(exc), file=sys.stderr)
        return 2

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    run_dir = REPO / "artifacts" / (stamp + "_milestone0_" + inst.version)
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "milestone": 0,
        "invoked": " ".join(sys.argv),
        "ansys_version": inst.version,
        "ansys_release": inst.release,
        "mechanical_exe": inst.mechanical_exe,
        "mech_script": str(MECH_SCRIPT),
        "geometry": args.geometry,
        "element_size_m": args.element_size,
        "started": stamp,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("=" * 68)
    print("Milestone 0  --  Autonomous Ansys Runner")
    print("=" * 68)
    print("Ansys      : {0} ({1})   [primary target 251 {2}]".format(
        inst.version, inst.release,
        "PRESENT" if inst.version == detect_ansys.PRIMARY_VERSION else "ABSENT -- see docs/ansys_environment.md"))
    print("Mechanical : {0}".format(inst.mechanical_exe))
    print("Mech script: {0}".format(MECH_SCRIPT))
    print("Geometry   : {0}".format(args.geometry or "(none -- plumbing-only run)"))
    print("Run dir    : {0}".format(run_dir))
    print("-" * 68)

    if not MECH_SCRIPT.is_file():
        print("INFRA FAIL: missing {0}".format(MECH_SCRIPT), file=sys.stderr)
        return 2

    extra_env = {"SD_ELEM_SIZE": str(args.element_size)}
    if args.geometry:
        extra_env["SD_GEOMETRY"] = str(Path(args.geometry).resolve())

    sentinel_path = None
    tried = []

    want_pymech = args.backend in ("auto", "pymechanical")
    if want_pymech and pymechanical_runner.available():
        print("backend     : PyMechanical (embedded -> launch)")
        pres = pymechanical_runner.run_script(
            str(MECH_SCRIPT), str(run_dir),
            version=inst.version, exec_file=inst.mechanical_exe,
            sentinel_name="milestone_result.json", extra_env=extra_env,
        )
        tried.append("pymechanical:{0}".format(pres.strategy))
        print("  strategy  : {0}   ok={1}   {2}".format(pres.strategy, pres.ok, pres.note))
        print("  log       : {0}".format(pres.log_path))
        if pres.ok:
            sentinel_path = pres.sentinel_path
    elif want_pymech:
        print("backend     : PyMechanical requested/auto but ansys-mechanical-core not importable")

    if sentinel_path is None and args.backend in ("auto", "native"):
        print("backend     : native AnsysWBU.exe batch")
        res = mechanical_cli.run_script(
            inst.mechanical_exe, str(MECH_SCRIPT), str(run_dir),
            sentinel_name="milestone_result.json",
            timeout_s=args.timeout, extra_env=extra_env,
        )
        tried.append("native")
        print("  launch note : {0}".format(res.note))
        print("  returncode  : {0}".format(res.returncode))
        print("  duration    : {0:.1f}s".format(res.duration_s))
        print("  argv        : {0}".format(" ".join(res.argv_used)))
        print("  stdout log  : {0}".format(res.stdout_path))
        print("  stderr log  : {0}".format(res.stderr_path))
        if res.ok:
            sentinel_path = res.sentinel_path

    if sentinel_path is None:
        print("-" * 68)
        print("VERDICT: INFRASTRUCTURE FAILURE -- no backend produced a result sentinel.")
        print("Backends tried: {0}".format(", ".join(tried) or "(none)"))
        print("Inspect the logs in {0}".format(run_dir))
        return 2

    sentinel = json.loads(Path(sentinel_path).read_text(encoding="utf-8"))
    (run_dir / "milestone_result.pretty.json").write_text(
        json.dumps(sentinel, indent=2, sort_keys=True), encoding="utf-8")

    status = sentinel.get("status", "?")
    print("-" * 68)
    print("sentinel status : {0}".format(status))
    for k in ("message", "mesh", "solve_status", "solve_seconds",
              "peak_equivalent_stress", "max_total_deformation", "ansys_version_reported"):
        if k in sentinel:
            print("  {0:22s}: {1}".format(k, sentinel[k]))
    if "traceback" in sentinel:
        print("  Mechanical-side traceback:\n" + sentinel["traceback"])

    print("=" * 68)
    if status in PLUMBING_OK_STATUSES:
        print("VERDICT: MILESTONE 0 PLUMBING PROVEN"
              + (" + mesh/solve/extract PASS" if status == "PASS" else " (no geometry -- supply a fixture next)"))
        return 0
    print("VERDICT: ran end-to-end but model stage failed ({0}). Logs kept in {1}".format(status, run_dir))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
