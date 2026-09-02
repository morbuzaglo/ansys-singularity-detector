"""PyMechanical backend: run a Mechanical-side script via ansys-mechanical-core.

External automation layer -> CPython 3.

Preferred over the raw ``AnsysWBU.exe`` batch backend (mechanical_cli.py) when
``ansys-mechanical-core`` imports and a compatible Mechanical is installed --
avoids fragile command-line arg quoting (our repo path has spaces) and gives a
clean, in-process, head-less run.

Strategy order (each is capability-probed; falls through on failure):
  1. EMBEDDED   -- ``ansys.mechanical.core.App`` (in-process, pythonnet). Fastest,
                   no port, no extra process. Needs a matching embedding build.
  2. LAUNCH     -- ``launch_mechanical(batch=True)`` (gRPC server subprocess).
  3. (caller's responsibility) fall back to mechanical_cli.run_script.

Success contract is identical to mechanical_cli: the Mechanical-side script must
write ``<run_dir>/<sentinel_name>`` as JSON.  We set ``SD_RUN_DIR`` /
``SD_SENTINEL`` in the environment first.
"""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from pathlib import Path


@dataclasses.dataclass
class PyMechResult:
    ok: bool
    strategy: str                 # "embedded" | "launch" | "none"
    sentinel_path: str | None
    duration_s: float
    log_path: str
    note: str = ""


def available() -> bool:
    try:
        import ansys.mechanical.core  # noqa: F401
        return True
    except Exception:
        return False


def _prep_env(run_dir: Path, sentinel_name: str, extra_env: dict | None) -> None:
    os.environ["SD_RUN_DIR"] = str(run_dir)
    os.environ["SD_SENTINEL"] = sentinel_name
    if extra_env:
        for k, v in extra_env.items():
            os.environ[str(k)] = str(v)


def _get_or_make_embedded_app(version: str, log):
    """Return (app, created_here). Embedded Mechanical is one-per-process, so if
    an instance already exists (e.g. an earlier test in the same pytest run),
    reuse it and reset the project with .new()."""
    from ansys.mechanical.core import App
    from ansys.mechanical.core.embedding import app as _appmod

    try:
        try:
            app = App(version=int(version))
        except TypeError:
            app = App()
        return app, True
    except RuntimeError as exc:
        if "more than one embedded" not in str(exc):
            raise
        existing = list(getattr(_appmod, "_INSTANCES", []) or [])
        if not existing:
            raise
        app = existing[0]
        log.write("[embedded] reusing existing in-process App; calling .new()\n")
        try:
            app.new()
        except Exception as e:  # noqa: BLE001
            log.write("[embedded] app.new() warn: {0}\n".format(e))
        return app, False


def _try_embedded(script: Path, run_dir: Path, sentinel: Path, version: str, log) -> bool:
    log.write("[embedded] constructing App(version={0})\n".format(version))
    app, created_here = _get_or_make_embedded_app(version, log)
    try:
        log.write("[embedded] App up: {0} (created_here={1})\n".format(
            getattr(app, "version", "?"), created_here))
        # Make Mechanical scripting globals (Model, ExtAPI, Quantity, ...) resolvable
        # inside the executed script.
        try:
            app.update_globals(globals())
        except Exception as exc:  # noqa: BLE001
            log.write("[embedded] update_globals warn: {0}\n".format(exc))
        app.execute_script_from_file(str(script))
        log.write("[embedded] execute_script_from_file returned\n")
        return sentinel.is_file()
    finally:
        # Embedded Mechanical is one-per-process and cannot be re-initialised
        # after close(), so we deliberately leave it running for the lifetime of
        # the process. `run_milestone0.py` exits right after; pytest reuses it via
        # _get_or_make_embedded_app (with .new() to reset the project).
        _ = (app, created_here)


def _try_launch(script: Path, run_dir: Path, sentinel: Path, exec_file: str | None, log) -> bool:
    from ansys.mechanical.core import launch_mechanical

    log.write("[launch] launch_mechanical(batch=True, exec_file={0})\n".format(exec_file))
    mech = launch_mechanical(exec_file=exec_file, batch=True, cleanup_on_exit=True)
    try:
        out = mech.run_python_script_from_file(str(script))
        log.write("[launch] script output:\n{0}\n".format(out))
        return sentinel.is_file()
    finally:
        try:
            mech.exit(force=True)
        except Exception:
            pass


def run_script(
    script_path: str,
    run_dir: str,
    *,
    version: str = "252",
    exec_file: str | None = None,
    sentinel_name: str = "milestone_result.json",
    extra_env: dict | None = None,
    strategies: tuple[str, ...] = ("embedded",),
) -> PyMechResult:
    # NOTE: "launch" (gRPC) is intentionally NOT in the default list -- the local
    # 2025 R2 build lacks SP03, so `launch_mechanical` raises
    # "does not support secure transport modes" (spec S15). Pass
    # strategies=("embedded", "launch") explicitly on an SP03+ / 261 machine.
    script = Path(script_path).resolve()
    if not script.is_file():
        raise FileNotFoundError("Mechanical script not found: {0}".format(script_path))
    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    sentinel = run / sentinel_name
    if sentinel.exists():
        sentinel.unlink()
    log_path = run / "pymechanical_runner.log"

    if not available():
        return PyMechResult(False, "none", None, 0.0, str(log_path),
                            "ansys-mechanical-core not importable")

    _prep_env(run, sentinel_name, extra_env)

    with open(log_path, "w", encoding="utf-8") as log:
        for strat in strategies:
            log.write("\n===== strategy: {0} =====\n".format(strat))
            started = time.time()
            try:
                if strat == "embedded":
                    ok = _try_embedded(script, run, sentinel, version, log)
                elif strat == "launch":
                    ok = _try_launch(script, run, sentinel, exec_file, log)
                else:
                    log.write("unknown strategy, skipping\n")
                    continue
            except Exception:
                log.write("strategy {0} raised:\n{1}\n".format(strat, traceback.format_exc()))
                ok = False
            dur = time.time() - started
            if ok and sentinel.is_file():
                log.write("strategy {0} OK in {1:.1f}s\n".format(strat, dur))
                return PyMechResult(True, strat, str(sentinel), dur, str(log_path), "ok")
            log.write("strategy {0} did not produce the sentinel ({1:.1f}s)\n".format(strat, dur))

    return PyMechResult(False, "none", None, 0.0, str(log_path),
                        "no PyMechanical strategy produced the sentinel")


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    p = argparse.ArgumentParser(description="Run a Mechanical script via PyMechanical.")
    p.add_argument("script")
    p.add_argument("--run-dir", default=os.path.join("artifacts", time.strftime("%Y-%m-%d_%H%M%S") + "_pymech"))
    p.add_argument("--version", default="252")
    p.add_argument("--sentinel", default="milestone_result.json")
    args = p.parse_args()

    print("ansys-mechanical-core available:", available())
    res = run_script(args.script, args.run_dir, version=args.version, sentinel_name=args.sentinel)
    print("strategy :", res.strategy)
    print("ok       :", res.ok)
    print("sentinel :", res.sentinel_path)
    print("log      :", res.log_path)
    print("note     :", res.note)
    raise SystemExit(0 if res.ok else 1)
