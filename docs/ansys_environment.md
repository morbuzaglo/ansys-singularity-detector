# Ansys environment on this development machine

_Captured 2026-09-02 by `devtools/detect_ansys.py`._

| Version | Release | Status | Notes |
|---|---|---|---|
| **252** | 2025 R2 | **usable, working target** | Found via `ANSYS252_DIR`. Root `C:\Program Files\ANSYS Inc\v252`. `AnsysWBU.exe`, `ansys252.exe`, `RunWB2.exe` all present. `AWP_ROOT252` is set. |
| 241 | 2024 R1 | present but NOT usable | fs-scan only; no `AnsysWBU.exe` under it (solver/support components only). Not in the supported set. |
| **251** | 2025 R1 | **NOT installed** | This is the master spec's *primary* target. |

## Consequence

- The spec's primary compatibility target (**2025 R1 / 251**) cannot be locally
  validated here. Per spec §45 we must not claim "Supports 2025 R1+" until 251
  has actually run the suite.
- Development proceeds against **252** as the working target. All code stays
  behind the compatibility layer (`extension/compatibility.py`, `dpf_adapter.py`,
  `devtools/detect_ansys.py`) so a 251 install can be slotted in and validated
  with `python devtools/run_validation.py --ansys-version 251` later.
- `--resolve auto` picks the **oldest supported** usable release (spec §11); with
  only 252 present that is 252.
- The bundled reference PDFs in the `act-builder` skill are **251** docs. Where
  252 behaviour differs, record it in
  `~/.claude/skills/act-builder/knowledge/compatibility/ironpython-and-versions.md`.

## Also on PATH / of note

- Bundled CPython 3.10 at `C:\Program Files\ANSYS Inc\v252\commonfiles\CPython\3_10\winx64\Release\python\`
  (ships numpy/scipy). Not used — the external layer uses its own `.venv`.
- No `ansys-mechanical-core` / `ansys-dpf-core` in the active Python yet
  (`pip install -r requirements-dev.txt`). The native `AnsysWBU.exe` batch
  backend does not need them.
- License env: `ANSYSLMD_LICENSE_FILE=1055@localhost`, `ANSYSLIC_DIR` set.
