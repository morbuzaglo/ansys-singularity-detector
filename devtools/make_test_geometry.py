"""Generate benchmark geometry fixtures headlessly via SpaceClaim scripting.

External automation layer -> CPython 3.  The *generated* script that SpaceClaim
runs is SpaceClaim-IronPython, embedded here as a string.

    python devtools/make_test_geometry.py bar          # -> test_models/bar.stp
    python devtools/make_test_geometry.py --list

Fixtures are exported as STEP (.stp) -- portable across Ansys releases -- and
land in test_models/ (spec S41).  A one-time generation; commit the .stp.

    !! SpaceClaim headless flags + geometry API calls are NEEDS VALIDATION until
       this has produced a file once.  See docs/milestones.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from devtools import detect_ansys  # noqa: E402

TEST_MODELS = REPO / "test_models"

# name -> (description, SpaceClaim script body). Script must end by exporting to
# the path in the `OUT` variable (injected as a raw string at the top).
FIXTURES: dict[str, tuple[str, str]] = {
    "bar": (
        "Plain 100 x 10 x 10 mm rectangular bar. Fixed min-X face, axial load max-X face.",
        r'''
# --- SpaceClaim IronPython ---
ClearAll()
# Block between two opposite corners, in metres.
Block.Create(Point.Create(0.0, 0.0, 0.0), Point.Create(0.100, 0.010, 0.010))
# Export STEP (format inferred from extension).
DocumentSave.Execute(OUT)
''',
    ),
    "plate_with_hole": (
        "200 x 100 x 10 mm plate, central 20 mm-dia through hole. Expect convergence.",
        r'''
ClearAll()
Block.Create(Point.Create(0.0, 0.0, 0.0), Point.Create(0.200, 0.100, 0.010))
# Cylinder to subtract (through the thickness, centred).
cyl = CylinderBody.Create(Point.Create(0.100, 0.050, -0.005),
                          Direction.Create(0.0, 0.0, 1.0), 0.010, 0.020)
# Boolean subtract: newest body is the tool.
try:
    Combine.Merge(GetRootPart().Bodies[0], [GetRootPart().Bodies[1]], False)
except:
    pass
DocumentSave.Execute(OUT)
''',
    ),
}


def _spaceclaim_exe(version: str) -> str:
    try:
        inst = detect_ansys.resolve(version)
        root = Path(inst.root)
    except RuntimeError:
        root = None
    candidates = []
    if root:
        candidates += [root / "scdm" / "SpaceClaim.exe"]
    for env in ("AWP_ROOT252", "AWP_ROOT251", "AWP_ROOT261"):
        v = os.environ.get(env)
        if v:
            candidates.append(Path(v) / "scdm" / "SpaceClaim.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError("SpaceClaim.exe not found under any Ansys install root")


def build(name: str, version: str = "auto", timeout_s: float = 600.0) -> Path:
    if name not in FIXTURES:
        raise KeyError("unknown fixture {0!r}; known: {1}".format(name, ", ".join(sorted(FIXTURES))))
    desc, body = FIXTURES[name]
    TEST_MODELS.mkdir(parents=True, exist_ok=True)
    out_path = TEST_MODELS / (name + ".stp")

    scexe = _spaceclaim_exe(version)
    script_dir = Path(tempfile.mkdtemp(prefix="sc_geom_"))
    script_file = script_dir / (name + "_gen.py")
    header = 'OUT = r"{0}"\n'.format(str(out_path))
    script_file.write_text(header + body, encoding="utf-8")

    log_file = script_dir / "spaceclaim.log"
    argv = [
        scexe,
        "/Headless=True", "/Splash=False", "/Welcome=False",
        "/ExitAfterScript=True",
        "/ScriptAPI=V232",
        "/RunScript=" + str(script_file),
        "/LogFile=" + str(log_file),
    ]
    print("fixture   : {0}  ({1})".format(name, desc))
    print("SpaceClaim: {0}".format(scexe))
    print("script    : {0}".format(script_file))
    print("out       : {0}".format(out_path))

    if out_path.exists():
        out_path.unlink()
    started = time.time()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    dur = time.time() - started
    (script_dir / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (script_dir / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")

    if out_path.is_file() and out_path.stat().st_size > 0:
        print("OK        : {0} bytes in {1:.1f}s".format(out_path.stat().st_size, dur))
        sidecar = out_path.with_suffix(".stp.md")
        sidecar.write_text(
            "# {0}\n\n{1}\n\n- generator: `devtools/make_test_geometry.py {0}`\n"
            "- SpaceClaim: `{2}`\n- units: metres in the STEP\n".format(name, desc, scexe),
            encoding="utf-8",
        )
        return out_path

    raise RuntimeError(
        "SpaceClaim produced no STEP (rc={0}, {1:.1f}s). Logs in {2}\nstderr:\n{3}".format(
            proc.returncode, dur, script_dir, (proc.stderr or "")[-2000:]
        )
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fixture", nargs="?", help="fixture name (see --list)")
    p.add_argument("--list", action="store_true", help="list known fixtures")
    p.add_argument("--ansys-version", default="auto")
    p.add_argument("--timeout", type=float, default=600.0)
    args = p.parse_args(argv)

    if args.list or not args.fixture:
        for k, (desc, _) in sorted(FIXTURES.items()):
            marker = "  [built]" if (TEST_MODELS / (k + ".stp")).is_file() else ""
            print("{0:18s} {1}{2}".format(k, desc, marker))
        return 0

    try:
        build(args.fixture, args.ansys_version, args.timeout)
    except (KeyError, FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("FAILED: {0}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
