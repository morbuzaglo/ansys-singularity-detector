"""Deploy the SingularityDetector ACT extension into Ansys (spec S19).

External automation layer -> CPython 3.

The source repo is authoritative -- NEVER edit the installed copy.  This tool
assembles the correct ACT layout

    <ACT extensions dir>/
      SingularityDetector.xml
      SingularityDetector/
        visualization.py
        images/result.bmp

from repo `extension/` into
`%APPDATA%\\Ansys\\v<ver>\\ACT\\extensions` (or the install `Addins` tree, or a
folder you pass), using the one stable GUID in the XML.

    python devtools/extension_deployer.py                 # copy into %APPDATA% for 252
    python devtools/extension_deployer.py --ansys-version 261
    python devtools/extension_deployer.py --dest "D:\\act_dev"
    python devtools/extension_deployer.py --link          # junction the folder (edit-in-place)
    python devtools/extension_deployer.py --uninstall

After deploying: start Mechanical, Extensions -> Manage Extensions -> tick
"SingularityDetector" (needs an ACT licence).  A clean restart picks up source
changes; there is no reliable hot-reload.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _rmtree(p: Path) -> None:
    def _onerror(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass
    shutil.rmtree(p, onerror=_onerror)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import detect_ansys  # noqa: E402

EXT_NAME = "SingularityDetector"
SRC = REPO / "extension"
# the extension script + every Mechanical-side (IronPython 2.7) module it imports
EXT_FILES = ["main.py", "visualization.py", "act_study.py", "mech_env.py",
             "mesh_manager.py", "result_extractor.py", "model_setup.py"]
EXT_DIRS = ["images"]
EXT_XML = "SingularityDetector.xml"

DEFAULT_STUDY_SETTINGS = {"mesh_levels": 4, "ratio": 0.75,
                          "confidence_threshold": 70.0}


def _appdata_ext_dir(version: str) -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Ansys" / ("v" + version) / "ACT" / "extensions"


def resolve_dest(version: str, dest: str | None) -> Path:
    if dest:
        return Path(dest)
    return _appdata_ext_dir(version)


def deploy(version: str = "auto", dest: str | None = None, link: bool = False) -> Path:
    if version == "auto":
        version = detect_ansys.resolve("auto").version
    d = resolve_dest(version, dest)
    d.mkdir(parents=True, exist_ok=True)

    xml_dst = d / EXT_XML
    folder_dst = d / EXT_NAME

    shutil.copy2(SRC / EXT_XML, xml_dst)

    # keep a user-edited settings file across a redeploy
    kept_settings = None
    prev = folder_dst / "sd_study_settings.json"
    if prev.is_file() and not (folder_dst.is_symlink() or _is_junction(folder_dst)):
        try:
            kept_settings = prev.read_text(encoding="utf-8")
        except Exception:
            kept_settings = None

    if folder_dst.exists() or folder_dst.is_symlink():
        if folder_dst.is_symlink() or (os.name == "nt" and folder_dst.is_dir()
                                       and _is_junction(folder_dst)):
            _rmlink(folder_dst)
        else:
            _rmtree(folder_dst)

    if link:
        _mklink(folder_dst, SRC)          # whole extension/ dir; harmless extra files
        note = "junction -> {0}".format(SRC)
    else:
        folder_dst.mkdir(parents=True, exist_ok=True)
        for f in EXT_FILES:
            shutil.copy2(SRC / f, folder_dst / f)
        for sub in EXT_DIRS:
            if (SRC / sub).is_dir():
                shutil.copytree(SRC / sub, folder_dst / sub, dirs_exist_ok=True)
        note = "copied"

    # tell the extension where the analysis venv + repo live (spec: extension is
    # IronPython with no numpy -> it shells out to the venv for analysis)
    venv_py = REPO / ".venv" / "Scripts" / ("python.exe" if os.name == "nt" else "python")
    cfg = {
        "repo_path": str(REPO),
        "venv_python": str(venv_py) if venv_py.exists() else None,
        "deployed_at": _now(),
        "ansys_version": version,
    }
    cfg_target = SRC if link else folder_dst
    (cfg_target / "sd_config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    settings_target = cfg_target / "sd_study_settings.json"
    if kept_settings is not None:
        settings_target.write_text(kept_settings, encoding="utf-8")
    elif not settings_target.is_file():
        settings_target.write_text(json.dumps(DEFAULT_STUDY_SETTINGS, indent=2), encoding="utf-8")

    print("Deployed {0} ({1}) to {2}".format(EXT_NAME, note, d))
    print("  {0}".format(xml_dst))
    print("  {0}\\".format(folder_dst))
    print("\nIn Mechanical: Extensions -> Manage Extensions -> enable "
          "'SingularityDetector' (ACT licence required).")
    return d


def uninstall(version: str = "auto", dest: str | None = None) -> None:
    if version == "auto":
        try:
            version = detect_ansys.resolve("auto").version
        except Exception:
            version = "252"
    d = resolve_dest(version, dest)
    removed = []
    xml = d / EXT_XML
    folder = d / EXT_NAME
    if xml.is_file():
        xml.unlink(); removed.append(str(xml))
    if folder.is_symlink() or (os.name == "nt" and folder.is_dir() and _is_junction(folder)):
        _rmlink(folder); removed.append(str(folder) + " (link)")
    elif folder.is_dir():
        _rmtree(folder); removed.append(str(folder))
    print("Removed:" if removed else "Nothing to remove at {0}".format(d))
    for r in removed:
        print("  " + r)


# --- Windows junction helpers -------------------------------------------
def _mklink(link_path: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
                       check=True, capture_output=True, text=True)
    else:
        os.symlink(target, link_path)


def _rmlink(link_path: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(link_path)], check=False,
                       capture_output=True, text=True)
    else:
        link_path.unlink()


def _is_junction(p: Path) -> bool:
    try:
        return bool(os.readlink(str(p)))
    except OSError:
        try:
            attrs = os.stat(str(p)).st_file_attributes  # type: ignore[attr-defined]
            return bool(attrs & 0x400)                   # FILE_ATTRIBUTE_REPARSE_POINT
        except Exception:
            return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ansys-version", default="auto")
    p.add_argument("--dest", default=None, help="explicit target extensions dir")
    p.add_argument("--link", action="store_true", help="junction the folder for edit-in-place")
    p.add_argument("--uninstall", action="store_true")
    a = p.parse_args(argv)
    if a.uninstall:
        uninstall(a.ansys_version, a.dest)
        return 0
    deploy(a.ansys_version, a.dest, a.link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
