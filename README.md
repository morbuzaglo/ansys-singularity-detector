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

**Milestones 0–1 complete on 2025 R2.**

- **M0** `python devtools/run_milestone0.py` — no human in Mechanical: detect
  Ansys → licence preflight → PyMechanical embedded → import STEP → static
  structural → BC → mesh → solve → peak stress → JSON → exit.
- **M1** `python devtools/run_milestone1.py --geometry test_models/bar.stp` —
  parametrised element-size sweep (`mesh_manager` + `result_extractor` +
  `model_setup`), per-level `file.rst` preserved, mesh restored. Plain bar peak
  stress converges (increment ratio 0.36); reaction = applied load at every
  level.

`pytest tests --run-benchmarks` green. Tracker:
[`docs/milestones.md`](docs/milestones.md). Next: **M2** full convergence-study
controller (spec §22).

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
