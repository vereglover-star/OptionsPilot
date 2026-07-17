"""Sync the project version across the two files that currently must agree
by hand: pyproject.toml and optionspilot/__init__.py.

    python scripts/bump_version.py 0.2.0

Normally invoked via scripts/release.ps1 -Version X.Y.Z. Refuses anything
that isn't a plain X.Y.Z (no pre-release suffixes) - if this project ever
needs those, extend the regex deliberately rather than loosening it by
accident.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def main() -> int:
    if len(sys.argv) != 2 or not VERSION_RE.match(sys.argv[1]):
        print("usage: python scripts/bump_version.py X.Y.Z")
        return 1
    new = sys.argv[1]

    pyproject = ROOT / "pyproject.toml"
    init = ROOT / "optionspilot" / "__init__.py"

    p_text = pyproject.read_text(encoding="utf-8")
    p_new, n1 = re.subn(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{new}"', p_text)
    if n1 != 1:
        print("FAIL: could not find a single `version = \"...\"` line in pyproject.toml")
        return 1

    i_text = init.read_text(encoding="utf-8")
    i_new, n2 = re.subn(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new}"', i_text)
    if n2 != 1:
        print("FAIL: could not find __version__ in optionspilot/__init__.py")
        return 1

    pyproject.write_text(p_new, encoding="utf-8")
    init.write_text(i_new, encoding="utf-8")
    print(f"OK: version set to {new} in pyproject.toml and optionspilot/__init__.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
