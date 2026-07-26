"""Per-trade category scorecard (AI Coach 2.0, Phase 1).

Turns the checks a `TradeCoach` review already gathers — the before/during
`Finding`s and the mistake-taxonomy tags — into a fixed set of named scoring
categories (Entry Quality, Risk Management, Patience, …), each with a 0–100
score, a data-referenced explanation, and one concrete suggestion.

Deliberately additive and deterministic: it introduces **no new data capture**.
Every category is derived from signals the review has already computed, so the
scorecard is reproducible and testable, and adding it cannot change what the
review observes or when it runs.

Scoring stays process-based (consistent with `coach.py`'s philosophy): a
category starts at 100 and loses points for the specific mistakes that belong to
it. `context_only` categories that depend on the near-entry analysis snapshot
report `score=None` ("not enough data") when no snapshot was captured, rather
than a misleading perfect score. Outcome/order categories (Risk, Exit,
Reward/Risk, discipline) are always assessable — the *absence* of a violation is
itself a valid positive reading.
"""

from __future__ import annotations

from dataclasses import dataclass

from optionspilot.coach.coach import MISTAKES, Finding

# category -> spec. `checks` are the Finding.check names that inform the
# explanation; `mistakes` maps each relevant taxonomy tag to its point penalty;
# `context_only` marks categories that need the near-entry snapshot to assess;
# `clean`/`suggestion` are the text used when the category is clean.
CATEGORY_SPEC: dict[str, dict] = {
    "Entry Quality": {
        "context_only": True,
        "checks": {"setup quality", "entry not chased", "volume sufficient"},
        "mistakes": {"no_confirmation": 30, "chased_entry": 22},
        "clean": "Entered on a setup the analysis agreed with, not an extended move.",
        "suggestion": "Keep waiting for a confirmed, agreed-direction setup before entering.",
    },
    "Exit Quality": {
        "context_only": False,
        "checks": set(),
        "mistakes": {"held_loser": 30, "cut_winner_early": 18},
        "clean": "Closed without riding the loser past invalidation or cutting a runner short.",
        "suggestion": "Keep cutting losers at invalidation and letting winners run with a trail.",
    },
    "Risk Management": {
        "context_only": False,
        "checks": {"stop in place", "stop discipline"},
        "mistakes": {"no_stop": 40, "moved_stop": 30},
        "clean": "Protected the position with a resting stop and never widened it.",
        "suggestion": "Keep placing a resting stop within a minute of the fill, and never widen it.",
    },
    "Position Size": {
        "context_only": True,
        "checks": {"position sizing"},
        "mistakes": {"oversized": 35},
        "clean": "Sized so a full premium loss is an annoyance, not an event.",
        "suggestion": "Keep single-trade premium outlay in the low single-digit percent of equity.",
    },
    "Emotional Discipline": {
        "context_only": False,
        "checks": {"emotional state"},
        "mistakes": {"revenge_trade": 32, "moved_stop": 16, "averaged_down": 22},
        "clean": "No revenge entry, stop-moving, or averaging-down — steady execution.",
        "suggestion": "Step away after a stop-out; never move a stop or average a loser.",
    },
    "Rule Following": {
        "context_only": False,
        "checks": {"stop in place", "no averaging down"},
        "mistakes": {"no_stop": 26, "moved_stop": 20, "averaged_down": 22},
        "clean": "Followed the basic rules: stop placed, no adds into weakness.",
        "suggestion": "Treat 'stop before anything else' and 'no adds below entry' as non-negotiable.",
    },
    "Patience": {
        "context_only": True,
        "checks": {"avoided opening chop", "entry not chased"},
        "mistakes": {"no_confirmation": 22, "chased_entry": 22, "open_chop": 20,
                     "revenge_trade": 18},
        "clean": "Waited for the setup instead of anticipating or chasing.",
        "suggestion": "Let the setup complete; skip entries while setup quality reads 'poor'.",
    },
    "Timing": {
        "context_only": True,
        "checks": {"avoided opening chop"},
        "mistakes": {"open_chop": 28, "chased_entry": 14},
        "clean": "Entered outside the opening chop and without chasing.",
        "suggestion": "Avoid the first 15 minutes; enter on a retest, not the breakout candle.",
    },
    "Trend Alignment": {
        "context_only": True,
        "checks": {"trend confirmation"},
        "mistakes": {"counter_trend": 35},
        "clean": "Traded with the higher-timeframe trend.",
        "suggestion": "Match the higher-timeframe trend unless the setup is exceptional.",
    },
    "Reward/Risk Ratio": {
        "context_only": False,
        "checks": {"profit target defined", "expiration appropriate", "strike selection"},
        "mistakes": {"held_loser": 20, "cut_winner_early": 16, "theta_ignored": 16,
                     "lottery_ticket": 16},
        "clean": "Defined a target and used a strike/expiry with a workable payoff.",
        "suggestion": "Define a target before entry; favor 0.30+ delta and 7–45 DTE.",
    },
}

CATEGORY_ORDER = list(CATEGORY_SPEC)


def _grade(score: int | None) -> str:
    if score is None:
        return "—"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


@dataclass(frozen=True, slots=True)
class CategoryScore:
    name: str
    score: int | None          # 0-100, or None when not enough data
    grade: str                 # A-F, or "—"
    explanation: str           # references actual trade data where available
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "name": self.name, "score": self.score, "grade": self.grade,
            "explanation": self.explanation, "suggestion": self.suggestion,
        }


def score_categories(
    findings: list[Finding],
    mistakes: list[str],
    *,
    verdict: str,
    had_context: bool,
) -> list[CategoryScore]:
    """Score every category for one review. `findings` is the combined
    before+during finding list; `mistakes` the taxonomy tags; `had_context`
    whether a near-entry snapshot was available."""
    by_check = {f.check: f for f in findings}
    tags = set(mistakes)
    out: list[CategoryScore] = []

    for name in CATEGORY_ORDER:
        spec = CATEGORY_SPEC[name]
        present = [by_check[c] for c in spec["checks"] if c in by_check]
        relevant = {m: p for m, p in spec["mistakes"].items() if m in tags}

        # No evidence at all for a context-only category with no snapshot →
        # honestly report "not enough data" rather than a perfect score.
        if spec["context_only"] and not had_context and not present and not relevant:
            out.append(CategoryScore(name, None, "—",
                                     "Not enough captured data to assess "
                                     f"{name.lower()} for this trade.", ""))
            continue

        score = max(0, 100 - sum(relevant.values()))
        detail_bits = [f.detail for f in present][:3]

        if relevant:
            worst = max(relevant, key=lambda m: relevant[m])
            label = MISTAKES[worst][0].lower()
            explanation = "; ".join(detail_bits)
            explanation = (f"{explanation} — {label}." if explanation
                           else f"{label.capitalize()}.")
            suggestion = MISTAKES[worst][2]
        else:
            explanation = "; ".join(detail_bits) or spec["clean"]
            suggestion = spec["suggestion"]

        out.append(CategoryScore(name, score, _grade(score), explanation, suggestion))
    return out
