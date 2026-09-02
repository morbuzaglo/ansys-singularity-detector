# The two execution layers

The single most important structural rule in this project (master spec §2).

```
                 repo
                 ├── extension/                 <-- LAYER A: Mechanical runtime
                 │   ├── *.py  (ACT extension)      IronPython 2.7 (.NET)
                 │   └── mechanical_scripts/*.py    IronPython 2.7 (.NET)
                 │
                 └── devtools/  tests/           <-- LAYER B: external automation
                     └── *.py                        modern CPython 3 (venv)
```

## Layer A — Mechanical runtime (IronPython 2.7)

Runs *inside* Ansys Mechanical / the ACT engine. Constraints:

- **No** f-strings, dataclasses, `match`, walrus, `pathlib` conveniences,
  `typing` generics, `enum` module.
- **No** compiled wheels — `numpy`, `scipy`, `pandas` are unavailable.
- Use `.format()` / `%`, `clr` + `System.*`, `import json/os/math`, `import units`,
  `ExtAPI.*`, `Quantity(...)`, and `import mech_dpf` for DPF.
- Output via `ExtAPI.Log.WriteMessage` and **machine-readable JSON files**, never
  console text alone.

Contents: the ACT extension (`main.py`, `study_controller.py`, `mesh_manager.py`,
`result_extractor.py`, `dpf_adapter.py`, `visualization.py`, `compatibility.py`,
…) and standalone Mechanical scripts the external runner injects.

## Layer B — external automation (CPython 3)

Runs from the terminal / VS Code. This is where the real engineering compute
lives so it can be unit-tested without Ansys (spec §42):

- Ansys discovery & launch (`detect_ansys.py`, `mechanical_cli.py`,
  `pymechanical_runner.py`, `workbench_runner.py`)
- convergence math: `sigma_inf + C h^p` vs `B + A h^-lambda` fits, Richardson
  extrapolation, ZZ/SPR recovery, clustering (numpy/scipy)
- cross-mesh result mapping validation
- test orchestration, report + chart generation, CI
- extension deploy/sync (`extension_deployer.py`)

## The bridge

Layer B launches Ansys and pushes a Layer A script into it; Layer A writes a JSON
sentinel into the run's artifact directory; Layer B reads it back and decides
pass/fail. Neither layer imports the other. Every ACT UI callback is a one-liner
delegating to a Layer A function that a Layer B test can also drive in batch.
