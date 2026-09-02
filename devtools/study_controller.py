"""Convergence-study controller (Milestone 2) -- external CPython 3.

This is the object an ACT "Run Study" callback will delegate to; a test script
calls the same ``StudyController.run(...)`` directly (spec S3, S21).

    from devtools.study_controller import StudyConfig, StudyController
    cfg = StudyController.plan(geometry="test_models/bar.stp", h0=0.01)
    ctrl = StudyController(cfg)
    ok, lines = ctrl.preflight()
    result = ctrl.run()                 # launches Mechanical, runs the sweep
    ctrl.write_csv(result)
    result.classify_convergence()       # convergent / uncertain / diverging (coarse)
    ctrl.cleanup(result)               # remove level_XX/ once summarised (spec S49)

Failure policy (spec S50): a mesh/solve failure -> StudyResult.status is
MESH_FAILED / SOLVE_FAILED, .ok is False, and it is NEVER reported as a
singularity verdict.  An infrastructure failure (no sentinel) raises InfraError.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import detect_ansys, licensing_preflight, mesh_plan, pymechanical_runner  # noqa: E402

STUDY_RUNNER = REPO / "extension" / "study_runner.py"
EXT_DIR = REPO / "extension"


class InfraError(RuntimeError):
    """Ansys/launch/licence failure -- not a study result."""


@dataclasses.dataclass
class StudyConfig:
    geometry: str
    sizes: list[float]                 # explicit characteristic element sizes, metres, decreasing
    setup: str = "axial_bar"
    force_newtons: float = 1000.0
    ansys_version: str = "auto"
    force_n_env: bool = True
    out_dir: str | None = None         # default: artifacts/<ts>_study_<ver>
    keep_rst: bool = True              # keep per-level file.rst after summarising
    plan_meta: dict | None = None

    def size_str(self) -> str:
        return ",".join(repr(s) for s in self.sizes)


@dataclasses.dataclass
class LevelResult:
    index: int
    requested_size_m: float
    actual_char_size_m: float | None
    nodes: int
    elements: int
    status: str
    generate_seconds: float | None = None
    solve_seconds: float | None = None
    peak_eqv: dict | None = None
    max_def: dict | None = None
    reaction: dict | None = None
    result_path: str | None = None

    @classmethod
    def from_json(cls, lv: dict) -> "LevelResult":
        r = lv.get("results") or {}
        return cls(
            index=lv.get("index", -1),
            requested_size_m=lv.get("requested_size_m", float("nan")),
            actual_char_size_m=lv.get("actual_char_size_m"),
            nodes=lv.get("nodes", 0),
            elements=lv.get("elements", 0),
            status=lv.get("status", "?"),
            generate_seconds=lv.get("generate_seconds"),
            solve_seconds=(r.get("solve_seconds") or lv.get("solve_wall_seconds")),
            peak_eqv=r.get("peak_equivalent_stress"),
            max_def=r.get("max_total_deformation"),
            reaction=r.get("reaction_at_fixed_support"),
            result_path=lv.get("result_path"),
        )


@dataclasses.dataclass
class StudyResult:
    status: str
    ok: bool
    run_dir: str
    summary_path: str
    raw: dict
    levels: list[LevelResult]
    restore_ok: bool | None
    ansys_version_reported: str

    @property
    def peak_series(self) -> list[float]:
        return [lv.peak_eqv["value"] for lv in self.levels
                if lv.status == "ok" and lv.peak_eqv]

    def classify_convergence(self) -> dict:
        """Coarse, provisional read of the peak-stress trend.  NOT the real
        singularity classifier (Milestone 4) -- just enough to sanity-check M2.
        """
        s = self.peak_series
        if len(s) < 3:
            return {"verdict": "insufficient_levels", "n": len(s)}
        incs = [abs(s[i + 1] - s[i]) for i in range(len(s) - 1)]
        last, prev = incs[-1], incs[-2]
        ratio = (last / prev) if prev > 0 else None
        rel_last = last / abs(s[-1]) if s[-1] else None
        if ratio is not None and ratio < 0.8 and (rel_last is None or rel_last < 0.05):
            verdict = "convergent"
        elif ratio is not None and ratio > 1.1:
            verdict = "diverging"
        else:
            verdict = "uncertain"
        return {"verdict": verdict, "increments": incs, "increment_ratio": ratio,
                "relative_last_increment": rel_last, "peak_series": s}


class StudyController:
    def __init__(self, config: StudyConfig):
        self.cfg = config

    # ---- planning helpers ------------------------------------------------
    @staticmethod
    def plan(geometry: str, *, h0: float = 0.01,
             refinements: int = mesh_plan.DEFAULT_REFINEMENTS,
             ratio: float = mesh_plan.DEFAULT_RATIO,
             sizes: list[float] | None = None, **kw) -> StudyConfig:
        if sizes:
            p = mesh_plan.plan_from_env(h0, {"SD_ELEM_SIZES": ",".join(repr(s) for s in sizes)})
        else:
            p = mesh_plan.global_size_plan(h0, refinements=refinements, ratio=ratio)
        return StudyConfig(geometry=str(geometry), sizes=p.sizes, plan_meta=p.as_dict(), **kw)

    # ---- preflight ---------------------------------------------------
    def preflight(self) -> tuple[bool, list[str]]:
        if not Path(self.cfg.geometry).is_file():
            return False, ["geometry not found: {0}".format(self.cfg.geometry)]
        try:
            detect_ansys.resolve(self.cfg.ansys_version)
        except RuntimeError as exc:
            return False, [str(exc)]
        try:
            return licensing_preflight.preflight(
                self.cfg.ansys_version, list(licensing_preflight.DEFAULT_NEEDED))
        except Exception as exc:  # noqa: BLE001
            return True, ["licence preflight skipped ({0})".format(exc)]

    # ---- run -------------------------------------------------------
    def run(self) -> StudyResult:
        inst = detect_ansys.resolve(self.cfg.ansys_version)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        run_dir = Path(self.cfg.out_dir) if self.cfg.out_dir else (
            REPO / "artifacts" / (stamp + "_study_" + inst.version))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "study_config.json").write_text(json.dumps({
            **dataclasses.asdict(self.cfg), "ansys": inst.version, "started": stamp,
        }, indent=2), encoding="utf-8")

        res = pymechanical_runner.run_script(
            str(STUDY_RUNNER), str(run_dir),
            version=inst.version, exec_file=inst.mechanical_exe,
            sentinel_name="study_result.json",
            extra_env={
                "SD_EXT_DIR": str(EXT_DIR),
                "SD_GEOMETRY": str(Path(self.cfg.geometry).resolve()),
                "SD_ELEM_SIZES": self.cfg.size_str(),
                "SD_SETUP": self.cfg.setup,
                "SD_FORCE_N": repr(self.cfg.force_newtons),
            },
        )
        if not res.ok:
            raise InfraError(
                "Mechanical produced no study sentinel ({0}); log: {1}".format(res.note, res.log_path))

        raw = json.loads(Path(res.sentinel_path).read_text(encoding="utf-8"))
        (run_dir / "study_result.pretty.json").write_text(
            json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        levels = [LevelResult.from_json(lv) for lv in raw.get("levels", [])]
        status = raw.get("status", "?")
        return StudyResult(
            status=status,
            ok=(status in ("PASS", "NO_GEOMETRY")),
            run_dir=str(run_dir),
            summary_path=res.sentinel_path,
            raw=raw,
            levels=levels,
            restore_ok=raw.get("restore_ok"),
            ansys_version_reported=raw.get("ansys_version_reported", "unknown"),
        )

    # ---- outputs -------------------------------------------------
    def write_csv(self, result: StudyResult, path: str | None = None) -> str:
        path = path or str(Path(result.run_dir) / "convergence.csv")
        cols = ["level", "requested_h_m", "actual_h_m", "nodes", "elements",
                "generate_s", "solve_s", "peak_eqv", "peak_eqv_unit",
                "max_def", "max_def_unit", "reaction_N", "status"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for lv in result.levels:
                w.writerow([
                    lv.index, lv.requested_size_m, lv.actual_char_size_m,
                    lv.nodes, lv.elements, lv.generate_seconds, lv.solve_seconds,
                    (lv.peak_eqv or {}).get("value"), (lv.peak_eqv or {}).get("unit"),
                    (lv.max_def or {}).get("value"), (lv.max_def or {}).get("unit"),
                    (lv.reaction or {}).get("value"), lv.status,
                ])
        return path

    def cleanup(self, result: StudyResult, keep_rst: bool | None = None) -> list[str]:
        """Remove per-level dirs AFTER the summary + CSV exist (spec S49:
        'never delete study data before successful final processing')."""
        keep_rst = self.cfg.keep_rst if keep_rst is None else keep_rst
        summary_ok = Path(result.summary_path).is_file()
        csv_ok = (Path(result.run_dir) / "convergence.csv").is_file()
        if not (summary_ok and csv_ok):
            raise RuntimeError("refusing cleanup: summary/CSV not both present")
        removed = []
        for d in sorted(Path(result.run_dir).glob("level_*")):
            if not d.is_dir():
                continue
            if keep_rst and (d / "file.rst").is_file():
                # keep the rst + level.json, drop nothing
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed.append(str(d))
        return removed


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Run one convergence study.")
    p.add_argument("--geometry", required=True)
    p.add_argument("--h0", type=float, default=0.01)
    p.add_argument("--refinements", type=int, default=mesh_plan.DEFAULT_REFINEMENTS)
    p.add_argument("--ratio", type=float, default=mesh_plan.DEFAULT_RATIO)
    p.add_argument("--sizes", default=None, help="explicit csv of sizes, metres")
    p.add_argument("--setup", default="axial_bar")
    p.add_argument("--ansys-version", default="auto")
    p.add_argument("--cleanup", action="store_true", help="drop level_*/ dirs without a file.rst after summarising")
    args = p.parse_args()

    sizes = [float(x) for x in args.sizes.replace(";", ",").split(",")] if args.sizes else None
    cfg = StudyController.plan(
        args.geometry, h0=args.h0, refinements=args.refinements, ratio=args.ratio,
        sizes=sizes, setup=args.setup, ansys_version=args.ansys_version)
    ctrl = StudyController(cfg)

    ok, lines = ctrl.preflight()
    for ln in lines:
        print(ln)
    if not ok:
        print("PREFLIGHT FAILED", file=sys.stderr)
        raise SystemExit(2)

    try:
        result = ctrl.run()
    except InfraError as exc:
        print("INFRA FAILURE: {0}".format(exc), file=sys.stderr)
        raise SystemExit(2)

    csv_path = ctrl.write_csv(result)
    print("\nstatus              : {0}".format(result.status))
    print("ansys (reported)    : {0}".format(result.ansys_version_reported))
    print("restore_original_ok : {0}".format(result.restore_ok))
    print("levels              : {0}".format(len(result.levels)))
    print("convergence.csv     : {0}".format(csv_path))
    for lv in result.levels:
        pe = (lv.peak_eqv or {}).get("value")
        print("  L{0}  h={1:.5g}  n_el={2:<6}  peak={3}  [{4}]".format(
            lv.index, lv.requested_size_m, lv.elements,
            ("%.4g" % pe) if pe is not None else "-", lv.status))
    print("\nconvergence (provisional): {0}".format(result.classify_convergence()))

    if args.cleanup and result.ok:
        removed = ctrl.cleanup(result)
        print("cleaned {0} level dir(s)".format(len(removed)))

    raise SystemExit(0 if result.ok else 1)
