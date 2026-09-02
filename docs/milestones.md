# Milestone tracker

**GitHub**: <https://github.com/morbuzaglo/ansys-singularity-detector> (private).
At every completed major milestone: `git push origin main` **and** an annotated
tag `git tag -a milestone-N -m "..." && git push origin milestone-N`.

| Tag | Points at |
|-----|-----------|
| `milestone-0` | Autonomous Ansys runner, validated on 2025 R2 |
| `milestone-1` | Parametrised mesh/solve sweep (`mesh_manager` + `result_extractor` + `model_setup`) |
| `milestone-2` | Convergence-study controller: N-level loop, per-level `level_XX/`, restore, clean failure, `convergence.csv` |
| `milestone-3` | Spatial cross-mesh mapping: `field_mapping` (scipy) + `dpf_adapter` (`mapping.on_coordinates`) + `compatibility` probe + `cross_mesh` σ(x,hᵢ) series |
| `milestone-4` | Primary singularity classifier: `convergence_classifier` (finite vs divergent fit) + `classify_study`; plate-with-hole (converges) vs re-entrant corner (diverges) clearly separate |
| `milestone-5` | Secondary metrics: `secondary_metrics.py` — S28 nodal-difference persistence, S29 discretisation error, S30 hotspot localisation, S31 geometry/BC prior, S32 global sanity gate |
| `milestone-6` | Singularity Confidence score (`confidence.py`, spec §33 weighting + S32 gate) + region clustering (`region_clustering.py`, spec §34) + `analyze_study.py` end-to-end pipeline |
| `milestone-7` | Custom contours: `stress_recovery.py` (spec §37 filtered stress) + `build_contours.py` (Raw / Confidence / Filtered fields) + `extension/{visualization.py,SingularityDetector.xml}` (IronPython 2.7 `<evaluate>` callbacks) |


Order and definitions from the master spec (§51). Status is honest: only what has
actually been executed and verified is "done".

| # | Milestone | Status | Evidence |
|---|---|---|---|
| — | `act-builder` skill (global) | **done** | `~/.claude/skills/act-builder/` — SKILL.md, 10 knowledge notes, template, 4 official 251 PDFs + samples |
| 0 | Autonomous Ansys Runner | **DONE (on 252)** | Full PASS 2026-09-02. `artifacts/2026-09-02_045523_milestone0_252/` — terminal → PyMechanical embedded → import STEP → static structural → BC → mesh → solve (Done) → peak σ 10.86 MPa (nominal 10) + δ ≈ 5 µm (analytic 5.0) → JSON → exit, no orphans. `pytest tests --run-benchmarks` = 10 passed, incl. `tests/mechanical/test_milestone0.py` with physics sanity bands. |
| 1 | Mesh/Solve prototype | **DONE (on 252)** | Full PASS 2026-09-02. `artifacts/2026-09-02_051233_milestone1_252/`. 3-level global-size sweep on `bar.stp`: h=[5,4,3]mm → elems [80,225,297], peak σ_eqv [10.86, 11.86, 12.22] MPa (increments 1.00→0.36, ratio 0.36 → **converging**), max def flat at 4.98 µm (analytic 5.0), reaction = 1000 N at every level, per-level `file.rst` preserved, original mesh restored. `mech_env` / `mesh_manager` / `result_extractor` / `model_setup` modules. `pytest tests/mechanical/test_milestone1.py --run-benchmarks` green. |
| 2 | Convergence Study Controller | **DONE (on 252)** | Full PASS 2026-09-02. `extension/study_runner.py` (Mechanical-side loop, per-level `level_XX/{level.json,file.rst}`, checkpointed summary, restore even on failure) + `devtools/study_controller.py` (`StudyConfig`/`StudyController`/`StudyResult`, preflight, `convergence.csv`, `cleanup()` guarded by spec §49, provisional `classify_convergence()`). 4-level study on `bar.stp`: all levels ok, `restore_ok=True`, csv written, verdict `convergent`. Bad-setup path handled cleanly (spec §50). `pytest tests/mechanical/test_milestone2.py --run-benchmarks` = 2 passed. |
| 3 | Spatial cross-mesh mapping | **DONE (on 252)** | Full PASS 2026-09-02. `devtools/field_mapping.py` (scipy barycentric-linear + nearest-fill, hull flagging, `analytic_validation`, `nearest_value_error`), `devtools/compatibility.py` (DPF probe: core 0.16.1 / server 10.0 / `mapping.on_coordinates` present), `devtools/dpf_adapter.py` (read nodal von Mises + coords from a preserved `.rst`; official operator or scipy fallback, choice recorded), `devtools/cross_mesh.py` (`CrossMeshSeries`: σ_vm(x,hᵢ) on the finest mesh's node locations, increments, divergence ratio, npz/csv). Validated: identity map exact, analytic linear field machine-precision, DPF vs scipy agree < 5% of range on the real field, `valid_fraction` 1.0. `pytest tests/mechanical/test_milestone3.py --run-benchmarks` = 4 passed. |
| 4 | Primary singularity classifier | **DONE (on 252)** | Full PASS 2026-09-02. `devtools/convergence_classifier.py` — increment-ratio analysis + two-model fit (`σ∞+C·hᵖ` finite vs `B+A·h^(-λ)` divergent), compared on normalised residual + plausibility; `classify_series` / `classify_hotspot_neighborhood` (localises to the corner) / vectorised `field_divergence`. `devtools/classify_study.py` ties study → `cross_mesh` → classifier → `classification.json`. Geometry: `devtools/step_prism.py` (bar, **lbracket**, vnotch — no CAD kernel) + `devtools/make_geometry.py` (cadquery: **plate_hole**, fillet_bar). New `clean_tension` setup (frictionless supports, no fixed-face restraint singularity). **Result: plate_hole → convergent, evidence 0.23, Kt limit 3.05; lbracket corner → singular, evidence 1.00, λ 0.463 (theory 0.4555); gap 0.77.** 19 unit tests (synthetic, spec §42) + `test_milestone4.py`. |
| 5 | Secondary metrics | **DONE (on 252)** | Full PASS 2026-09-02. `devtools/secondary_metrics.py` — **S28** nodal-difference persistence (DPF `nodal_difference_fc`/`nodal_fraction_fc`; near-hotspot vs bulk decay ratio), **S29** ZZ-lite discretisation error, **S30** hotspot localisation (does the σ>0.8·peak region shrink while peak grows), **S31** geometry/BC prior (re-entrant edge detection from skinned mesh dihedral angles + BC-face proximity), **S32** global sanity gate (deformation/reaction stability → confidence multiplier). `assemble()` → `secondary_metrics.json`. **Result: lbracket S28 0.69 / S30 1.0 / S31 0.68 ; plate_hole S28 0.0 / S30 0.0 / S31 0.0 ; S32 1.0 for both.** `study_runner` now records `bc_face_geometry`. 6 unit tests + `test_milestone5.py`. |
| 6 | Confidence score | **DONE (on 252)** | Full PASS 2026-09-02. `devtools/confidence.py` — per-point `0.55·mesh_div + locality·(0.20·S28 + 0.10·S29) + 0.15·geom_prior`, `× S32 gate`, ×100; categories 0–39/40–69/70–89/90–100. `devtools/region_clustering.py` — union-find over a KD-tree radius graph on confidence≥threshold points; per region: max/mean confidence, centroid, size, max raw stress, divergence exponent, nearest re-entrant edge / BC face, probable cause. `devtools/analyze_study.py` — full pipeline → `singularity_analysis.json` + `confidence_field.npz`. **Result: L-bracket → "singular", confidence 77.6 [probable singularity], 1 region on the re-entrant edge (centroid (20.8,19.4,6.0), cause "re-entrant corner … λ~0.46"); plate-with-hole → "convergent", confidence 2.7, 0 regions.** 9 unit tests + `test_milestone6.py` (4 passed). Also added `dpf_adapter.eval_retry` (transient DPF licence-check retry) + `ANSYS_DPF_ACCEPT_LA=Y`. Weights/thresholds still PRELIMINARY (spec §33) — calibrate on the full suite. |
| 7 | Custom Mechanical contours | **DONE (on 252)** | Full PASS 2026-09-02. `devtools/stress_recovery.py` (spec §37 exact formula, same-body/material, ~2.5 element layers, median±3·MAD rejection, "Not Recoverable" if <6 donors — never invents a value). `devtools/build_contours.py` → `contour_fields.{npz,csv}` + PNGs: **Raw Stress** (byte-identical, §36), **Singularity Confidence [%]** (0–100), **Singularity-Filtered Stress**. `extension/visualization.py` (IronPython 2.7, grid nearest lookup + 3 `<evaluate>` callbacks) + `extension/SingularityDetector.xml` (3 `<result>` objects, GUID `f27a27e0-…`). L-bracket: 41 corner nodes recovered mean −9.8% / max −30%, 0 not-recoverable; plate-with-hole: nothing filtered. `docs/stress_recovery_research.md` (§38: MLS/SPR/ZZ). 19 unit + `test_milestone7.py` (6). Unit suite = 101. |
| 8 | Region clustering + charts | next | Clustering already done in M6 (`region_clustering.py`). M8 = convergence charts (spec §39): global (peak σ / deformation / strain energy / reaction imbalance vs h) + per-region (peak σ vs h, log-log, increment, λ, nodal-fraction, energy-error, confidence-vs-level) as PNG **and** CSV data. |
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
