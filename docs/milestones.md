# Milestone tracker

Order and definitions from the master spec (§51). Status is honest: only what has
actually been executed and verified is "done".

| # | Milestone | Status | Evidence |
|---|---|---|---|
| — | `act-builder` skill (global) | **done** | `~/.claude/skills/act-builder/` — SKILL.md, 10 knowledge notes, template, 4 official 251 PDFs + samples |
| 0 | Autonomous Ansys Runner | **DONE (on 252)** | Full PASS 2026-09-02. `artifacts/2026-09-02_045523_milestone0_252/` — terminal → PyMechanical embedded → import STEP → static structural → BC → mesh → solve (Done) → peak σ 10.86 MPa (nominal 10) + δ ≈ 5 µm (analytic 5.0) → JSON → exit, no orphans. `pytest tests --run-benchmarks` = 10 passed, incl. `tests/mechanical/test_milestone0.py` with physics sanity bands. |
| 1 | Mesh/Solve prototype | next | Generalise `milestone0_hello.py` into `mesh_manager` + `result_extractor` with an element-size sweep; capture actual element size, per-level `.rst`. |
| 2 | Convergence Study Controller | not started | |
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

## Decisions (settled 2026-09-02)

- Working target: **252** now, behind the compat layer; 251 to be validated if installed.
- `.venv` created; deps installed (see `requirements-dev.lock.txt`).
- Autonomous Ansys launches: **authorised**. Preflight via `licensing_preflight.py`.
- Project has its own git repo (`git init` done); home-dir repo untouched.
