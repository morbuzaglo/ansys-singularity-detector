# Trying it yourself

Two ways, depending on whether you want to see it **in the Mechanical window**
or just get the numbers/plots.

Everything below assumes:

```bat
cd "c:\Users\morde\OneDrive - Technion\Documents\VSCODE projects\Ansys Singularity Recognition ACT"
.venv\Scripts\activate
```

(the `.venv` already has every dependency; `python -m devtools.detect_ansys`
should print your 2025 R2 install).

---

## A. Headless — run the whole analysis, no GUI (works today)

This is exactly what the automated tests do. One geometry, ~4 minutes for the
solves.

```bat
:: 1. run the convergence study (5 mesh levels, clean uniaxial tension)
python -m devtools.study_controller --geometry test_models\lbracket.stp ^
    --setup clean_tension --sizes 0.004,0.003,0.0022,0.0016,0.0012

:: note the artifact dir it prints, e.g. artifacts\2026-09-02_1830xx_study_252
set STUDY=artifacts\<paste that dir>

:: 2. classify + Singularity Confidence + regions
python -m devtools.analyze_study %STUDY%

:: 3. the three contour fields (Raw / Confidence / Filtered stress)
python -m devtools.build_contours %STUDY%

:: 4. convergence charts
python -m devtools.convergence_charts %STUDY%
```

Then look in `%STUDY%`:

| file | what |
|---|---|
| `singularity_analysis.json` | headline verdict, Singularity Confidence 0-100, the region table with probable cause |
| `contours_summary.json` | Raw / Confidence / Filtered-Stress ranges, how many nodes were recovered |
| `contour_fields.csv` | per-node: x,y,z, raw stress, confidence %, filtered stress |
| `contours\*.png` | quick 3-D scatter previews of the three fields |
| `charts\*.png` + `charts\*.csv` | peak stress vs h, log-log, model fits, nodal-fraction vs level, ... |
| `convergence.csv` | the raw per-level table (nodes, elements, peak stress, deformation, reaction) |
| `level_00\ .. level_04\` | the preserved `file.rst` for each mesh level |

Try `test_models\plate_hole.stp` the same way for the "converges, non-singular"
case — you should get confidence ~3 and zero regions.

---

## B. In the Mechanical GUI -- one ribbon button

You need an **ACT licence** (the site licence has it: increment `agppi`).

### One-time: deploy the extension

```bat
python -m devtools.extension_deployer --ansys-version 252
```

Copies `SingularityDetector.xml` + `SingularityDetector\` (script + every
IronPython module + `sd_config.json` pointing at this repo's `.venv`) into
`%APPDATA%\Ansys\v252\ACT\extensions\`. Re-run after a code change; `--link`
junctions the folder for edit-in-place; `--uninstall` removes it.
(It is already deployed on this machine.)

### Each time

1. Open **Workbench** -> **Static Structural** -> attach your geometry (e.g.
   `test_models\lbracket.stp`), edit in **Mechanical**, apply your loads/supports,
   mesh, and **Solve** once.
2. **Extensions -> Manage Extensions** -> tick **SingularityDetector**. A
   **Singularity Detector** ribbon tab appears with three buttons.
3. *(optional)* **Study Settings** -- shows the current `refinements` / `ratio` /
   `confidence_threshold`; edit `sd_study_settings.json` (path is in the dialog)
   to change them.
4. Click **Run Singularity Study**. It:
   - runs the mesh-refinement study **on your analysis** (your BCs/loads
     untouched; original mesh restored afterwards),
   - shells out to the repo `.venv` for the numpy/DPF analysis
     (`analyze_study` -> `build_contours` -> `convergence_charts`),
   - drops **Raw Stress -- Original FE Solution**, **Singularity Confidence [%]**
     and **Singularity-Filtered Stress (estimate)** under *Solution* and
     evaluates them,
   - pops a summary: classification, confidence 0-100, divergence exponent, and
     the artifact folder (JSON + PNG charts).
5. The Confidence contour is 0-100; the Filtered contour is the raw field with
   the flagged region pulled toward the converged neighbourhood (grey /
   max-double where "Not Recoverable"). **Raw Stress** is the untouched solver
   field, there for comparison.

**Add Contours** (button 2) just re-adds the three results from the most recent
`contour_fields.csv` without re-running the study.

If the button reports "cannot find the analysis venv Python", edit
`venv_python` in
`%APPDATA%\Ansys\v252\ACT\extensions\SingularityDetector\sd_config.json`.

### Notes

- The **Raw Stress** result is deliberately identical to Mechanical's own
  Equivalent (von Mises) Stress -- it's there so you always have the untouched
  solver field next to the estimates.
- **Singularity-Filtered Stress is an estimate for visualisation**, never "true"
  stress. Where confidence < 70 it is exactly the raw stress.
- For a crack-tip-type model the right answer is fracture-mechanics quantities
  (K, J), not a local finite stress -- the analysis will say so in
  `singularity_analysis.json`.

---

## Still to come

- **Cross-version** (M10): validated here on 2025 R2 only; 2025 R1 / 2026 R1 are
  architecturally supported but not locally tested on this machine.
- **`.wbex` packaging** (M11) for sharing the extension without the repo/venv.
