"""Static design-token gate for `optionspilot/ui/static/index.html`.

The frontend has no automated test coverage, so a token regression is
invisible until someone looks at the screen. This is the cheapest possible
guard: it never launches a browser, it reads one file, and it enforces the
four things that keep the three-layer token architecture from rotting.

1. **Layering.** A component references the SEMANTIC layer and never a
   primitive. The neutral ramp (`--n-*`) may only be read inside `:root`,
   where the semantic tokens are derived from it. This is the rule that
   makes a theme change a token change rather than an audit, and it is the
   one people break first because reaching for `--n-800` is easy.

2. **Every reference resolves.** A `var()` naming a property nothing
   defines silently renders as nothing. M0-C4 renamed `--muted` and a
   browser check that read it by name started comparing the empty string to
   the empty string and passing vacuously — this catches that class before
   it reaches a browser at all.

3. **No hardcoded font sizes.** Type comes from the scale, in rem, so the
   OS text-size setting and the app's large-text mode work by definition.

4. **Two ratchets, for the migration debt that could not be paid in M0.**
   Neither number may grow. Both retire per destination in M3-M6, where the
   markup is re-authored and the layout can actually be verified:

   - `--legacy-fs-md` — 13px is not a step in the frozen scale. Collapsing
     its uses into caption or body is a density change nothing in the suite
     can check (the surfaces at risk are the option chain and the provider
     dashboard at 1280px).
   - off-scale rhythm — padding/margin/gap values that are not a step on
     the spacing scale. Same reason: rounding 6px to 8px across the product
     moves every component.

   Values above 48px are excluded from the rhythm count deliberately. A
   110px margin is a layout dimension, not rhythm, and a spacing scale has
   nothing to say about it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "optionspilot" / "ui" / "static" / "index.html"

# The spacing scale, as values. Kept here rather than parsed so the gate
# fails loudly if someone edits a step rather than silently accepting it.
SCALE_PX = {0, 4, 8, 12, 16, 24, 32, 48}

# ── ratchets ─────────────────────────────────────────────────────────────
# These may only ever DECREASE. Lower them in the same commit that retires
# the uses; never raise one to make a build pass.
MAX_LEGACY_FS_MD = 51
MAX_OFF_SCALE_RHYTHM = 313

# Names retired during M0. A reappearance means someone copied an old rule.
RETIRED = (
    "--fs-xs", "--fs-sm", "--fs-md", "--fs-base", "--fs-lg", "--fs-xl",
    "--fs-2xl", "--fs-3xl", "--fs-hero",
    "--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6",
    "--r-sm", "--r-md", "--r-lg", "--r-pill", "--t-fast", "--t-med",
    "--sh-1", "--sh-2", "--sh-3",
    "--page", "--surface", "--surface-2", "--surface-3", "--ink", "--ink-2",
    "--muted", "--grid", "--baseline", "--ring",
    "--accent", "--accent-soft", "--good", "--good-soft", "--bad",
    "--bad-soft", "--warn", "--warn-soft",
)

RHYTHM_DECL = re.compile(
    r"\b((?:padding|margin|gap)(?:-(?:top|right|bottom|left|inline|block|x|y))?)"
    r"\s*:\s*([^;{}]+);"
)


def block_span(text: str, selector: str) -> tuple[int, int] | None:
    """Character range of `selector { ... }`, brace-matched."""
    start = text.find(selector + " {")
    if start < 0:
        start = text.find(selector + "{")
        if start < 0:
            return None
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return (start, i)
    return None


def check() -> list[str]:
    text = INDEX.read_text(encoding="utf-8")
    problems: list[str] = []

    # 1. layering — the ramp is readable only where semantics are derived
    allowed = [s for s in (block_span(text, ":root"),
                           block_span(text, "html.market-alt")) if s]
    for m in re.finditer(r"var\(\s*(--n-[0-9]{3})\s*\)", text):
        if not any(lo <= m.start() <= hi for lo, hi in allowed):
            line = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"line {line}: component reads the primitive {m.group(1)}. "
                f"Reference a semantic token instead (--surface-*, --ink-*, "
                f"--border-*, --action-*, --market-*, --status-*, --focus-*).")

    # 2. every reference resolves
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
    for name in sorted(set(re.findall(r"var\(\s*(--[a-z0-9-]+)", text))):
        if name not in defined:
            problems.append(f"var({name}) is referenced but never defined.")

    # 3. no hardcoded font sizes
    for m in re.finditer(r"font-size:\s*([0-9.]+)px", text):
        line = text.count("\n", 0, m.start()) + 1
        problems.append(
            f"line {line}: hardcoded font-size {m.group(1)}px. "
            f"Use a --text-* role so large-text mode and OS scaling work.")

    # 4. retired names
    for name in RETIRED:
        if re.search(rf"var\(\s*{re.escape(name)}\s*\)", text):
            problems.append(
                f"{name} was retired during M0 and is referenced again. "
                f"See docs/UI_MIGRATION_TRACKER.md §6 for its replacement.")

    # 5. ratchets
    legacy_fs = len(re.findall(r"var\(\s*--legacy-fs-md\s*\)", text))
    if legacy_fs > MAX_LEGACY_FS_MD:
        problems.append(
            f"--legacy-fs-md used {legacy_fs} times, ratchet is "
            f"{MAX_LEGACY_FS_MD}. The 13px step may only shrink.")

    off_scale = 0
    for m in RHYTHM_DECL.finditer(text):
        for value in re.findall(r"([0-9]+)px", m.group(2)):
            n = int(value)
            if n <= 48 and n not in SCALE_PX:
                off_scale += 1
    if off_scale > MAX_OFF_SCALE_RHYTHM:
        problems.append(
            f"off-scale rhythm values: {off_scale}, ratchet is "
            f"{MAX_OFF_SCALE_RHYTHM}. Use a --space-* step.")

    if not problems:
        print(f"OK: token layering clean; {legacy_fs} legacy type uses "
              f"(<= {MAX_LEGACY_FS_MD}), {off_scale} off-scale rhythm values "
              f"(<= {MAX_OFF_SCALE_RHYTHM}).")
        if legacy_fs < MAX_LEGACY_FS_MD or off_scale < MAX_OFF_SCALE_RHYTHM:
            print("     Debt fell — lower the ratchets in scripts/token_check.py.")
    return problems


def main() -> int:
    if not INDEX.exists():
        print(f"FAIL: {INDEX} not found.")
        return 1
    problems = check()
    if problems:
        print(f"FAIL: {len(problems)} design-token problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
