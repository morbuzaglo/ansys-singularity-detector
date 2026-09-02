"""Licensing preflight (spec S20).

External automation layer -> CPython 3.

Checks that a license server is reachable and that the feature(s) a run needs
are actually available, BEFORE launching Mechanical. A license failure must be
reported as *infrastructure failure*, never misclassified as a test failure.

    python devtools/licensing_preflight.py [--ansys-version auto] [--need mech_solve_level1 agppi ...]

Exit codes: 0 = all needed features available; 3 = a needed feature is missing or
the server is unreachable; 2 = could not run the check (no lmutil, etc.).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from devtools import detect_ansys  # noqa: E402

# Features a typical run of this project consumes.  Names are FlexLM increments
# as seen via `lmutil lmstat`; keep this list conservative and documented.
DEFAULT_NEEDED = (
    "acdi_adprepost",       # Mechanical pre/post (Mechanical UI / ACT session)
    "mech_preppost_level1", # Mechanical pre/post entitlement (bundle-dependent)
    "mech_solve_level1",    # structural solve entitlement (bundle-dependent)
    "ansys",               # MAPDL solver increment
    "agppi",               # ACT / customization (needed once a scripted extension loads)
)

_USERS_RE = re.compile(
    r"Users of (?P<feat>[A-Za-z0-9_\-]+):\s+\(Total of (?P<total>\d+) licenses? issued;"
    r"\s+Total of (?P<used>\d+) licenses? in use\)"
)


def _find_lmutil(inst: detect_ansys.AnsysInstall | None) -> str | None:
    candidates = []
    if inst and inst.root:
        candidates += [
            Path(inst.root) / "licensingclient" / "winx64" / "lmutil.exe",
            Path(inst.root) / "licensingclient" / "linx64" / "lmutil",
        ]
    lic_dir = os.environ.get("ANSYSLIC_DIR")
    if lic_dir:
        candidates += [
            Path(lic_dir) / "winx64" / "lmutil.exe",
            Path(lic_dir) / "linx64" / "lmutil",
        ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _license_spec() -> str:
    return (
        os.environ.get("ANSYSLMD_LICENSE_FILE")
        or os.environ.get("LM_LICENSE_FILE")
        or "1055@localhost"
    )


def query(lmutil: str, spec: str) -> dict[str, tuple[int, int]]:
    """Return {feature: (total, in_use)} from `lmutil lmstat -a`."""
    out = subprocess.run(
        [lmutil, "lmstat", "-a", "-c", spec],
        capture_output=True, text=True, timeout=120,
    )
    text = out.stdout + "\n" + out.stderr
    if "Cannot connect to license server" in text or "lmgrd is not running" in text:
        raise RuntimeError("cannot connect to license server {0}".format(spec))
    feats: dict[str, tuple[int, int]] = {}
    for m in _USERS_RE.finditer(text):
        feats[m.group("feat")] = (int(m.group("total")), int(m.group("used")))
    if not feats:
        raise RuntimeError("no feature lines parsed from lmstat output (server up but empty?)")
    return feats


def preflight(version: str, needed: list[str]) -> tuple[bool, list[str]]:
    inst = None
    try:
        inst = detect_ansys.resolve(version)
    except RuntimeError:
        pass
    lmutil = _find_lmutil(inst)
    if not lmutil:
        raise FileNotFoundError("lmutil not found (looked under install root + ANSYSLIC_DIR)")
    spec = _license_spec()
    feats = query(lmutil, spec)

    lines = ["license server : {0}  (OK)".format(spec)]
    ok = True
    for feat in needed:
        if feat not in feats:
            lines.append("  {0:24s}: NOT OFFERED by this server".format(feat))
            ok = False
            continue
        total, used = feats[feat]
        avail = total - used
        mark = "ok" if avail > 0 else "NONE FREE"
        if avail <= 0:
            ok = False
        lines.append("  {0:24s}: {1} free / {2} total   [{3}]".format(feat, avail, total, mark))
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ansys-version", default="auto")
    p.add_argument("--need", nargs="*", default=list(DEFAULT_NEEDED),
                   help="FlexLM feature names that must be available")
    p.add_argument("--any-of", nargs="*", default=None,
                   help="pass if AT LEAST ONE of these is available (bundle-agnostic solve check)")
    args = p.parse_args(argv)

    try:
        ok, lines = preflight(args.ansys_version, args.need)
    except (RuntimeError, FileNotFoundError) as exc:
        print("PREFLIGHT ERROR: {0}".format(exc), file=sys.stderr)
        return 2 if isinstance(exc, FileNotFoundError) else 3

    for ln in lines:
        print(ln)

    if args.any_of:
        try:
            _, feats_lines = preflight(args.ansys_version, args.any_of)
        except Exception:
            feats_lines = []
        got_one = any("free" in ln and "NONE FREE" not in ln for ln in feats_lines)
        print("any-of {0}: {1}".format(args.any_of, "satisfied" if got_one else "NOT satisfied"))
        ok = ok and got_one

    print("\nPREFLIGHT: {0}".format("PASS" if ok else "FAIL"))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
