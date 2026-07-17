"""Static check: every `$("id")` reference in index.html must resolve to a
real `id="..."` element in the same file.

Previously an ad hoc one-off script (see docs/CONTRIBUTING.md history);
committed here so it runs the same way every time via scripts/docs.ps1 or
scripts/verify.ps1. There is no automated browser test suite for the
frontend (see docs/AI_CONTEXT.md "Technical debt"), so this and
scripts/browser_check.py are the only automated guards `static/index.html`
has.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "optionspilot" / "ui" / "static" / "index.html"


def main() -> int:
    text = HTML.read_text(encoding="utf-8")
    declared = set(re.findall(r'id="([^"]+)"', text))
    referenced = set(re.findall(r'\$\("([^"]+)"\)', text))
    missing = sorted(referenced - declared)
    if missing:
        print(f'FAIL: {len(missing)} $("id") reference(s) with no matching id="...":')
        for name in missing:
            print(f"  - {name}")
        return 1
    print(f'OK: all {len(referenced)} $("id") references resolve '
          f"({len(declared)} declared ids in {HTML.relative_to(ROOT)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
