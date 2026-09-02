"""Unit tests for devtools.extension_deployer -- deploy to a temp dir, no Ansys."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import extension_deployer as ed  # noqa: E402


def test_deploy_produces_the_act_layout(tmp_path):
    dest = tmp_path / "extensions"
    ed.deploy(version="252", dest=str(dest))

    assert (dest / "SingularityDetector.xml").is_file()
    folder = dest / "SingularityDetector"
    assert folder.is_dir()
    # the extension script + every IronPython module it imports
    for f in ("main.py", "visualization.py", "act_study.py", "mech_env.py",
              "mesh_manager.py", "result_extractor.py", "model_setup.py"):
        assert (folder / f).is_file(), f
    assert (folder / "images" / "result.bmp").is_file()

    # NOT the headless-only Mechanical scripts
    assert not (folder / "study_runner.py").exists()
    assert not (folder / "mechanical_scripts").exists()

    xml = (dest / "SingularityDetector.xml").read_text(encoding="utf-8")
    assert "f27a27e0-ca49-4fa6-87f4-ef972a94c1d7" in xml       # stable GUID
    assert '<script src="main.py"' in xml
    assert "run_singularity_study" in xml


def test_deploy_writes_config_and_settings(tmp_path):
    dest = tmp_path / "extensions"
    ed.deploy(version="252", dest=str(dest))
    folder = dest / "SingularityDetector"

    cfg = json.loads((folder / "sd_config.json").read_text(encoding="utf-8"))
    assert cfg["repo_path"].endswith("Ansys Singularity Recognition ACT")
    assert cfg["ansys_version"] == "252"
    assert "venv_python" in cfg                                # may be None if no venv

    s = json.loads((folder / "sd_study_settings.json").read_text(encoding="utf-8"))
    assert s["mesh_levels"] == 4 and s["ratio"] == 0.75


def test_redeploy_keeps_edited_settings(tmp_path):
    dest = tmp_path / "extensions"
    ed.deploy(version="252", dest=str(dest))
    sp = dest / "SingularityDetector" / "sd_study_settings.json"
    sp.write_text(json.dumps({"mesh_levels": 6, "ratio": 0.8}), encoding="utf-8")
    ed.deploy(version="252", dest=str(dest))                   # again
    s = json.loads(sp.read_text(encoding="utf-8"))
    assert s["mesh_levels"] == 6                               # not clobbered


def test_uninstall_removes_everything(tmp_path):
    dest = tmp_path / "extensions"
    ed.deploy(version="252", dest=str(dest))
    ed.uninstall(version="252", dest=str(dest))
    assert not (dest / "SingularityDetector.xml").exists()
    assert not (dest / "SingularityDetector").exists()


def test_appdata_path_shape():
    p = ed._appdata_ext_dir("261")
    assert p.name == "extensions"
    assert p.parts[-4:] == ("Ansys", "v261", "ACT", "extensions")
