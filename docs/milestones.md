# Milestone tracker

**GitHub**: <https://github.com/morbuzaglo/ansys-singularity-detector> (private).
At every completed major milestone: `git push origin main` **and** an annotated
tag `git tag -a milestone-N -m "..." && git push origin milestone-N`.

| Tag | Points at |
|-----|-----------|
| `milestone-0` | Autonomous Ansys runner, validated on 2025 R2 |
| `milestone-1` | Parametrised mesh/solve sweep (`mesh_manager` + `result_extractor` + `model_setup`) |


Order and definitions from the master spec (§51). Status is honest: only what has
actually been executed and verified is "done".

| # | Milestone | Status | Evidence |
|---|---|---|---|
| — | `act-builder` skill (global) | **done** | `~/.claude/skills/act-builder/` — SKILL.md, 10 knowledge notes, template, 4 official 251 PDFs + samples |
| 0 | Autonomous Ansys Runner | **DONE (on 252)** | Full PASS 2026-09-02. `artifacts/2026-09-02_045523_milestone0_252/` — terminal → PyMechanical embedded → import STEP → static structural → BC → mesh → solve (Done) → peak σ 10.86 MPa (nominal 10) + δ ≈ 5 µm (analytic 5.0) → JSON → exit, no orphans. `pytest tests --run-benchmarks` = 10 passed, incl. `tests/mechanical/test_milestone0.py` with physics sanity bands. |
| 1 | Mesh/Solve prototype | **DONE (on 252)** | Full PASS 2026-09-02. `artifacts/2026-09-02_051233_milestone1_252/`. 3-level global-size sweep on `bar.stp`: h=[5,4,3]mm → elems [80,225,297], peak σ_eqv [10.86, 11.86, 12.22] MPa (increments 1.00→0.36, ratio 0.36 → **converging**), max def flat at 4.98 µm (analytic 5.0), reaction = 1000 N at every level, per-level `file.rst` preserved, original mesh restored. `mech_env` / `mesh_manager` / `result_extractor` / `model_setup` modules. `pytest tests/mechanical/test_milestone1.py --run-benchmarks` green. |
| 2 | Convergence Study Controller | next | Full study loop (spec §22): N levels + metadata dir per level, `study_controller`, restore-original test, handle failed solve cleanly. Generalise beyond the axial-bar setup. |
| 3 | Spatial cross-mesh mapping | not started | |
| 4 | Primary singularity classifier | not started | |
| 5 | Secondary metrics | not started | |
| 6 | Confidence score | not started | |
| 7 | Custom Mechanical contours | not started | |
| 8 | Region clustering + charts | not started | |
| 9 | ACT interface | not started | |
| 10 | Cross-version validation | blocked | 251 not installed here — see `ansys_environment.md` |
| 11 | Release packaging | not started | |

## Milestone 0 — how it landed

- **Backend: PyMechanical embedded** (`ansys.mechanical.core.App(version=252)`).
  `devtools/pymechanical_runner.py`, strategy `embedded` (25–71 s incl. .NET load).
  The native `AnsysWBU.exe -DSApplet ... -b -script` path (`mechanical_cli.py`) was
  tried first and **did not produce output headlessly on 252** — kept only as a
  documented fallback; `BATCH_ARGV_CANDIDATES` still needs pruning against the
  Mechanical User's Guide if we ever need the non-PyMechanical path.
- **Geometry fixture: `devtools/step_box.py`** writes a valid STEP AP214 B-rep box
  directly (no CAD kernel, no Ansys). `test_models/bar.stp` = 100×10×10 mm.
  `devtools/make_test_geometry.py` (SpaceClaim headless) was attempted and is
  **unverified** — SpaceClaim launches but the geometry script produced nothing;
  left in place, marked, for later fixtures that need real features (holes/notches).
- **Licensing preflight**: `devtools/licensing_preflight.py` — PASS (server up,
  `ansys`/`struct`/`mech_*`/`agppi`/`acdi_adprepost` all free).
- **Evidence**: `artifacts/2026-09-02_044624_milestone0_252/milestone_result.pretty.json`
  (`status: PASS`, 7 steps). `tests/mechanical/test_milestone0.py` — 1 passed
  (plumbing), benchmark full-solve gated behind `--run-benchmarks`.

## Milestone 1 — notes

- **`mesh.ClearGeneratedData()` between levels, NOT `model.ClearGeneratedData()`**
  — the latter left `Solution.Status == SolveRequired` after `Solve(True)` in an
  embedded session (silent no-solve). Cost one debug cycle.
- **Imported Mechanical-side modules don't see engine-injected globals**
  (`ExtAPI`, `Quantity`, `Ansys`, ...) under `execute_script_from_file`. Fix:
  `extension/mech_env.py` — the driver script calls `mech_env.bind({...})` once,
  library modules read `mech_env.G`. (The real ACT extension gets `ExtAPI` for
  free from the ACT loader; this shim is only for the script drivers.)
- `body.Volume` is in the **active unit system (mm³ in Mechanical)**, not m³ —
  `mesh_manager._total_volume_m3()` converts via the ACT `units` helper with a
  magnitude-heuristic fallback, so `actual_char_size_m` is real metres.
- `AddStrainEnergy()` — not available as-is on 252 (returns null); revisit for
  the global sanity gate (spec §32).

## Decisions (settled 2026-09-02)

- Working target: **252** now, behind the compat layer; 251 to be validated if installed.
- `.venv` created; deps installed (see `requirements-dev.lock.txt`).
- Autonomous Ansys launches: **authorised**. Preflight via `licensing_preflight.py`.
- Project has its own git repo (`git init` done); home-dir repo untouched.
