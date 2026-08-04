"""The guided-onboarding domain layer (V0.6.1).

The tutorials themselves — their titles, their steps, the elements they point
at — live in `ui/static/index.html`, because a step is a CSS selector plus a
sentence and neither of those is knowledge Python should hold. What lives here
is everything the *frontend* cannot answer for itself:

  1. **Durable state.** Which tutorials the user finished, which they skipped,
     which features they have actually used, and their motion/hint preferences.
     Persisted through `RuntimeSettings` into `settings.json` rather than
     localStorage so it survives a cleared webview profile, a reinstall, and a
     restore-from-backup — the same standard every other user preference in this
     app is held to.
  2. **Which tutorial to offer next**, from measured usage. This is the "AI
     Coach notices you've only ever placed market orders" behaviour, and it is
     built the same way as everything else in this codebase that makes a claim
     about the user: each recommendation carries the evidence that produced it,
     and a rule that cannot evidence itself does not fire.

Two boundaries are load-bearing:

**Ids are the contract, prose is not.** A `Recommendation` names a tutorial by
**id only**; the human title comes from the frontend catalogue at render time.
Duplicating the titles here would be a second place that tracks one fact, which
is a failure this codebase has already paid for twice (`data/health.py` in
V0.5.3, the settings ranking in V0.5.7). `tests/test_guide.py` asserts the two
catalogues hold exactly the same ids, so adding a tutorial in one place and
forgetting the other fails the suite instead of silently producing a
recommendation that renders as blank.

**This module recommends TUTORIALS from FEATURE USAGE. It never recommends
trading behaviour.** That is `intelligence/`'s job, it does it from the trade
record with a false-discovery correction underneath it, and a second, cruder
path to the same kind of claim would be exactly the drift described above. The
line is concrete: "you have never placed a limit order" is a fact about the
software; "you should place more limit orders" is a claim about the trader, and
this module does not make it.

Pure and deterministic: no I/O, no clock, no network. `GuideFacts` is measured
by the caller (`ui/server.py`) and passed in.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# ── the tutorial catalogue ───────────────────────────────────────────────────
# Ids only. `tests/test_guide.py::TestCatalogueContract` asserts this set is
# exactly the set declared by GUIDE_TUTORIALS in index.html.
TUTORIALS: tuple[str, ...] = (
    "welcome",       # the first-launch tour, the only one that spans tabs
    "dashboard",
    "charts",
    "trade",
    "coach",
    "watchlist",
    "journal",
    "backtest",
    "learning",
    "settings",
    "marketdata",    # Settings ▸ Market data, deep enough to warrant its own
)

# Feature keys the recommender actually reads. The frontend may record others
# (instrumentation should not need a backend change to land); unknown keys are
# stored and never interpreted. These specific ones must exist as literal
# `Guide.mark("…")` calls in index.html or the rule that reads them can never
# fire — which the contract test also checks, because a recommendation that is
# unreachable is worse than one that is absent: it looks implemented.
KNOWN_FEATURES: tuple[str, ...] = (
    "tab.charts",
    "tab.coach",
    "tab.journal",
    "tab.watchlist",
    "tab.backtest",
    "tab.learning",
    "chart.indicator",
    "chart.drawing",
    "settings.marketdata",
    "help.search",
)

GUIDE_VERSION = 1

# A hand-edited settings.json must cost a user their guide progress, never their
# app (the `apply_control_state` lesson, V0.5.7). Everything below is validated
# by SHAPE on the way in, and the cap exists so a malformed or malicious file
# cannot grow settings.json without bound.
MAX_FEATURE_KEYS = 200
_FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")

DEFAULT_STATE: dict = {
    "onboarded": False,     # has the welcome tour been finished or skipped?
    "completed": [],        # tutorial ids the user finished
    "dismissed": [],        # tutorial ids the user skipped out of
    "features": {},         # feature key -> times used
    "reduce_motion": False,  # explicit user override, on top of the OS setting
    "tips": True,           # adaptive hover explanations
    "large_text": False,    # scale the whole type ramp
    "high_contrast": False,  # raise secondary-text and border contrast
    "version": GUIDE_VERSION,
}

# The three display preferences, all booleans, all replaced rather than merged.
# They live here rather than in localStorage for the same reason tutorial
# progress does: an accessibility setting a user needed once is a setting they
# will need again on the next machine, and losing it silently is worse than
# never having offered it.
DISPLAY_FLAGS = ("reduce_motion", "tips", "large_text", "high_contrast")


def default_state() -> dict:
    """A fresh state document. Deep enough copy that callers can mutate it."""
    return {**DEFAULT_STATE, "completed": [], "dismissed": [], "features": {}}


# ── state validation and merging ─────────────────────────────────────────────

def _id_list(raw, *, known: frozenset[str]) -> list[str]:
    """A de-duplicated, order-preserving list of known tutorial ids."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in known and item not in out:
            out.append(item)
    return out


def _features(raw) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        if len(out) >= MAX_FEATURE_KEYS:
            break
        if not isinstance(key, str) or not _FEATURE_RE.match(key):
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value < 0:
            continue
        out[key] = value
    return out


def normalize_state(raw) -> dict:
    """Coerce anything read off disk into a valid state document.

    Never raises. An unrecognised tutorial id is dropped rather than preserved:
    it may be from a build that no longer exists, and carrying it forward would
    let a downgrade resurrect a tutorial the user cannot see.
    """
    known = frozenset(TUTORIALS)
    if not isinstance(raw, dict):
        return default_state()
    return {
        "onboarded": bool(raw.get("onboarded", False)),
        "completed": _id_list(raw.get("completed"), known=known),
        "dismissed": _id_list(raw.get("dismissed"), known=known),
        "features": _features(raw.get("features")),
        "reduce_motion": bool(raw.get("reduce_motion", False)),
        "tips": bool(raw.get("tips", True)),
        "large_text": bool(raw.get("large_text", False)),
        "high_contrast": bool(raw.get("high_contrast", False)),
        "version": GUIDE_VERSION,
    }


def merge_state(current: dict, patch) -> dict:
    """Apply a client patch to a normalized state document.

    Merge semantics differ per field on purpose:

      * `completed` / `dismissed` are **unions** — finishing a tutorial is an
        event, and a client that posts a short list (or an old one) must not be
        able to un-finish anything.
      * `features` are **increments**, posted as a list of keys used since the
        last patch, so two tabs open at once cannot clobber each other's counts.
      * `onboarded` and every flag in `DISPLAY_FLAGS` are **replacements** —
        they are settings, and a user turning one off must win.

    `forget: true` resets everything, which is what Settings ▸ "Replay the
    tutorials" needs; it is a full reset rather than a per-field one because a
    user asking to start over means all of it.
    """
    state = normalize_state(current)
    if not isinstance(patch, dict):
        return state
    if patch.get("forget") is True:
        return default_state()

    known = frozenset(TUTORIALS)
    for field_name in ("completed", "dismissed"):
        for item in _id_list(patch.get(field_name), known=known):
            if item not in state[field_name]:
                state[field_name].append(item)
    # Finishing a tutorial un-dismisses it: the user came back and did it.
    state["dismissed"] = [i for i in state["dismissed"]
                          if i not in state["completed"]]

    used = patch.get("features")
    if isinstance(used, str):
        used = [used]
    if isinstance(used, (list, tuple)):
        for key in used:
            if not isinstance(key, str) or not _FEATURE_RE.match(key):
                continue
            if key not in state["features"] and \
                    len(state["features"]) >= MAX_FEATURE_KEYS:
                continue
            state["features"][key] = state["features"].get(key, 0) + 1

    for flag in ("onboarded", *DISPLAY_FLAGS):
        if flag in patch:
            state[flag] = bool(patch[flag])
    # Finishing or skipping the welcome tour IS being onboarded — the client
    # should not have to remember to post both.
    if "welcome" in state["completed"] or "welcome" in state["dismissed"]:
        state["onboarded"] = True
    return state


# ── measured facts ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuideFacts:
    """What the app can observe about how it has been used.

    Every field is measured by the caller from state that already exists (the
    journal, the order book, the broker, the config) — nothing here is recorded
    specially for the guide, and nothing here touches the network.

    `single_data_source` is `bool | None` because "I could not determine this"
    and "no, there are several sources" are different answers and only the first
    should stop a rule from firing. A provider double (a test, an embedding) has
    no chain to inspect, and inventing `False` there would be a claim.
    """
    closed_trades: int = 0
    manual_trades: int = 0
    coach_reviews: int = 0
    open_positions: int = 0
    orders_placed: int = 0
    order_kinds_used: frozenset[str] = field(default_factory=frozenset)
    watchlist_size: int = 0
    single_data_source: bool | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["order_kinds_used"] = sorted(self.order_kinds_used)
        return out


@dataclass(frozen=True)
class Recommendation:
    """One suggested tutorial, and the measurement that suggested it.

    `tutorial` is an id from `TUTORIALS`; the frontend supplies its title.
    `reason` is the sentence shown to the user and must state the observation,
    not a judgement — "all 6 of your orders so far have been market orders", not
    "you are over-reliant on market orders".
    """
    tutorial: str
    headline: str
    reason: str
    priority: int
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


MAX_RECOMMENDATIONS = 3


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many}"


def recommendations(state: dict, facts: GuideFacts) -> list[Recommendation]:
    """Which tutorials to offer, best first, capped at `MAX_RECOMMENDATIONS`.

    Rules are independent and each one is a guard plus a sentence. A rule fires
    only when the state it describes has actually been observed; nothing here
    fires on an absence of data alone, except the welcome tour, whose whole
    purpose is to run before there is any data.
    """
    state = normalize_state(state)
    used = state["features"]
    done = set(state["completed"])
    skipped = set(state["dismissed"])
    out: list[Recommendation] = []

    def offer(tutorial: str, headline: str, reason: str, priority: int,
              **evidence) -> None:
        if tutorial in done or tutorial in skipped:
            return
        out.append(Recommendation(tutorial=tutorial, headline=headline,
                                  reason=reason, priority=priority,
                                  evidence=evidence))

    if not state["onboarded"]:
        offer("welcome", "Take the two-minute tour",
              "You haven't been through the guided tour yet — it walks you "
              "through every screen in about two minutes.", 0)

    # Order types. The evidence is the order book, not a guess: the user has
    # placed real orders and every one of them was a market order.
    kinds = set(facts.order_kinds_used)
    if facts.orders_placed >= 3 and kinds and kinds <= {"market"}:
        offer("trade", "There is more than one kind of order",
              f"All {facts.orders_placed} orders you've placed so far were "
              "market orders. A limit order lets you name the price you're "
              "willing to pay instead of taking whatever is quoted.", 10,
              orders_placed=facts.orders_placed, kinds_used=sorted(kinds))

    # An open position and no exit order has ever been used. This is about the
    # existence of the feature, not about whether this trade should have one.
    exit_kinds = {"stop_loss", "take_profit", "trailing_stop"}
    if facts.open_positions >= 1 and not (kinds & exit_kinds):
        offer("trade", "Exit orders run while you're away",
              f"You have {_plural(facts.open_positions, 'open position', 'open positions')} "
              "and haven't used a stop-loss, take-profit or trailing stop yet. "
              "They're placed from the same ticket, on the sell side.", 20,
              open_positions=facts.open_positions)

    if facts.coach_reviews >= 1 and not used.get("tab.coach"):
        offer("coach", "Your trades have already been reviewed",
              f"The coach has written {_plural(facts.coach_reviews, 'review', 'reviews')} "
              "you haven't opened. It scores the process you followed, not "
              "whether the trade won.", 15,
              coach_reviews=facts.coach_reviews)

    if facts.closed_trades >= 1 and not used.get("tab.journal"):
        offer("journal", "Everything you've traded is recorded",
              f"You've closed {_plural(facts.closed_trades, 'trade', 'trades')} "
              "and haven't opened the Journal. It's where the patterns in your "
              "own trading become visible.", 25,
              closed_trades=facts.closed_trades)

    if used.get("tab.charts", 0) >= 3 and not used.get("chart.indicator"):
        offer("charts", "The chart can show you more",
              f"You've opened the chart {_plural(used['tab.charts'], 'time', 'times')} "
              "without switching on an indicator. EMA, VWAP, RSI and MACD each "
              "answer a different question about the same candles.", 30,
              chart_visits=used["tab.charts"])

    if facts.single_data_source is True and not used.get("settings.marketdata"):
        offer("marketdata", "You're relying on a single price source",
              "Only one independent market-data provider can answer right now, "
              "so one outage takes your charts with it. Adding a free API key "
              "takes about a minute.", 35,
              single_data_source=True)

    if facts.closed_trades == 0 and not used.get("tab.backtest"):
        offer("backtest", "Try a strategy on history first",
              "You haven't closed a trade yet. A backtest replays the same "
              "engine over past data, so you can watch how it decides before "
              "any of it is your money — even paper money.", 45)

    if facts.watchlist_size <= 3 and not used.get("tab.watchlist"):
        offer("watchlist", "The AI only looks at what you tell it to",
              f"Your watchlist holds {_plural(facts.watchlist_size, 'symbol', 'symbols')}. "
              "Every scan cycle only ever examines those.", 50,
              watchlist_size=facts.watchlist_size)

    out.sort(key=lambda r: (r.priority, r.tutorial))
    # One tutorial can be reached by two rules (Trade, above). Keep the
    # higher-priority reason rather than showing the same tour twice.
    seen: set[str] = set()
    unique = [r for r in out if not (r.tutorial in seen or seen.add(r.tutorial))]
    return unique[:MAX_RECOMMENDATIONS]


def payload(state: dict, facts: GuideFacts) -> dict:
    """The whole `/api/guide` body: state, measured facts, and what to offer."""
    normalized = normalize_state(state)
    return {
        "state": normalized,
        "tutorials": list(TUTORIALS),
        "facts": facts.to_dict(),
        "recommendations": [r.to_dict()
                            for r in recommendations(normalized, facts)],
        "version": GUIDE_VERSION,
    }


__all__ = [
    "DEFAULT_STATE", "DISPLAY_FLAGS", "GUIDE_VERSION", "GuideFacts",
    "KNOWN_FEATURES",
    "MAX_FEATURE_KEYS", "MAX_RECOMMENDATIONS", "Recommendation", "TUTORIALS",
    "default_state", "merge_state", "normalize_state", "payload",
    "recommendations",
]
