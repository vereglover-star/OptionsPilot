"""TimelineEngine — how the trader has changed, stated as dated facts.

The improvement timeline is the single most motivating output this system
produces, and for exactly that reason it is the easiest to make dishonest. Two
guards do the work:

* **Both sides of every comparison must clear the sample floor.** "Your average
  risk has fallen 18% since May" requires a May with enough trades to have an
  average worth comparing. `windows.previous_and_latest` is the one gate, and
  every month-over-month statement here goes through it.
* **A streak is only reported over trades that could have broken it.** "27
  consecutive trades without a stop-loss violation" is a lie if twenty of those
  trades were never reviewed and so could never have recorded a violation. The
  streak counter walks only trades whose stop behaviour was actually observed.

Entries are ranked by `magnitude` — the relative size of the change — so the
timeline leads with what actually moved rather than with whatever happened to be
computed first.
"""

from __future__ import annotations

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import Evidence, TimelineEntry
from optionspilot.intelligence.performance import METRIC_SPECS, compute
from optionspilot.intelligence.windows import bucket, period_label

# A month needs this many trades before it can be compared against another.
MIN_PERIOD_TRADES = 5
# Relative change below this is noise and is not narrated.
MIN_RELATIVE_CHANGE = 0.10
MAX_ENTRIES = 12

# Metrics narrated month over month. Chosen because each maps to a sentence a
# trader can act on; adding one here adds it to the timeline automatically.
NARRATED = (
    "expectancy", "win_rate", "profit_factor", "avg_r", "avg_outlay",
    "avg_hold_minutes", "consistency", "avg_process_score",
    "stop_discipline_rate", "mistake_rate", "clean_trade_rate",
    "size_consistency", "max_drawdown",
)

_VERBS: dict[str, tuple[str, str]] = {
    # metric key -> (verb when it rose, verb when it fell)
    "avg_outlay": ("grown", "fallen"),
    "avg_hold_minutes": ("lengthened", "shortened"),
    "mistake_rate": ("risen", "fallen"),
    "max_drawdown": ("deepened", "eased"),
}


def _verb(key: str, rose: bool) -> str:
    up, down = _VERBS.get(key, ("improved", "declined"))
    return up if rose else down


def _phrase_label(label: str) -> str:
    """A metric label as it reads mid-sentence: first character lowered, the
    rest untouched. Lower-casing the whole thing turns "Average R multiple"
    into "average r multiple", and the R is the unit."""
    return label[:1].lower() + label[1:] if label else label


class TimelineEngine:
    """Builds the dated improvement narrative."""

    def build(self, facts: tuple[TradeFact, ...] | list[TradeFact]
              ) -> tuple[TimelineEntry, ...]:
        facts = tuple(facts)
        entries: list[TimelineEntry] = []
        entries.extend(self._month_over_month(facts))
        entries.extend(self._streaks(facts))
        entries.extend(self._milestones(facts))
        entries.sort(key=lambda e: (-e.magnitude, e.period))
        return tuple(entries[:MAX_ENTRIES])

    # ── month over month ─────────────────────────────────────────────────

    def _month_over_month(self, facts: tuple[TradeFact, ...]
                          ) -> list[TimelineEntry]:
        months = bucket(facts, "month")
        eligible = [(k, v) for k, v in sorted(months.items())
                    if len(v) >= MIN_PERIOD_TRADES]
        if len(eligible) < 2:
            return []
        (prev_key, prev_facts), (latest_key, latest_facts) = eligible[-2], eligible[-1]
        prev_metrics = compute(prev_facts)
        latest_metrics = compute(latest_facts)

        out: list[TimelineEntry] = []
        for key in NARRATED:
            before = prev_metrics[key].value
            after = latest_metrics[key].value
            if not stats.comparable(before, after):
                continue
            label, unit, higher_is_better, _ = METRIC_SPECS[key]
            rose = after > before
            improved = rose == higher_is_better
            if (before > 0) != (after > 0):
                # Crossing zero makes a percentage meaningless — "expectancy
                # declined 114%" is arithmetically true and tells the trader
                # nothing. State the crossing itself.
                change = 100.0
                headline = (f"Your {_phrase_label(label)} turned "
                            f"{'positive' if after > 0 else 'negative'} in "
                            f"{period_label(latest_key, 'month')}.")
            else:
                pct = stats.pct_change(before, after)
                if pct is None or abs(pct) < MIN_RELATIVE_CHANGE * 100:
                    continue
                change = pct
                headline = (f"Your {_phrase_label(label)} has "
                            f"{_verb(key, rose)} "
                            f"{abs(pct):.0f}% since "
                            f"{period_label(prev_key, 'month')}.")
            out.append(TimelineEntry(
                period=latest_key,
                kind="improvement" if improved else "decline",
                headline=headline,
                detail=(
                    f"{_fmt(before, unit)} in {period_label(prev_key, 'month')} "
                    f"→ {_fmt(after, unit)} in "
                    f"{period_label(latest_key, 'month')}, over "
                    f"{len(prev_facts)} and {len(latest_facts)} trades."),
                evidence=(
                    Evidence(f"{label}, {period_label(prev_key, 'month')}",
                             before, len(prev_facts)),
                    Evidence(f"{label}, {period_label(latest_key, 'month')}",
                             after, len(latest_facts)),
                ),
                magnitude=abs(change) / 100,
            ))
        return out

    # ── streaks ──────────────────────────────────────────────────────────

    def _streaks(self, facts: tuple[TradeFact, ...]) -> list[TimelineEntry]:
        """Current runs of good behaviour, counted only over trades that could
        have broken them."""
        out: list[TimelineEntry] = []

        observed = [f for f in facts if f.had_stop is not None]
        run = 0
        for fact in reversed(observed):
            if fact.had_stop and not fact.widened_stop:
                run += 1
            else:
                break
        if run >= 5:
            out.append(TimelineEntry(
                period=observed[-1].entry_date if observed else "",
                kind="streak",
                headline=f"You have not violated your stop-loss rule in {run} "
                         f"consecutive trades.",
                detail=f"Counted over the {len(observed)} trades whose order "
                       f"history was reviewed — trades with no review could not "
                       f"have broken the streak and are not counted.",
                evidence=(Evidence("consecutive protected trades", run,
                                   len(observed),
                                   trade_ids=tuple(
                                       f.trade_id for f in observed[-run:][:25])),),
                magnitude=min(1.0, run / 30),
            ))

        reviewed = [f for f in facts if f.reviewed]
        clean_run = 0
        for fact in reversed(reviewed):
            if not fact.mistakes:
                clean_run += 1
            else:
                break
        if clean_run >= 5:
            out.append(TimelineEntry(
                period=reviewed[-1].entry_date if reviewed else "",
                kind="streak",
                headline=f"{clean_run} consecutive reviewed trades with no "
                         f"process mistake at all.",
                detail=f"Out of {len(reviewed)} reviewed trades in total.",
                evidence=(Evidence("consecutive clean trades", clean_run,
                                   len(reviewed),
                                   trade_ids=tuple(
                                       f.trade_id for f in reviewed[-clean_run:][:25])),),
                magnitude=min(1.0, clean_run / 25),
            ))

        wins = 0
        for fact in reversed(facts):
            if fact.pnl > 0:
                wins += 1
            elif fact.pnl < 0:
                break
        if wins >= 5:
            out.append(TimelineEntry(
                period=facts[-1].entry_date if facts else "", kind="streak",
                headline=f"{wins} winning trades in a row.",
                detail="Streaks are worth noticing and worth distrusting — "
                       "keep position size flat while it runs.",
                evidence=(Evidence("consecutive wins", wins, len(facts)),),
                magnitude=min(0.8, wins / 20),
            ))
        return out

    # ── milestones ───────────────────────────────────────────────────────

    def _milestones(self, facts: tuple[TradeFact, ...]) -> list[TimelineEntry]:
        """Round-number markers of accumulated experience. Deliberately few:
        a timeline crowded with '10 trades!' entries buries the real changes."""
        out: list[TimelineEntry] = []
        total = len(facts)
        for threshold in (25, 50, 100, 250, 500, 1000):
            if total >= threshold:
                milestone = facts[threshold - 1]
                out.append(TimelineEntry(
                    period=milestone.entry_date, kind="milestone",
                    headline=f"{threshold} completed trades.",
                    detail=f"Reached on {milestone.entry_date}. Sample size is "
                           f"what turns a hunch into a measurement.",
                    evidence=(Evidence("trades completed", threshold, total),),
                    magnitude=0.15,
                ))
        return out[-2:]


def _fmt(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "R":
        return f"{value:.2f}R"
    if unit == "min":
        return f"{value:.0f} min"
    if unit == "trades":
        return f"{value:.0f}"
    return f"{value:.2f}"
