"""Unit tests for devtools.detect_ansys -- no Ansys required.

These build fake install trees on disk and point discovery at them by
manipulating the environment, so they run anywhere.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools import detect_ansys  # noqa: E402


def _fake_install(tmp_path: Path, version: str, *, mechanical: bool = True) -> Path:
    root = tmp_path / ("v" + version)
    (root / "aisol" / "bin" / "winx64").mkdir(parents=True)
    if mechanical:
        (root / "aisol" / "bin" / "winx64" / "AnsysWBU.exe").write_bytes(b"MZ")
    (root / "ansys" / "bin" / "winx64").mkdir(parents=True)
    (root / "ansys" / "bin" / "winx64" / ("ansys" + version + ".exe")).write_bytes(b"MZ")
    (root / "Framework" / "bin" / "Win64").mkdir(parents=True)
    (root / "Framework" / "bin" / "Win64" / "RunWB2.exe").write_bytes(b"MZ")
    return root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip every real Ansys env var so tests are deterministic."""
    for name in list(os.environ):
        u = name.upper()
        if u.startswith(("AWP_ROOT", "ANSYSINC_ROOT")) or (u.startswith("ANSYS") and u.endswith("_DIR")):
            monkeypatch.delenv(name, raising=False)
    # Neutralise the filesystem scan of Program Files.
    monkeypatch.setattr(detect_ansys, "DEFAULT_INSTALL_PARENTS", (), raising=True)


def test_discovers_via_awp_root(tmp_path, monkeypatch):
    root = _fake_install(tmp_path, "252")
    monkeypatch.setenv("AWP_ROOT252", str(root))

    installs = detect_ansys.discover()

    assert len(installs) == 1
    inst = installs[0]
    assert inst.version == "252"
    assert inst.release == "2025 R2"
    assert inst.source == "AWP_ROOT252"
    assert inst.supported is True
    assert inst.usable is True
    assert inst.mechanical_exe.endswith("AnsysWBU.exe")
    assert inst.runwb2_exe.endswith("RunWB2.exe")


def test_ansys_dir_env_points_at_ANSYS_subdir(tmp_path, monkeypatch):
    root = _fake_install(tmp_path, "251")
    # ANSYS<ver>_DIR conventionally points one level below the install root.
    monkeypatch.setenv("ANSYS251_DIR", str(root / "ANSYS"))

    inst = detect_ansys.discover()[0]

    assert inst.version == "251"
    assert Path(inst.root) == root
    assert inst.usable is True


def test_unusable_when_mechanical_exe_missing(tmp_path, monkeypatch):
    root = _fake_install(tmp_path, "241", mechanical=False)
    monkeypatch.setenv("AWP_ROOT241", str(root))

    inst = detect_ansys.discover()[0]

    assert inst.version == "241"
    assert inst.supported is False
    assert inst.usable is False


def test_resolve_auto_picks_oldest_supported(tmp_path, monkeypatch):
    r251 = _fake_install(tmp_path, "251")
    r252 = _fake_install(tmp_path, "252")
    r261 = _fake_install(tmp_path, "261")
    monkeypatch.setenv("AWP_ROOT251", str(r251))
    monkeypatch.setenv("AWP_ROOT252", str(r252))
    monkeypatch.setenv("AWP_ROOT261", str(r261))

    chosen = detect_ansys.resolve("auto")

    assert chosen.version == "251"  # oldest supported, not newest


def test_resolve_auto_ignores_unsupported_when_supported_exists(tmp_path, monkeypatch):
    r241 = _fake_install(tmp_path, "241")   # unsupported
    r252 = _fake_install(tmp_path, "252")   # supported
    monkeypatch.setenv("AWP_ROOT241", str(r241))
    monkeypatch.setenv("AWP_ROOT252", str(r252))

    assert detect_ansys.resolve("auto").version == "252"


def test_resolve_specific_version(tmp_path, monkeypatch):
    r252 = _fake_install(tmp_path, "252")
    monkeypatch.setenv("AWP_ROOT252", str(r252))

    assert detect_ansys.resolve("252").version == "252"
    assert detect_ansys.resolve("v252").version == "252"

    with pytest.raises(RuntimeError):
        detect_ansys.resolve("251")


def test_resolve_raises_when_nothing_usable(monkeypatch):
    assert detect_ansys.discover() == []
    with pytest.raises(RuntimeError):
        detect_ansys.resolve("auto")


def test_no_duplicate_roots_from_env_and_scan(tmp_path, monkeypatch):
    root = _fake_install(tmp_path, "252")
    monkeypatch.setenv("AWP_ROOT252", str(root))
    monkeypatch.setenv("ANSYS252_DIR", str(root / "ANSYS"))
    monkeypatch.setattr(detect_ansys, "DEFAULT_INSTALL_PARENTS", (str(tmp_path),), raising=True)

    installs = detect_ansys.discover()

    assert len(installs) == 1
