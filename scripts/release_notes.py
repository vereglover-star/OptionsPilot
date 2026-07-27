"""Print the CHANGELOG section for a version, for use as GitHub Release notes.

    python scripts/release_notes.py 0.5.0

Finds the first `## ` heading whose text mentions the version and prints through
to the next `## ` heading. Falls back to a one-line pointer if no section
matches (the release workflow also enables GitHub's auto-generated commit notes,
so an empty/short body is harmless). Pure stdlib; safe to run anywhere.
"""

import sys
from pathlib import Path

# The CHANGELOG contains non-cp1252 characters (arrows, en-dashes); emit UTF-8
# regardless of the Windows console's default code page.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
    pass

CHANGELOG = Path(__file__).resolve().parent.parent / "docs" / "CHANGELOG.md"


def extract(version: str) -> str:
    if not CHANGELOG.exists():
        return ""
    out: list[str] = []
    capturing = False
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if capturing:
                break                     # reached the next section
            capturing = version in line
            if capturing:
                out.append(line)
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/release_notes.py X.Y.Z", file=sys.stderr)
        return 1
    version = sys.argv[1]
    body = extract(version)
    if not body:
        body = f"OptionsPilot v{version}. See docs/CHANGELOG.md for details."
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
