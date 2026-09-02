"""Probe for Milestone 9: exercise extension/act_study.run in an embedded
Mechanical session on an EXISTING analysis (spec S4), the way the ACT
"Run Singularity Study" button will.

    python devtools/_act_study_probe.py <geometry.stp> <run_dir> <h0> <h1> [h2 ...]

Prints the study summary JSON to stdout. Used by tests/mechanical/test_milestone9.py.
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "extension"


def main():
    geo = os.path.abspath(sys.argv[1])
    run_dir = os.path.abspath(sys.argv[2])
    sizes = [float(x) for x in sys.argv[3:]]

    from ansys.mechanical.core import App
    app = App(version=252)
    app.update_globals(globals())          # ExtAPI, Model, Quantity, Ansys, ... into this ns

    sys.path.insert(0, str(EXT))
    import mech_env
    import model_setup
    import act_study

    mech_env.bind({k: globals().get(k) for k in
                   ("ExtAPI", "Model", "Quantity", "Ansys", "LoadDefineBy")
                   if globals().get(k) is not None})

    # a normal user model: geometry + real BCs/loads already set up
    analysis, _support, _faces = model_setup.build_clean_tension_analysis(
        Model, geo, force_newtons=1000.0)      # noqa: F821

    restore = os.environ.get("SD_PROBE_RESTORE") in ("1", "true", "True")
    summary = act_study.run(
        analysis, sizes, run_dir, restore=restore,
        progress=lambda f, m: sys.stderr.write("[%3d%%] %s\n" % (int(100 * f), m)))
    sys.stdout.write("SUMMARY_JSON_BEGIN\n")
    sys.stdout.write(json.dumps(summary))
    sys.stdout.write("\nSUMMARY_JSON_END\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
