"""Set the project version at its single source of truth.

    python scripts/bump_version.py 0.5.0

The version lives ONLY in optionspilot/__init__.py (__version__). pyproject.toml
derives it dynamically (`[tool.setuptools.dynamic] version = {attr = ...}`), the
app UI reads `optionspilot.__version__`, and packaging + the GitHub Release tag
derive from the same place — so this script edits exactly one line in one file.

Normally invoked via scripts/release.ps1 -Version X.Y.Z. Refuses anything that
isn't a plain X.Y.Z (no pre-release suffixes) - if this project ever needs those,
extend the regex deliberately rather than loosening it by accident.
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

    init = ROOT / "optionspilot" / "__init__.py"
    i_text = init.read_text(encoding="utf-8")
    i_new, n = re.subn(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new}"', i_text)
    if n != 1:
        print("FAIL: could not find __version__ in optionspilot/__init__.py")
        return 1

    init.write_text(i_new, encoding="utf-8")
    print(f"OK: version set to {new} in optionspilot/__init__.py "
          f"(pyproject.toml derives it dynamically)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
