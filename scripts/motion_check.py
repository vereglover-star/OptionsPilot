"""Static motion gate for `optionspilot/ui/static/index.html`.

`DESIGN_SYSTEM_V2.md` §7.3 is a CLOSED catalogue of seventeen animations and
§7.4 is an explicit prohibition list; the Flight Deck freeze (F-15) then uses
thirteen of the seventeen. A closed catalogue is only closed if something
checks, so this reads the stylesheet and enforces the parts of it that are
statically decidable.

What it can decide, and does:

1. **Durations come from the scale.** A transition timed in a literal
   `.18s` is outside the system, and is also invisible to the reduced-motion
   rules that key off the tokens.

2. **The chart canvas is exempt from the whole system, and must stay
   exempt.** No transition, transform or animation may be attached to it.
   This is not stylistic: re-clamping the chart from a ResizeObserver was
   tried in this repository and reverted, because it snapped a user's manual
   price-axis drag back mid-gesture. Anything that animates the canvas is in
   the same family.

3. **Keyframes are on an allow-list.** A new `@keyframes` is a new animation,
   which §7.3 says requires a decision rather than an implementation.

4. **Reduced motion is honoured**, and neutralises both animation and
   transition rather than only one.

What it cannot decide, and therefore ratchets instead: whether a given
transition sits on a value-bearing element, and whether an existing
attention-seeking animation has been removed yet. Three keyframes are
prohibited by §7.4 and F-15 but still present — `shimmer` (F-15 omits the
skeleton shimmer: a shimmering instrument reads as a malfunctioning one),
`gdpulse` and `gdnudge` (§7.4: nothing animates to attract attention). They
retire with the surfaces that own them — skeletons in M3, the guided tour in
M7 — because removing them is a visible change to a surface this milestone
is not rebuilding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "optionspilot" / "ui" / "static" / "index.html"

# Ratchets. May only ever DECREASE.
MAX_HARDCODED_DURATIONS = 3
MAX_PROHIBITED_KEYFRAMES = 3

# Every @keyframes allowed to exist. Adding a row is a design decision, not a
# code change: check it against DESIGN_SYSTEM_V2.md §7.3 first.
ALLOWED_KEYFRAMES = {
    "chspin":    "indeterminate progress on the chart loader (M-16 family)",
    "gdcard":    "guided-tour card enter (M-3)",
    "gdcheck":   "guided-tour step completion (M-10)",
    "gdpop":     "guided-tour spotlight enter (M-3)",
    "gdtip":     "contextual tip enter (M-3)",
    "pop":       "toast enter (M-13)",
    "tabin":     "destination content cross-fade (M-1)",
    "updIndet":  "indeterminate update progress",
    # Present but prohibited — see the module docstring. Ratcheted, not
    # allowed: PROHIBITED_KEYFRAMES holds them so the count can only fall.
}
PROHIBITED_KEYFRAMES = {
    "shimmer": "F-15 omits the skeleton shimmer — retire with M3's skeletons",
    "gdpulse": "§7.4: nothing animates to attract attention — retire with M7",
    "gdnudge": "§7.4: nothing animates to attract attention — retire with M7",
}

# Selectors that ARE the chart canvas, as opposed to controls near it.
CANVAS_SELECTOR = re.compile(r"(^|[\s,>+~])(canvas\b|#ch-chart\b|#chart-canvas\b)")

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
DURATION = re.compile(r"\b\d*\.?\d+m?s\b")

# Either half of the reduced-motion switch: the media query, or the
# `html.gd-nomotion` class this app routes both sources through.
REDUCED_MOTION = re.compile(
    r"(?:@media[^{]*prefers-reduced-motion[^{]*|[^{}\n]*gd-nomotion[^{]*)\{")


def check() -> list[str]:
    text = INDEX.read_text(encoding="utf-8")
    problems: list[str] = []

    # Reduced motion in this app is ONE switch: `html.gd-nomotion` is set from
    # either the OS preference or the in-app toggle, so the global sweep is a
    # class rather than a media query. A gate that looked only for @media
    # would have reported the app as ignoring the preference while it was in
    # fact honouring it through a better mechanism. These regions are also
    # excluded from the duration count below: their whole job is to force a
    # literal near-zero duration, and there is no token for "off".
    reduced_spans: list[tuple[int, int]] = []
    for m in re.finditer(REDUCED_MOTION, text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    reduced_spans.append((m.start(), i))
                    break
            i += 1

    def in_reduced(idx: int) -> bool:
        return any(lo <= idx <= hi for lo, hi in reduced_spans)

    # 1. durations from the scale
    hardcoded = 0
    for m in re.finditer(r"transition(?:-duration)?\s*:\s*([^;{}]+)", text):
        if in_reduced(m.start()):
            continue
        value = m.group(1)
        literals = [d for d in DURATION.findall(value) if float(
            d.rstrip("ms") if d.endswith("ms") else d.rstrip("s")) > 0]
        if literals and "var(--dur-" not in value:
            hardcoded += len(literals)
    if hardcoded > MAX_HARDCODED_DURATIONS:
        problems.append(
            f"{hardcoded} hardcoded transition duration(s), ratchet is "
            f"{MAX_HARDCODED_DURATIONS}. Use --dur-instant/fast/medium.")

    # 2. the chart canvas stays exempt
    for m in RULE.finditer(text):
        selector, body = m.group(1), m.group(2)
        if CANVAS_SELECTOR.search(selector) and re.search(
                r"\b(transition|animation|transform)\s*:", body):
            line = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"line {line}: motion attached to the chart canvas "
                f"({selector.strip()[:60]}). The canvas owns its own physics "
                f"and is exempt from this design system.")

    # 3. keyframes on the allow-list
    found = set(re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", text))
    unknown = found - set(ALLOWED_KEYFRAMES) - set(PROHIBITED_KEYFRAMES)
    for name in sorted(unknown):
        problems.append(
            f"@keyframes {name} is not in the catalogue. A new animation is a "
            f"decision (DESIGN_SYSTEM_V2.md §7.3), not an implementation — add "
            f"it to ALLOWED_KEYFRAMES with the catalogue entry it serves.")
    still_present = sorted(found & set(PROHIBITED_KEYFRAMES))
    if len(still_present) > MAX_PROHIBITED_KEYFRAMES:
        problems.append(
            f"{len(still_present)} prohibited keyframes, ratchet is "
            f"{MAX_PROHIBITED_KEYFRAMES}: {', '.join(still_present)}")

    # 4. reduced motion neutralises both channels. The file carries several
    # narrow blocks that switch off one animation each; what matters is that
    # ONE of them is the global sweep covering both channels.
    if not reduced_spans:
        problems.append(
            "no reduced-motion block found (neither a prefers-reduced-motion "
            "media query nor the html.gd-nomotion switch).")
    else:
        bodies = [text[lo:hi] for lo, hi in reduced_spans]
        for channel in ("animation-duration", "transition-duration"):
            if not any(channel in b for b in bodies):
                problems.append(
                    f"no reduced-motion block neutralises {channel}. "
                    f"Reduced motion removes MOVEMENT, never FEEDBACK — both "
                    f"channels must be handled.")

    if not problems:
        print(f"OK: {len(found)} keyframes, all catalogued "
              f"({len(still_present)} prohibited, <= {MAX_PROHIBITED_KEYFRAMES}); "
              f"{hardcoded} hardcoded duration(s) (<= {MAX_HARDCODED_DURATIONS}); "
              f"chart canvas clean; reduced motion honoured.")
        if hardcoded < MAX_HARDCODED_DURATIONS or len(
                still_present) < MAX_PROHIBITED_KEYFRAMES:
            print("     Debt fell — lower the ratchets in scripts/motion_check.py.")
    return problems


def main() -> int:
    if not INDEX.exists():
        print(f"FAIL: {INDEX} not found.")
        return 1
    problems = check()
    if problems:
        print(f"FAIL: {len(problems)} motion problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
