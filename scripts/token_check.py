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

# Contrast floors, from DESIGN_SYSTEM_V2.md §10.2. The design document
# reports these as measured; this recomputes them from the tokens actually in
# the file, so a well-meaning tweak to a ramp step fails here rather than in
# an accessibility audit six months later. 4.5 is body text, 3.0 is large
# text and meaningful non-text (borders, marks, focus indicators).
CONTRAST = [
    ("--ink-primary", "--surface-base", 4.5),
    ("--ink-secondary", "--surface-base", 4.5),
    ("--ink-muted", "--surface-base", 4.5),
    ("--ink-muted", "--surface-raised", 4.5),
    ("--action-primary-text", "--surface-base", 4.5),
    ("--action-primary-ink", "--action-primary-fill", 4.5),
    ("--market-pos-text", "--surface-base", 4.5),
    ("--market-neg-text", "--surface-base", 4.5),
    ("--status-caution", "--surface-base", 4.5),
    ("--action-primary-fill", "--surface-base", 3.0),
    ("--market-pos-mark", "--surface-base", 3.0),
    ("--market-neg-mark", "--surface-base", 3.0),
    ("--border-control", "--surface-base", 3.0),
    ("--focus-ring", "--surface-base", 3.0),
    # The dual focus ring, and the reason it is dual: the ring alone on the
    # primary fill measures 2.75:1 and fails, so the gap carries it.
    ("--focus-ring", "--focus-gap", 3.0),
    ("--focus-gap", "--action-primary-fill", 3.0),
]


def resolve(defs: dict[str, str], name: str, depth: int = 0) -> str | None:
    """Follow a token through any var() chain to a literal colour."""
    if depth > 8 or name not in defs:
        return None
    value = defs[name].strip()
    chain = re.fullmatch(r"var\(\s*(--[a-z0-9-]+)\s*\)", value)
    return resolve(defs, chain.group(1), depth + 1) if chain else value


def luminance(hex_colour: str) -> float | None:
    h = hex_colour.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return None
    parts = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        parts.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = parts
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float | None:
    la, lb = luminance(a), luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


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


#: Radius tokens, smallest first. Concentricity is judged on this order rather
#: than on the pixel values, so retuning a radius cannot silently invert the
#: relationship the rule is about.
RADIUS_ORDER = ("--radius-sm", "--radius-med", "--radius-lg")


def check_instrument(text: str) -> list[str]:
    """The two rules that make an instrument an instrument (M3-C4).

    Both are the kind of thing that reads as a harmless tidy-up in review and
    changes what the component *is*:

    * **No border.** Separation between instruments is space and surface. A
      border re-creates the "nine panels of identical visual weight" fault
      `UI_V2_DESIGN.md` §5.1 names as the current Dashboard's second problem —
      the layout stops expressing priority and the user has to supply it.
    * **Concentric radii.** The recessed interior's corner must be *smaller*
      than its housing's. Equal or larger nested corners make the interior look
      pasted on rather than recessed, which is the whole visual claim.
    """
    problems: list[str] = []

    span = block_span(text, ".ins")
    if span is None:
        return [".ins is not defined — the instrument component is missing."]
    body = text[span[0]:span[1]]
    if re.search(r"(?<!-)\bborder\s*:(?!\s*0)", body):
        problems.append(
            ".ins declares a border. An instrument is separated by surface and "
            "space, not by an outline (UI_V2_VISUAL_EXPLORATION.md, "
            "'Panel treatment'). A bordered instrument is the legacy .panel.")

    def radius_of(selector: str) -> str | None:
        found = block_span(text, selector)
        if found is None:
            return None
        m = re.search(r"border-radius:\s*var\(\s*(--radius-[a-z]+)\s*\)",
                      text[found[0]:found[1]])
        return m.group(1) if m else None

    outer, inner = radius_of(".ins"), radius_of(".ins-well")
    if outer is None:
        problems.append(".ins has no tokenised border-radius.")
    elif inner is None:
        problems.append(".ins-well has no tokenised border-radius.")
    elif outer in RADIUS_ORDER and inner in RADIUS_ORDER:
        if RADIUS_ORDER.index(inner) >= RADIUS_ORDER.index(outer):
            problems.append(
                f".ins-well ({inner}) is not smaller than .ins ({outer}). "
                f"Nested corners that match make the interior look pasted on "
                f"rather than recessed.")
    return problems


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

    # 5b. the instrument's own invariants (M3-C4)
    #
    # The Flight Deck's one region primitive, and every later milestone reuses
    # it — so the two rules that make it read as an instrument rather than as
    # another panel are asserted here rather than left to review.
    problems += check_instrument(text)

    # 6. contrast, recomputed from the tokens in the file
    root = block_span(text, ":root")
    defs: dict[str, str] = {}
    if root:
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text[root[0]:root[1]]):
            defs[m.group(1)] = m.group(2)
    checked = 0
    for fg, bg, floor in CONTRAST:
        a, b = resolve(defs, fg), resolve(defs, bg)
        if a is None or b is None:
            problems.append(
                f"contrast: {fg} on {bg} — token missing or unresolvable.")
            continue
        ratio = contrast(a, b)
        if ratio is None:
            problems.append(
                f"contrast: {fg} ({a}) on {bg} ({b}) — not a plain hex colour.")
        elif ratio < floor:
            problems.append(
                f"contrast: {fg} on {bg} is {ratio:.2f}:1, floor is {floor}:1.")
        else:
            checked += 1

    if not problems:
        print(f"OK: {checked} contrast floors met; token layering clean; "
              f"{legacy_fs} legacy type uses "
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
