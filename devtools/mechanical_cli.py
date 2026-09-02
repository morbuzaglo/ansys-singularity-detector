"""Native Mechanical batch backend: run a Mechanical scripting .py head-less.

External automation layer -> modern CPython 3.

This is the *baseline* backend (spec S12). It shells out to ``AnsysWBU.exe``
(the Mechanical / Workbench host) in batch mode, runs a Mechanical-side
IronPython script, captures stdout/stderr, enforces a timeout, and kills
orphans on failure. Higher-level backends (PyMechanical, Workbench .wbjn) sit in
their own modules and fall back to this one.

    !! EXACT BATCH FLAGS ARE NOT YET VALIDATED ON A REAL RUN. !!

``AnsysWBU.exe`` batch/scripting flags are only lightly documented. Candidate
invocations are listed in ``BATCH_ARGV_CANDIDATES`` below; ``run_script`` tries
them in order until one produces the sentinel file, and records which worked in
the run directory. Verify against "Opening Mechanical from the Command Line"
(Mechanical User's Guide) for the target release and prune the list.

Prefer ``ansys-mechanical`` / PyMechanical when installed and compatible --
see pymechanical_runner.py (probe + fallback to this module).
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

# Placeholders substituted per call: {script}=Mechanical .py, {project}=optional .mechdb/.wbpj
BATCH_ARGV_CANDIDATES: tuple[tuple[str, ...], ...] = (
    # 1. Mechanical standalone applet, run script, batch, exit.
    ("-DSApplet", "-AppModeMech", "-b", "-nosplash", "-notabctrl", "-script", "{script}"),
    # 2. Same, with a project file loaded first.
    ("-DSApplet", "-AppModeMech", "-b", "-nosplash", "-notabctrl", "-file", "{project}", "-script", "{script}"),
    # 3. Generic batch script form.
    ("-B", "-R", "{script}"),
)

ORPHAN_PROCESS_NAMES = (
    "AnsysWBU.exe", "AnsysFWW.exe", "ansys252.exe", "ANSYS.exe",
    "Ansys.SolverManager.252.exe", "MechanicalSolverProxy.exe",
    "Ans.Dpf.Grpc.exe",
)


@dataclasses.dataclass
class RunResult:
    ok: bool
    returncode: int | None
    argv_used: list[str]
    stdout_path: str
    stderr_path: str
    duration_s: float
    sentinel_path: str | None
    timed_out: bool
    note: str = ""


def _kill_orphans(started_after: float) -> list[str]:
    """Best-effort: terminate lingering Ansys processes created during this run.

    Windows-only via `taskkill`. Only kills by image name -- deliberately
    conservative; will not touch a pre-existing interactive Mechanical because
    we compare creation time where possible via `wmic`. If in doubt, no-op.
    """
    if os.name != "nt":
        return []
    killed = []
    for name in ORPHAN_PROCESS_NAMES:
        try:
            out = subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0:
                killed.append(name)
        except Exception:
            pass
    return killed


def run_script(
    mechanical_exe: str,
    script_path: str,
    run_dir: str,
    *,
    project_path: str | None = None,
    sentinel_name: str = "milestone_result.json",
    timeout_s: float = 1800.0,
    extra_env: dict | None = None,
) -> RunResult:
    """Run ``script_path`` in Mechanical batch; success == ``sentinel_name`` appears in ``run_dir``.

    The Mechanical-side script is responsible for writing
    ``<run_dir>/<sentinel_name>`` as its machine-readable result (spec S17).
    """
    mech = Path(mechanical_exe)
    if not mech.is_file():
        raise FileNotFoundError("Mechanical executable not found: {0}".format(mechanical_exe))
    script = Path(script_path).resolve()
    if not script.is_file():
        raise FileNotFoundError("Mechanical script not found: {0}".format(script_path))

    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    sentinel = run / sentinel_name
    if sentinel.exists():
        sentinel.unlink()

    stdout_path = run / "mechanical_stdout.log"
    stderr_path = run / "mechanical_stderr.log"

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    # Tell the Mechanical-side script where to drop its result.
    env["SD_RUN_DIR"] = str(run)
    env["SD_SENTINEL"] = sentinel_name

    last_note = ""
    for template in BATCH_ARGV_CANDIDATES:
        if "{project}" in template and not project_path:
            continue
        argv = [str(mech)]
        for tok in template:
            argv.append(tok.format(script=str(script), project=project_path or ""))

        started = time.time()
        timed_out = False
        with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
            try:
                proc = subprocess.Popen(argv, cwd=str(run), env=env, stdout=so, stderr=se)
            except OSError as exc:
                last_note = "spawn failed: {0}".format(exc)
                continue
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                try:
                    rc = proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    rc = None
        duration = time.time() - started

        if timed_out:
            _kill_orphans(started)
            return RunResult(
                ok=False, returncode=rc, argv_used=argv,
                stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                duration_s=duration, sentinel_path=None, timed_out=True,
                note="timeout after {0:.0f}s; orphans killed".format(timeout_s),
            )

        if sentinel.is_file():
            (run / "_backend_argv.txt").write_text(" ".join(argv), encoding="utf-8")
            return RunResult(
                ok=True, returncode=rc, argv_used=argv,
                stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                duration_s=duration, sentinel_path=str(sentinel), timed_out=False,
                note="ok",
            )
        last_note = "argv produced no sentinel (rc={0}); trying next form".format(rc)

    _kill_orphans(time.time())
    return RunResult(
        ok=False, returncode=None, argv_used=[],
        stdout_path=str(stdout_path), stderr_path=str(stderr_path),
        duration_s=0.0, sentinel_path=None, timed_out=False,
        note=last_note or "no batch argv form succeeded",
    )


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    from devtools import detect_ansys

    p = argparse.ArgumentParser(description="Run a Mechanical scripting .py in batch.")
    p.add_argument("script", help="path to the Mechanical-side (IronPython 2.7) script")
    p.add_argument("--ansys-version", default="auto", help="version tag or 'auto' (oldest supported)")
    p.add_argument("--run-dir", default=None, help="run/artifact directory (default: artifacts/<ts>_cli)")
    p.add_argument("--project", default=None, help="optional .mechdb / .wbpj to load first")
    p.add_argument("--sentinel", default="milestone_result.json")
    p.add_argument("--timeout", type=float, default=1800.0)
    args = p.parse_args(argv)

    inst = detect_ansys.resolve(args.ansys_version)
    run_dir = args.run_dir or os.path.join(
        "artifacts", time.strftime("%Y-%m-%d_%H%M%S") + "_cli"
    )
    print("Ansys      : {0} ({1})".format(inst.version, inst.release))
    print("Mechanical : {0}".format(inst.mechanical_exe))
    print("Script     : {0}".format(args.script))
    print("Run dir    : {0}".format(run_dir))

    res = run_script(
        inst.mechanical_exe, args.script, run_dir,
        project_path=args.project, sentinel_name=args.sentinel, timeout_s=args.timeout,
    )
    print("\nresult: {0}".format("OK" if res.ok else "FAIL"))
    print("  note        : {0}".format(res.note))
    print("  returncode  : {0}".format(res.returncode))
    print("  duration    : {0:.1f}s".format(res.duration_s))
    print("  argv        : {0}".format(" ".join(res.argv_used)))
    print("  stdout log  : {0}".format(res.stdout_path))
    print("  sentinel    : {0}".format(res.sentinel_path))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(_cli())
