"""Time bucketing and analysis windows.

Two related jobs, kept together because they share one rule:

* **Periods** (`day`, `week`, `month`, `quarter`, `year`) slice the whole history
  into a time series, for trend detection and the improvement timeline.
* **Windows** (`lifetime`, `last_30d`, `last_20_trades`, …) select a *subset* of
  trades for a metric to be measured over, so a goal can say "average R above 2
  over the last 20 trades" and mean it.

The rule: **every calendar decision is made in America/New_York.** A trade
entered at 16:30 ET on a Friday is 20:30 UTC Friday — same day either way — but
one entered at 21:00 ET Friday (a late-session close) is 01:00 UTC *Saturday*,
and bucketing it into the following week would move a Friday trade into next
week's report. The exchange's calendar is the trader's calendar.

Week keys use ISO week-numbering (`2026-W30`), so a week always has seven days
and never straddles a year boundary ambiguously.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from optionspilot.intelligence.facts import ET, TradeFact

PERIODS = ("day", "week", "month", "quarter", "year")


def period_key(ts: datetime, period: str) -> str:
    """The sortable identifier of the period `ts` falls in, in exchange time.

    Keys are chosen so plain string sort equals chronological sort — which is
    what lets the whole package order time series without parsing dates back
    out. (`2026-Q3` > `2026-Q2` lexically as well as chronologically; a naive
    `2026-7` would break that against `2026-10`.)
    """
    et = ts.astimezone(ET)
    if period == "day":
        return et.date().isoformat()
    if period == "week":
        iso = et.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    if period == "month":
        return f"{et.year:04d}-{et.month:02d}"
    if period == "quarter":
        return f"{et.year:04d}-Q{(et.month - 1) // 3 + 1}"
    if period == "year":
        return f"{et.year:04d}"
    raise ValueError(f"unknown period {period!r}; expected one of {PERIODS}")


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def period_label(key: str, period: str) -> str:
    """A human-readable rendering of a period key. Falls back to the key itself
    for anything unparseable — a label is cosmetic and must never raise."""
    try:
        if period == "day":
            d = datetime.fromisoformat(key).date()
            return d.strftime("%d %b %Y").lstrip("0")
        if period == "week":
            year, week = key.split("-W")
            return f"Week {int(week)}, {year}"
        if period == "month":
            year, month = key.split("-")
            return f"{_MONTHS[int(month) - 1]} {year}"
        if period == "quarter":
            year, quarter = key.split("-Q")
            return f"Q{quarter} {year}"
        if period == "year":
            return key
    except (ValueError, IndexError):
        return key
    return key


def bucket(facts: list[TradeFact] | tuple[TradeFact, ...],
           period: str) -> dict[str, list[TradeFact]]:
    """Group facts by period key, preserving each bucket's chronological order
    (facts arrive sorted from `build_facts` and grouping is stable)."""
    out: dict[str, list[TradeFact]] = {}
    for fact in facts:
        out.setdefault(period_key(fact.entry_ts, period), []).append(fact)
    return out


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """A named subset of history.

    Exactly one of `days` / `count` is set, or neither for lifetime. Kept as
    data rather than as a function so a `Goal` can persist a window by name and
    the engine can resolve it later, without the goals file ever holding code.
    """

    name: str
    label: str
    days: int | None = None
    count: int | None = None

    def select(self, facts: tuple[TradeFact, ...] | list[TradeFact],
               now: datetime | None = None) -> list[TradeFact]:
        """The facts this window covers, oldest first.

        A day-bounded window measures back from `now` when given one, and
        otherwise from the most recent trade. Measuring from the last trade
        matters for reproducibility: a snapshot recomputed a week later with no
        new trades must not silently empty its own 30-day window and report that
        the trader's discipline has become unmeasurable.
        """
        if not facts:
            return []
        ordered = list(facts)
        if self.count is not None:
            return ordered[-self.count:]
        if self.days is not None:
            anchor = now or ordered[-1].entry_ts
            cutoff = anchor - timedelta(days=self.days)
            return [f for f in ordered if f.entry_ts >= cutoff]
        return ordered


WINDOWS: dict[str, WindowSpec] = {
    "lifetime": WindowSpec("lifetime", "All time"),
    "last_7d": WindowSpec("last_7d", "Last 7 days", days=7),
    "last_30d": WindowSpec("last_30d", "Last 30 days", days=30),
    "last_90d": WindowSpec("last_90d", "Last 90 days", days=90),
    "last_10_trades": WindowSpec("last_10_trades", "Last 10 trades", count=10),
    "last_20_trades": WindowSpec("last_20_trades", "Last 20 trades", count=20),
    "last_50_trades": WindowSpec("last_50_trades", "Last 50 trades", count=50),
}

# The window behavioral analysis and the action plan reflect. Deliberately
# trade-counted rather than date-bounded: a habit the trader has stopped should
# drop off the action plan because newer clean trades pushed it out, not because
# a calendar month elapsed while they were away from the screen.
RECENT_WINDOW = "last_50_trades"


def resolve(name: str) -> WindowSpec:
    """Look up a window by name, falling back to lifetime for an unknown one —
    a goal persisted by a newer build and read by an older one degrades to a
    wider measurement rather than disappearing."""
    return WINDOWS.get(name, WINDOWS["lifetime"])


def previous_and_latest(buckets: dict[str, list[TradeFact]], min_trades: int
                        ) -> tuple[tuple[str, list[TradeFact]] | None,
                                   tuple[str, list[TradeFact]] | None]:
    """The two most recent periods that each clear `min_trades`.

    Returns `(previous, latest)`, either of which may be None. This is the
    single gate on every "improved since last month" statement in the package:
    a month with two trades is not a month worth comparing, and comparing one
    would produce confident nonsense in exactly the situation — a new trader,
    few trades — where confident nonsense does the most damage.
    """
    eligible = [(k, v) for k, v in sorted(buckets.items()) if len(v) >= min_trades]
    if not eligible:
        return None, None
    if len(eligible) == 1:
        return None, eligible[-1]
    return eligible[-2], eligible[-1]
