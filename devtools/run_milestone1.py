"""Milestone 1 orchestrator -- parametrised mesh/solve sweep.

    python devtools/run_milestone1.py --geometry test_models/bar.stp
    python devtools/run_milestone1.py --geometry test_models/bar.stp --h0 0.01 --refinements 3 --ratio 0.75
    python devtools/run_milestone1.py --geometry test_models/bar.stp --sizes 0.01,0.008,0.006,0.004

Detect Ansys -> licence preflight -> compute the size plan (devtools/mesh_plan)
-> PyMechanical embedded runs extension/mechanical_scripts/milestone1_sweep.py
-> read the per-level JSON -> print a convergence table -> exit code.

Exit: 0 = sweep PASS (or NO_GEOMETRY); 1 = ran but a level failed; 2 = infra.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import detect_ansys, licensing_preflight, mesh_plan, pymechanical_runner  # noqa: E402

MECH_SCRIPT = REPO / "extension" / "mechanical_scripts" / "milestone1_sweep.py"
EXT_DIR = REPO / "extension"


def _print_table(levels: list[dict]) -> None:
    hdr = "{0:>3}  {1:>12}  {2:>12}  {3:>9}  {4:>9}  {5:>14}  {6:>12}".format(
        "lvl", "req h [m]", "act h [m]", "nodes", "elems", "peak eqv", "max def")
    print(hdr)
    print("-" * len(hdr))
    for lv in levels:
        r = lv.get("results", {}) or {}
        pe = (r.get("peak_equivalent_stress") or {})
        md = (r.get("max_total_deformation") or {})
        act = lv.get("actual_char_size_m")
        print("{0:>3}  {1:>12.5g}  {2:>12}  {3:>9}  {4:>9}  {5:>14}  {6:>12}".format(
            lv.get("index", "?"),
            lv.get("requested_size_m", float("nan")),
            ("%.5g" % act) if isinstance(act, (int, float)) else "-",
            lv.get("nodes", "-"),
            lv.get("elements", "-"),
            ("%.4g %s" % (pe.get("value"), pe.get("unit", ""))) if pe else "-",
            ("%.4g %s" % (md.get("value"), md.get("unit", ""))) if md else "-",
        ))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--geometry", required=True)
    p.add_argument("--ansys-version", default="auto")
    p.add_argument("--h0", type=float, default=0.01, help="base element size, metres")
    p.add_argument("--refinements", type=int, default=mesh_plan.DEFAULT_REFINEMENTS)
    p.add_argument("--ratio", type=float, default=mesh_plan.DEFAULT_RATIO)
    p.add_argument("--sizes", default=None, help="explicit csv of sizes (metres), overrides h0/ratio")
    p.add_argument("--skip-preflight", action="store_true")
    args = p.parse_args(argv)

    geo = Path(args.geometry).resolve()
    if not geo.is_file():
        print("INFRA FAIL: geometry not found: {0}".format(geo), file=sys.stderr)
        return 2

    try:
        inst = detect_ansys.resolve(args.ansys_version)
    except RuntimeError as exc:
        print("INFRA FAIL: {0}".format(exc), file=sys.stderr)
        return 2

    if not args.skip_preflight:
        try:
            ok, lines = licensing_preflight.preflight(args.ansys_version, list(licensing_preflight.DEFAULT_NEEDED))
            if not ok:
                print("\n".join(lines))
                print("INFRA FAIL: licence preflight did not pass", file=sys.stderr)
                return 2
        except Exception as exc:  # noqa: BLE001
            print("WARN: licence preflight could not run ({0}); continuing".format(exc))

    if args.sizes:
        plan = mesh_plan.plan_from_env(args.h0, {"SD_ELEM_SIZES": args.sizes})
    else:
        plan = mesh_plan.global_size_plan(args.h0, refinements=args.refinements, ratio=args.ratio)

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    run_dir = REPO / "artifacts" / (stamp + "_milestone1_" + inst.version)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_meta.json").write_text(json.dumps({
        "milestone": 1, "ansys_version": inst.version, "geometry": str(geo),
        "plan": plan.as_dict(), "invoked": " ".join(sys.argv), "started": stamp,
    }, indent=2), encoding="utf-8")

    print("=" * 72)
    print("Milestone 1  --  Mesh/Solve sweep")
    print("=" * 72)
    print("Ansys     : {0} ({1})".format(inst.version, inst.release))
    print("Geometry  : {0}".format(geo))
    print("Plan      : {0}  sizes(m)={1}".format(plan.strategy, ["%.5g" % s for s in plan.sizes]))
    for w in plan.warnings:
        print("  ! {0}".format(w))
    print("Run dir   : {0}".format(run_dir))
    print("-" * 72)

    res = pymechanical_runner.run_script(
        str(MECH_SCRIPT), str(run_dir),
        version=inst.version, exec_file=inst.mechanical_exe,
        sentinel_name="milestone_result.json",
        extra_env={
            "SD_EXT_DIR": str(EXT_DIR),
            "SD_GEOMETRY": str(geo),
            "SD_ELEM_SIZES": ",".join(repr(s) for s in plan.sizes),
        },
    )
    print("backend   : {0}   ok={1}   {2}".format(res.strategy, res.ok, res.note))
    print("log       : {0}".format(res.log_path))
    if not res.ok:
        print("-" * 72)
        print("VERDICT: INFRASTRUCTURE FAILURE (no sentinel). See log + {0}".format(run_dir))
        return 2

    data = json.loads(Path(res.sentinel_path).read_text(encoding="utf-8"))
    (run_dir / "milestone_result.pretty.json").write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    status = data.get("status", "?")
    print("-" * 72)
    print("status    : {0}".format(status))
    levels = data.get("levels", [])
    if levels:
        _print_table(levels)
    series = data.get("peak_equivalent_stress_series")
    if series:
        print("\npeak eqv stress series : {0}".format(["%.4g" % v for v in series]))
    if data.get("last_two_increments"):
        d1, d2 = data["last_two_increments"]
        print("last two increments    : {0:.4g}, {1:.4g}   ratio={2}".format(
            d1, d2, data.get("increment_ratio")))
    if "traceback" in data:
        print("\nMechanical-side traceback:\n" + data["traceback"])

    print("=" * 72)
    if status in ("PASS", "NO_GEOMETRY"):
        print("VERDICT: MILESTONE 1 {0}".format(
            "PASS -- sweep + per-level results + mesh restore" if status == "PASS" else "(no geometry)"))
        return 0
    print("VERDICT: sweep ran but ended {0}. Evidence in {1}".format(status, run_dir))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
