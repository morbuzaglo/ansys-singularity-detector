# Ansys Mechanical ACT Extension — Automatic Stress Singularity Detection

An Ansys Mechanical ACT extension for Static Structural analyses that automatically
detects and characterises stress singularities: automated mesh-refinement
convergence studies, local convergence/divergence analysis, elemental-nodal
discontinuity, discretisation-error indicators, geometry/BC heuristics, cross-mesh
spatial comparison, a Singularity Confidence score, region clustering, convergence
charts, and custom Mechanical contours (Singularity Confidence, Singularity-Filtered
Stress).

The authoritative scope, definitions, milestones (§51) and acceptance criteria
(§44) come from the project owner's _Master Development Specification_. Section
numbers referenced across the code and docs (e.g. "spec §24") point into it.
Keep a copy at `docs/SPEC.md` if you want it version-controlled.

## Status

**Milestones 0–9 complete on 2025 R2.**  **Want to run it? → [`docs/TRYING_IT.md`](docs/TRYING_IT.md).**

- **M0** `python devtools/run_milestone0.py` — no human in Mechanical: detect
  Ansys → licence preflight → PyMechanical embedded → import STEP → static
  structural → BC → mesh → solve → peak stress → JSON → exit.
- **M1** `python devtools/run_milestone1.py --geometry test_models/bar.stp` —
  parametrised element-size sweep (`mesh_manager` + `result_extractor` +
  `model_setup`).
- **M2** `python devtools/study_controller.py --geometry test_models/bar.stp
  --h0 0.01` — full convergence-study controller: N-level loop, per-level
  `level_XX/{level.json,file.rst}` preserved, original mesh restored (even after
  a failure), `convergence.csv`, guarded `cleanup()`, provisional trend verdict.
- **M3** `python -m devtools.cross_mesh <study_dir>` — spatial cross-mesh
  mapping: read nodal von Mises + coords from each preserved `.rst` (DPF), map
  every coarser level onto the finest mesh's node locations, emit the
  σ_vm(x, hᵢ) series. Mapping accuracy independently validated first.
- **M4** `python -m devtools.classify_study <study_dir>` — primary singularity
  classifier: increment-ratio analysis + finite (`σ∞+C·hᵖ`) vs divergent
  (`B+A·h^(-λ)`) model fit compared on residual + plausibility, localised to the
  hotspot neighbourhood. **plate-with-hole → convergent** (Kt limit 3.05);
  **re-entrant corner → singular** (λ ≈ 0.46, theory 0.4555); evidence gap 0.77.
- **M5** `python -m devtools.secondary_metrics <study_dir>` — supporting signals
  (spec §28–32): nodal-difference persistence, ZZ-lite discretisation error,
  hotspot localisation, geometry/BC prior, global-solution sanity gate.
- **M6** `python -m devtools.analyze_study <study_dir>` — the full pipeline:
  cross_mesh → classifier → secondary metrics → **Singularity Confidence**
  (0–100, spec §33 weighting × the S32 sanity gate) → **region clustering**
  (spec §34). Writes `singularity_analysis.json` + `confidence_field.npz`.
  **L-bracket → "singular", confidence 77.6 (probable singularity), one region
  on the re-entrant edge (λ ≈ 0.46); plate-with-hole → "convergent",
  confidence 2.7, zero regions.**

- **M7** `python -m devtools.build_contours <study_dir>` — the three fields on
  the finest mesh: **Raw Stress** (byte-identical to the solver result),
  **Singularity Confidence [%]** (0–100), **Singularity-Filtered Stress** (raw
  where confidence < 70; a robust confidence-weighted neighbourhood estimate,
  built only from converged donors, where ≥ 70; "Not Recoverable" rather than an
  invented value). `extension/SingularityDetector.xml` + `visualization.py` are
  the IronPython-2.7 `<evaluate>` callbacks that surface them in the Mechanical
  tree. L-bracket corner: 41 nodes recovered (mean −10 %); plate-with-hole:
  nothing filtered.

- **M8** `python -m devtools.convergence_charts <study_dir>` — global + per-region
  convergence charts, each PNG **plus** its CSV.
- **M9** the **ACT ribbon**: `python -m devtools.extension_deployer` installs
  **SingularityDetector** into Ansys; in Mechanical the **Singularity Detector**
  tab has **Run Singularity Study** (runs the sweep on *your* analysis, restores
  your mesh, shells out to the venv for the analysis, drops the three result
  contours in the tree + a summary), **Add Contours**, **Study Settings**.
  See [`docs/TRYING_IT.md`](docs/TRYING_IT.md).

`pytest tests --run-benchmarks` green (116 unit + per-milestone mechanical
tests). Tracker: [`docs/milestones.md`](docs/milestones.md). Next: **M10**
cross-version validation + `run_validation.py`; **M11** `.wbex` packaging.

Working Ansys target on this machine: **2025 R2 / 252** (primary spec target
2025 R1 / 251 is not installed — [`docs/ansys_environment.md`](docs/ansys_environment.md)).

## Two execution layers (read first)

[`docs/two_layer_architecture.md`](docs/two_layer_architecture.md).

- `extension/` — runs **inside Mechanical**: IronPython 2.7, no numpy, no f-strings.
- `devtools/`, `tests/` — run **from the terminal**: modern CPython 3 in a venv.

Never import one layer from the other.

## Layout

```
extension/            ACT extension + Mechanical-side scripts   (IronPython 2.7)
  mechanical_scripts/ standalone scripts the runner injects
devtools/             Ansys discovery, launchers, orchestrators  (CPython 3)
tests/                unit / mechanical / regression / benchmarks
test_models/          version-controlled geometry fixtures (.stp)
artifacts/            per-run output dirs (git-ignored)
logs/                 (git-ignored)
docs/                 architecture + milestone notes
```

## Quick start (external layer)

```bat
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt

python devtools\detect_ansys.py                 :: what Ansys is here
python -m pytest tests\unit                      :: fast, no Ansys
python devtools\run_milestone0.py                :: launch Mechanical, prove the loop
```

## Companion skill

Global Claude skill **`act-builder`** (`~/.claude/skills/act-builder/`) holds the
curated ACT knowledge base, the official 2025 R1 reference PDFs + sample
extensions, and a starter extension template. Invoke with `/act-builder`.
