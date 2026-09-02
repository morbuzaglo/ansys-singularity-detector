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

## B. See the contours inside the Mechanical GUI (manual, until Milestone 9)

There is **no ribbon button yet** (that's Milestone 9). Until then you load the
extension and add the results by hand. You need an **ACT licence** (the site
licence has it: increment `agppi`).

### One-time: deploy the extension

```bat
python -m devtools.extension_deployer --ansys-version 252
```

This copies `SingularityDetector.xml` + `SingularityDetector\` into
`%APPDATA%\Ansys\v252\ACT\extensions\`. Re-run it after any code change (or use
`--link` once to junction the folder so edits are picked up on the next
Mechanical restart). Remove it with `--uninstall`.

### Each time

1. **Run part A first** for the geometry you want, so `contour_fields.csv` exists
   in the study dir.
2. Open **Workbench**, drop a **Static Structural** system, attach
   `test_models\lbracket.stp` (or your own model), edit the model in
   **Mechanical**, mesh and **Solve** once (any mesh — the contours resample by
   coordinate, not node id).
3. In Mechanical: **Extensions -> Manage Extensions**, tick **SingularityDetector**.
   (If it isn't listed: *File -> Options -> Extensions -> Additional Extension
   Folders* and add the repo's `extension\` folder, or check the deploy path.)
4. Tell the extension where the CSV is. In the **ACT Console** (Automation tab,
   enable *Debug Mode* first under *File -> Options -> Extensions*):

   ```python
   import visualization
   visualization.CONTOUR_CSV_PATH = r"c:\...\artifacts\<STUDY>\contour_fields.csv"
   ```

   (Or copy `contour_fields.csv` into the analysis **Solver Files Directory** —
   right-click the Solution branch -> *Open Solver Files Directory* — and the
   callbacks find it automatically.)
5. On the **SingularityDetector** ribbon tab, click **AddContours** (or in the
   console: `ExtAPI.DataModel.AnalysisList[0].CreateResultObject("Singularity Confidence [%]", ExtAPI.ExtensionManager.CurrentExtension)`).
6. Three results appear under *Solution*: **Raw Stress -- Original FE Solution**,
   **Singularity Confidence [%]**, **Singularity-Filtered Stress (estimate)**.
   Right-click -> **Evaluate All Results**. The Confidence contour is 0-100; the
   Filtered contour shows the raw field with the flagged region pulled down
   (grey / max-double where "Not Recoverable").

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

## Milestone 9 (next) will replace step B4-B5 with a single ribbon button

"Run Singularity Study" -> pick refinements/ratio -> it runs the sweep, the
analysis and drops the three results in the tree for you.
