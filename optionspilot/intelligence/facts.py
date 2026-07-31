"""`TradeFact` — the one normalized input every intelligence engine reads.

The system already records a completed trade in three places, each for a good
reason and each with a different shape:

* `journal.db` (`TradeRecord`) — the system of record; always present.
* `experience.db` (`ExperienceRecord`) — the rich learning row: indicators, IV,
  delta, DTE, regime, session. Present for every trade recorded since the
  Experience Engine shipped.
* `data/coach/*.json` (`CoachReview` dicts) — the process review: category
  scores, mistake tags, and the *observed* order behaviour (was a stop placed,
  was it widened, was a target defined).

Ten engines each joining those three by hand is exactly the "two objects
tracking one fact will drift" failure this codebase has already paid for twice
(`data/health.py`, and again in the settings UI). So the join happens **once**,
here, and produces a flat, immutable `TradeFact`. Everything above reads facts
and nothing else.

Rules this module holds to:

* **Never invent.** A field the sources cannot supply stays None. The engines
  are all written to treat None as "no information on this axis" rather than
  substituting a default, because a fabricated 0 delta would quietly become a
  "lottery ticket" finding.
* **Never raise.** These sources include a user-editable JSON directory and a
  SQLite payload written by an older build. `build_facts` skips what it cannot
  parse and reports the count in `FactSet.skipped` — an unreadable review must
  cost the user one review, not the whole dashboard.
* **Timezone discipline.** Every timestamp arrives aware-UTC and every
  *calendar* judgement (weekday, hour, session, period bucketing) is made in
  America/New_York, because a trader's Tuesday is the exchange's Tuesday. A
  09:30 ET entry is 13:30 UTC, and bucketing that as "hour 13" would put the
  open in the middle of the afternoon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")


def _num(value: Any) -> float | None:
    """Coerce to a finite float, tolerating None, '', bools and junk strings."""
    if value is None or value is True or value is False or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(value: Any) -> int | None:
    f = _num(value)
    return int(f) if f is not None else None


def _str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(x) for x in value if isinstance(x, (str, int, float)))


def _aware(ts: Any) -> datetime | None:
    """Normalize anything timestamp-shaped to aware UTC, or None."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str) and ts:
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass(frozen=True, slots=True)
class TradeFact:
    """One completed round trip, flattened across every source that knows about
    it. Immutable: engines may sort and filter facts but never mutate them, so
    one engine's bucketing can't perturb another's."""

    # ── identity & outcome (always present) ───────────────────────────────
    trade_id: str
    symbol: str
    direction: str                    # "long" | "short"
    strategy: str
    managed_by: str                   # "ai" | "manual"
    entry_ts: datetime                # aware UTC
    exit_ts: datetime                 # aware UTC
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    is_win: bool
    hold_minutes: float
    exit_reason: str = ""
    return_pct: float | None = None

    # ── calendar, in exchange time ────────────────────────────────────────
    entry_date: str = ""              # ISO date in ET
    weekday: str = ""                 # "Tuesday"
    hour_et: int | None = None
    minute_et: int | None = None
    session: str = "regular"          # "pre" | "regular" | "post"

    # ── decision context (best-effort) ────────────────────────────────────
    confidence: float | None = None
    setup_quality: str | None = None
    market_regime: str | None = None
    htf_trend: str | None = None
    timeframe: str | None = None
    risk_reward: float | None = None
    rsi: float | None = None
    adx: float | None = None
    rvol: float | None = None
    iv: float | None = None
    delta: float | None = None
    dte: int | None = None
    spread_pct: float | None = None

    # ── risk & sizing ─────────────────────────────────────────────────────
    outlay: float = 0.0               # premium paid: entry_price × 100 × qty
    r_multiple: float | None = None   # realized P/L in units of planned risk

    # ── process (from the coach review, when one exists) ──────────────────
    process_score: int | None = None
    verdict: str | None = None        # "won" | "lost" | "scratch"
    mistakes: tuple[str, ...] = ()
    lessons: tuple[str, ...] = ()
    evidence_names: tuple[str, ...] = ()
    category_scores: dict[str, float] = field(default_factory=dict)
    # Tri-state, from the review's observed order history. None means "no review
    # covered this trade", which is emphatically not the same as False.
    had_stop: bool | None = None
    widened_stop: bool | None = None
    had_target: bool | None = None
    reviewed: bool = False

    @property
    def is_loss(self) -> bool:
        return self.pnl < 0

    @property
    def entry_ts_et(self) -> datetime:
        return self.entry_ts.astimezone(ET)

    @property
    def exit_ts_et(self) -> datetime:
        return self.exit_ts.astimezone(ET)

    def has_mistake(self, tag: str) -> bool:
        return tag in self.mistakes


@dataclass(frozen=True, slots=True)
class FactSet:
    """Facts plus what it cost to build them.

    `skipped` and `notes` exist so the engine can tell the user "42 of your 300
    trades predate the review system, so the discipline score is measured over
    258" — an omission the user can see beats a silently smaller denominator.
    """

    facts: tuple[TradeFact, ...]
    skipped: int = 0
    notes: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.facts)

    def __iter__(self):
        return iter(self.facts)


def _review_flags(review: dict) -> dict:
    """Read the three observed order behaviours out of a review's `during`
    findings.

    These are *observations the coach already made* from the real order history,
    not a re-derivation: `TradeCoach._during_checks` inspects the OrderManager
    record and writes the answer into a Finding with a stable `check` name. This
    reads that answer. Re-deriving it here would need the order history the
    intelligence layer deliberately does not depend on, and would be a second
    place the same fact is computed.
    """
    flags: dict[str, bool | None] = {
        "had_stop": None, "widened_stop": None, "had_target": None}
    during = review.get("during")
    if not isinstance(during, list):
        return flags
    for finding in during:
        if not isinstance(finding, dict):
            continue
        check = finding.get("check")
        passed = finding.get("passed")
        if not isinstance(passed, bool):
            continue
        if check == "stop in place":
            flags["had_stop"] = passed
        elif check == "stop discipline":
            # The finding passes when the stop was NOT widened.
            flags["widened_stop"] = not passed
        elif check == "profit target defined":
            flags["had_target"] = passed
    # A review that assessed stop placement but never had to assess stop
    # discipline (only one stop order ever existed) did not widen a stop.
    if flags["widened_stop"] is None and flags["had_stop"] is not None:
        flags["widened_stop"] = False
    return flags


def _category_scores(review: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    cats = review.get("categories")
    if not isinstance(cats, list):
        return out
    for c in cats:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        score = _num(c.get("score"))
        if isinstance(name, str) and score is not None:
            out[name] = score
    return out


def _fact_from_experience(rec: Any, review: dict | None) -> TradeFact | None:
    """Build a fact from an ExperienceRecord (the richest source) plus, when one
    exists, its coach review."""
    entry = _aware(getattr(rec, "entry_ts", None))
    exit_ts = _aware(getattr(rec, "exit_ts", None))
    trade_id = getattr(rec, "trade_id", None)
    if entry is None or exit_ts is None or not trade_id:
        return None

    et = entry.astimezone(ET)
    review = review or {}
    flags = _review_flags(review)
    quantity = _int(getattr(rec, "quantity", 0)) or 0
    entry_price = _num(getattr(rec, "entry_price", 0.0)) or 0.0
    pnl = _num(getattr(rec, "pnl", 0.0)) or 0.0

    # The experience row's own hour is captured from the entry context and may be
    # absent; the timestamp is always there, so it is the fallback — never the
    # other way round, since a context snapshot can be missing but is never wrong.
    hour = _int(getattr(rec, "hour_et", None))
    minute = _int(getattr(rec, "minute_et", None))

    return TradeFact(
        trade_id=str(trade_id),
        symbol=str(getattr(rec, "symbol", "") or "").upper(),
        direction=str(getattr(rec, "direction", "") or ""),
        strategy=str(getattr(rec, "strategy", "") or "unknown"),
        managed_by=str(getattr(rec, "managed_by", "") or "unknown"),
        entry_ts=entry, exit_ts=exit_ts,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=_num(getattr(rec, "exit_price", 0.0)) or 0.0,
        pnl=pnl,
        is_win=bool(getattr(rec, "is_win", pnl > 0)),
        hold_minutes=_num(getattr(rec, "hold_minutes", 0.0)) or 0.0,
        exit_reason=str(getattr(rec, "exit_reason", "") or ""),
        return_pct=_num(getattr(rec, "return_pct", None)),
        entry_date=et.date().isoformat(),
        weekday=WEEKDAY_NAMES[et.weekday()],
        hour_et=hour if hour is not None else et.hour,
        minute_et=minute if minute is not None else et.minute,
        session=str(getattr(rec, "market_session", "") or "regular"),
        confidence=_num(getattr(rec, "confidence_entry", None)),
        setup_quality=getattr(rec, "setup_quality", None),
        market_regime=getattr(rec, "market_regime", None),
        htf_trend=getattr(rec, "htf_trend", None),
        timeframe=getattr(rec, "timeframe", None),
        risk_reward=_num(getattr(rec, "risk_reward", None)),
        rsi=_num(getattr(rec, "rsi", None)),
        adx=_num(getattr(rec, "adx", None)),
        rvol=_num(getattr(rec, "rvol", None)),
        iv=_num(getattr(rec, "iv", None)),
        delta=_num(getattr(rec, "delta", None)),
        dte=_int(getattr(rec, "dte", None)),
        spread_pct=_num(getattr(rec, "spread_pct", None)),
        outlay=round(entry_price * 100 * quantity, 2),
        # The review's best-effort R is preferred over the experience row's,
        # because the coach derives it from the actual protective-stop level.
        r_multiple=(_num(review.get("r_multiple"))
                    if review.get("r_multiple") is not None
                    else _num(getattr(rec, "risk_multiple", None))),
        process_score=_int(review.get("score")),
        verdict=review.get("verdict") if isinstance(review.get("verdict"), str) else None,
        mistakes=tuple(sorted(set(_str_list(getattr(rec, "mistakes", ())))
                              | set(_str_list(review.get("mistakes"))))),
        lessons=_str_list(getattr(rec, "lessons", ())),
        evidence_names=_str_list(getattr(rec, "evidence_names", ())),
        category_scores=_category_scores(review),
        had_stop=flags["had_stop"],
        widened_stop=flags["widened_stop"],
        had_target=flags["had_target"],
        reviewed=bool(review),
    )


def _fact_from_trade(trade: Any, review: dict | None) -> TradeFact | None:
    """Fallback builder for a journal `TradeRecord` with no experience row —
    trades closed before the Experience Engine shipped. Everything the journal
    genuinely knows is carried; the indicator context simply stays None."""
    entry = _aware(getattr(trade, "entry_ts", None))
    exit_ts = _aware(getattr(trade, "exit_ts", None))
    trade_id = getattr(trade, "id", None)
    if entry is None or exit_ts is None or not trade_id:
        return None

    et = entry.astimezone(ET)
    review = review or {}
    flags = _review_flags(review)
    conditions = getattr(trade, "market_conditions", None) or {}
    quantity = _int(getattr(trade, "quantity", 0)) or 0
    entry_price = _num(getattr(trade, "entry_price", 0.0)) or 0.0
    try:
        pnl = float(trade.pnl)
    except (AttributeError, TypeError, ValueError):
        return None
    direction = getattr(trade, "direction", None)
    strategy = str(getattr(trade, "strategy", "") or "unknown")
    outlay = round(entry_price * 100 * quantity, 2)

    return TradeFact(
        trade_id=str(trade_id),
        symbol=str(getattr(trade, "symbol", "") or "").upper(),
        direction=getattr(direction, "value", str(direction or "")),
        strategy=strategy,
        managed_by="manual" if strategy == "manual" else "ai",
        entry_ts=entry, exit_ts=exit_ts,
        quantity=quantity, entry_price=entry_price,
        exit_price=_num(getattr(trade, "exit_price", 0.0)) or 0.0,
        pnl=pnl, is_win=pnl > 0,
        hold_minutes=_num(getattr(trade, "hold_minutes", 0.0)) or 0.0,
        exit_reason=str(getattr(trade, "exit_reason", "") or ""),
        return_pct=round(pnl / outlay * 100, 2) if outlay else None,
        entry_date=et.date().isoformat(),
        weekday=WEEKDAY_NAMES[et.weekday()],
        hour_et=et.hour, minute_et=et.minute,
        session="regular",
        confidence=_num(getattr(trade, "confidence", None)),
        setup_quality=conditions.get("setup_quality"),
        htf_trend=conditions.get("htf_trend"),
        timeframe=conditions.get("timeframe"),
        risk_reward=_num(conditions.get("risk_reward")),
        dte=_int(conditions.get("dte")),
        outlay=outlay,
        r_multiple=_num(review.get("r_multiple")),
        process_score=_int(review.get("score")),
        verdict=review.get("verdict") if isinstance(review.get("verdict"), str) else None,
        mistakes=tuple(sorted(set(_str_list(getattr(trade, "mistakes", ())))
                              | set(_str_list(review.get("mistakes"))))),
        lessons=_str_list(getattr(trade, "lessons", ())),
        evidence_names=_str_list(getattr(trade, "indicators_used", ())),
        category_scores=_category_scores(review),
        had_stop=flags["had_stop"], widened_stop=flags["widened_stop"],
        had_target=flags["had_target"], reviewed=bool(review),
    )


def build_facts(
    *,
    experiences: Iterable[Any] = (),
    reviews: Iterable[dict] = (),
    trades: Iterable[Any] = (),
) -> FactSet:
    """Join every source into one chronologically-sorted `FactSet`.

    Precedence is `experiences` first (richest), then any `trades` the
    experience store has never heard of. A trade present in both is built once,
    from the experience row — the journal row carries a strict subset of the
    same facts, so preferring it would silently drop the indicator context.

    Sorting is by (entry_ts, trade_id): the tie-break is what makes every
    downstream sequence-sensitive analysis — streaks, revenge-window detection,
    equity curves — reproducible when two trades share a timestamp.
    """
    review_by_id: dict[str, dict] = {}
    skipped = 0
    for review in reviews:
        if not isinstance(review, dict):
            skipped += 1
            continue
        tid = review.get("trade_id")
        if isinstance(tid, str) and tid:
            review_by_id[tid] = review

    facts: list[TradeFact] = []
    seen: set[str] = set()

    for rec in experiences:
        tid = str(getattr(rec, "trade_id", "") or "")
        fact = _fact_from_experience(rec, review_by_id.get(tid))
        if fact is None:
            skipped += 1
            continue
        facts.append(fact)
        seen.add(fact.trade_id)

    for trade in trades:
        tid = str(getattr(trade, "id", "") or "")
        if tid and tid in seen:
            continue
        fact = _fact_from_trade(trade, review_by_id.get(tid))
        if fact is None:
            skipped += 1
            continue
        facts.append(fact)
        seen.add(fact.trade_id)

    facts.sort(key=lambda f: (f.entry_ts, f.trade_id))

    notes: list[str] = []
    reviewed = sum(1 for f in facts if f.reviewed)
    if facts and reviewed < len(facts):
        notes.append(
            f"{len(facts) - reviewed} of {len(facts)} trades have no process "
            f"review, so discipline and execution findings are measured over "
            f"the {reviewed} that do.")
    if skipped:
        notes.append(f"{skipped} record(s) could not be read and were skipped.")

    return FactSet(facts=tuple(facts), skipped=skipped, notes=tuple(notes))
