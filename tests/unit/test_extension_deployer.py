"""Unit tests for devtools.extension_deployer -- deploy to a temp dir, no Ansys."""

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
    assert (folder / "visualization.py").is_file()
    assert (folder / "images" / "result.bmp").is_file()

    # the deployed folder must NOT carry the Mechanical-side milestone scripts
    for junk in ("study_runner.py", "mesh_manager.py", "model_setup.py"):
        assert not (folder / junk).exists()

    xml = (dest / "SingularityDetector.xml").read_text(encoding="utf-8")
    assert "f27a27e0-ca49-4fa6-87f4-ef972a94c1d7" in xml       # stable GUID
    assert "<script src=\"visualization.py\"" in xml


def test_redeploy_is_idempotent(tmp_path):
    dest = tmp_path / "extensions"
    ed.deploy(version="252", dest=str(dest))
    ed.deploy(version="252", dest=str(dest))                    # again
    assert (dest / "SingularityDetector" / "visualization.py").is_file()


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
