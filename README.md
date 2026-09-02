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

**Milestones 0–3 complete on 2025 R2.**

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
  every coarser level onto the finest mesh's node locations (official
  `mapping.on_coordinates`, scipy fallback, choice recorded), emit the
  σ_vm(x, hᵢ) series + per-point divergence ratio. Mapping accuracy is
  independently validated (analytic linear field → machine precision; DPF vs
  scipy agree) before it can feed a classifier.

`pytest tests --run-benchmarks` green. Tracker:
[`docs/milestones.md`](docs/milestones.md). Next: **M4** primary
singularity classifier (finite-limit vs divergent fit; spec §26/§27).

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
