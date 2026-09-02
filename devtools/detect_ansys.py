"""Discover installed Ansys releases and resolve one for a run.

External automation layer -> modern CPython 3 (see the two-layer rule in the
`act-builder` skill / docs/two_layer_architecture.md).

Discovery sources, in priority order:
  1. Environment variables ``AWP_ROOT<ver>`` (the canonical Ansys marker).
  2. Environment variables ``ANSYS<ver>_DIR`` (points at ``<root>\\ANSYS``; the
     install root is its parent).
  3. Filesystem scan of the standard Windows install parent
     ``C:\\Program Files\\ANSYS Inc\\v<ver>`` (and ``ANSYSInc_ROOT<ver>`` if set).

A release is only reported as usable when its install root actually exists on
disk and contains the executables we need.

CLI::

    python devtools/detect_ansys.py                # human summary of everything found
    python devtools/detect_ansys.py --json         # machine-readable dump
    python devtools/detect_ansys.py --resolve auto # pick one; 'auto' = OLDEST supported
    python devtools/detect_ansys.py --resolve 252  # pick a specific version tag

Exit code is non-zero when nothing usable is found, or when ``--resolve`` cannot
be satisfied.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Iterable

# Version tags this project is designed for.  Primary target per the master spec
# is 2025 R1 / 251; newer releases are supported through the compatibility layer.
SUPPORTED_VERSIONS = ("251", "252", "261")
PRIMARY_VERSION = "251"

# Human-readable names, extended opportunistically for anything else we find.
RELEASE_NAMES = {
    "241": "2024 R1",
    "242": "2024 R2",
    "251": "2025 R1",
    "252": "2025 R2",
    "261": "2026 R1",
    "262": "2026 R2",
}

DEFAULT_INSTALL_PARENTS = (
    r"C:\Program Files\ANSYS Inc",
    r"C:\Program Files\Ansys Inc",
)


@dataclasses.dataclass
class AnsysInstall:
    version: str                       # "252"
    release: str                       # "2025 R2"
    root: str                          # install root, e.g. C:\Program Files\ANSYS Inc\v252
    source: str                        # how we found it (env var name or "fs-scan")
    supported: bool                    # in SUPPORTED_VERSIONS
    mechanical_exe: str | None = None  # AnsysWBU.exe (Mechanical / Workbench UI+batch host)
    mapdl_exe: str | None = None       # ansys<ver>.exe (MAPDL solver)
    runwb2_exe: str | None = None      # RunWB2.exe (Workbench batch)
    act_extensions_dir: str | None = None  # %APPDATA%\Ansys\v<ver>\ACT\extensions

    @property
    def usable(self) -> bool:
        return bool(self.root) and Path(self.root).is_dir() and self.mechanical_exe is not None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["usable"] = self.usable
        d["is_primary_target"] = self.version == PRIMARY_VERSION
        return d


def _release_name(version: str) -> str:
    if version in RELEASE_NAMES:
        return RELEASE_NAMES[version]
    # Best-effort: "2XY" -> "20XY RZ"
    if len(version) == 3 and version.isdigit():
        return "20{0} R{1}".format(version[:2], version[2])
    return "unknown"


def _candidate_roots_from_env() -> list[tuple[str, str, str]]:
    """Return (version, root, source) tuples from environment variables."""
    found: list[tuple[str, str, str]] = []
    for name, value in os.environ.items():
        if not value:
            continue
        upper = name.upper()
        if upper.startswith("AWP_ROOT") and upper[len("AWP_ROOT"):].isdigit():
            found.append((upper[len("AWP_ROOT"):], value, name))
        elif (
            upper.startswith("ANSYS")
            and upper.endswith("_DIR")
            and upper[len("ANSYS"):-len("_DIR")].isdigit()
        ):
            version = upper[len("ANSYS"):-len("_DIR")]
            # ANSYS<ver>_DIR points at <root>\ANSYS -> take the parent as the root.
            root = str(Path(value).parent)
            found.append((version, root, name))
        elif upper.startswith("ANSYSINC_ROOT") and upper[len("ANSYSINC_ROOT"):].isdigit():
            found.append((upper[len("ANSYSINC_ROOT"):], value, name))
    return found


def _candidate_roots_from_fs() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for parent in DEFAULT_INSTALL_PARENTS:
        p = Path(parent)
        if not p.is_dir():
            continue
        for child in p.iterdir():
            if child.is_dir() and child.name.lower().startswith("v") and child.name[1:].isdigit():
                found.append((child.name[1:], str(child), "fs-scan"))
    return found


def _find_first(root: Path, relative_candidates: Iterable[str]) -> str | None:
    for rel in relative_candidates:
        candidate = root / rel
        if candidate.is_file():
            return str(candidate)
    return None


def _populate_executables(inst: AnsysInstall) -> None:
    root = Path(inst.root)
    inst.mechanical_exe = _find_first(
        root,
        (
            r"aisol\bin\winx64\AnsysWBU.exe",
            r"aisol\Bin\winx64\AnsysWBU.exe",
            r"aisol/bin/linx64/ansyswbu",
        ),
    )
    inst.mapdl_exe = _find_first(
        root,
        (
            r"ansys\bin\winx64\ansys{0}.exe".format(inst.version),
            r"ansys\bin\winx64\ANSYS{0}.exe".format(inst.version),
            r"ansys/bin/linx64/ansys{0}".format(inst.version),
        ),
    )
    inst.runwb2_exe = _find_first(
        root,
        (
            r"Framework\bin\Win64\RunWB2.exe",
            r"Framework/bin/Linux64/runwb2",
        ),
    )
    appdata = os.environ.get("APPDATA")
    if appdata:
        act_dir = Path(appdata) / "Ansys" / ("v" + inst.version) / "ACT" / "extensions"
        inst.act_extensions_dir = str(act_dir)


def discover() -> list[AnsysInstall]:
    """Return all distinct Ansys installs found, newest version first."""
    by_root: dict[str, AnsysInstall] = {}
    # Env first so its `source` label wins over a plain fs-scan for the same root.
    for version, root, source in _candidate_roots_from_env() + _candidate_roots_from_fs():
        root_norm = os.path.normcase(os.path.normpath(root))
        if not Path(root).is_dir():
            continue
        if root_norm in by_root:
            continue
        inst = AnsysInstall(
            version=version,
            release=_release_name(version),
            root=str(Path(root)),
            source=source,
            supported=version in SUPPORTED_VERSIONS,
        )
        _populate_executables(inst)
        by_root[root_norm] = inst
    return sorted(by_root.values(), key=lambda i: i.version, reverse=True)


def resolve(spec: str, installs: list[AnsysInstall] | None = None) -> AnsysInstall:
    """Resolve a ``--resolve`` spec to a single usable install.

    ``spec`` is a version tag ("252") or "auto".  For compatibility validation,
    "auto" deliberately picks the OLDEST usable *supported* release: a pass on a
    newer release does not prove the extension still runs on an older one.
    """
    installs = installs if installs is not None else discover()
    usable = [i for i in installs if i.usable]
    if not usable:
        raise RuntimeError("No usable Ansys installation found (need AnsysWBU.exe under an install root).")

    if spec == "auto":
        supported = [i for i in usable if i.supported]
        pool = supported or usable
        chosen = min(pool, key=lambda i: i.version)
        return chosen

    spec = spec.lstrip("vV")
    for inst in usable:
        if inst.version == spec:
            return inst
    available = ", ".join(sorted(i.version for i in usable)) or "(none)"
    raise RuntimeError("Requested Ansys version {0!r} not usable. Usable: {1}".format(spec, available))


def _print_human(installs: list[AnsysInstall]) -> None:
    if not installs:
        print("No Ansys installations detected.")
        print("Looked at: AWP_ROOT<ver> / ANSYS<ver>_DIR env vars and " + ", ".join(DEFAULT_INSTALL_PARENTS))
        return
    print("Detected Ansys installations (newest first):\n")
    for inst in installs:
        flags = []
        if inst.version == PRIMARY_VERSION:
            flags.append("PRIMARY TARGET")
        if not inst.supported:
            flags.append("not in supported set {0}".format(SUPPORTED_VERSIONS))
        if not inst.usable:
            flags.append("NOT USABLE (no Mechanical exe)")
        tag = ("  [" + "; ".join(flags) + "]") if flags else ""
        print("  {0}  ({1}){2}".format(inst.version, inst.release, tag))
        print("      root         : {0}".format(inst.root))
        print("      found via    : {0}".format(inst.source))
        print("      Mechanical   : {0}".format(inst.mechanical_exe or "-- not found --"))
        print("      MAPDL solver : {0}".format(inst.mapdl_exe or "-- not found --"))
        print("      RunWB2       : {0}".format(inst.runwb2_exe or "-- not found --"))
        print("      ACT ext dir  : {0}".format(inst.act_extensions_dir or "-- unknown --"))
        print("")

    primary = [i for i in installs if i.version == PRIMARY_VERSION and i.usable]
    if not primary:
        print("NOTE: primary target {0} ({1}) is not installed here. Other supported".format(
            PRIMARY_VERSION, _release_name(PRIMARY_VERSION)))
        print("      releases are architecturally supported but 2025 R1 compatibility")
        print("      cannot be locally validated on this machine.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a human summary")
    parser.add_argument(
        "--resolve",
        metavar="auto|<ver>",
        help="resolve to a single usable install and print it ('auto' = oldest supported)",
    )
    args = parser.parse_args(argv)

    installs = discover()

    if args.resolve:
        try:
            chosen = resolve(args.resolve, installs)
        except RuntimeError as exc:
            print("ERROR: {0}".format(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(chosen.to_dict(), indent=2))
        else:
            print("Resolved --resolve={0} -> {1} ({2})".format(args.resolve, chosen.version, chosen.release))
            print("  root       : {0}".format(chosen.root))
            print("  Mechanical : {0}".format(chosen.mechanical_exe))
        return 0

    if args.json:
        print(json.dumps([i.to_dict() for i in installs], indent=2))
    else:
        _print_human(installs)

    return 0 if any(i.usable for i in installs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
