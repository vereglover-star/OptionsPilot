"""BehaviorEngine — what the trader keeps *doing*, as opposed to how they did.

Performance answers "did this work". Behavior answers "why", and it is the part
that is easy to do badly: a coaching system that says "you revenge trade"
without being able to name the trades is indistinguishable from a horoscope.

So every detector in this module obeys the same four rules:

1. **It measures something.** Each detector returns the exact count, the exact
   rate, and up to `MAX_CITED` of the trade IDs it counted. A finding with no
   `trade_ids` is a bug, not a style choice.
2. **It states what it could not measure.** Where the captured data genuinely
   cannot answer a question — hesitation needs the latency between a signal
   appearing and the entry being taken, which nothing in this system records —
   the detector returns `assessable=False` with the reason. It does not return
   "not detected", because that is a claim, and it would be an unearned one.
3. **It prices the habit.** Where the trades are separable, the finding carries
   an `Impact`: the trader's expectancy recomputed over the same history with
   the affected trades removed. That is a counterfactual over trades that
   actually happened, not a forecast, and `Impact.basis` says so in words.
4. **It cannot be triggered by one trade.** `MIN_OCCURRENCES` is 2 and
   confidence is capped until a habit repeats. One bad Tuesday is not a habit,
   however large its share of a five-trade sample.

Relationship to the coach's `MISTAKES` taxonomy: the coach tags *one trade* at
review time; this engine tests *a population* for a repeated tendency, and
several detectors read those tags as one input among several (`chasing` also
reads RSI at entry; `moving_stops` also reads the observed order history). They
are different questions at different altitudes, which is why the intelligence
layer does not import the coach — tags reach it as data, on the fact.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass

from optionspilot.intelligence import stats
from optionspilot.intelligence.facts import TradeFact
from optionspilot.intelligence.models import (
    BehaviorFinding, Confidence, Evidence, Impact, Severity, Trend,
)

# How many trade IDs a finding carries as evidence. Enough for the UI's "Why?"
# to be convincing and to spot-check by hand; bounded so a 100k-trade history
# can't put a megabyte of IDs into a status payload.
MAX_CITED = 25

# A tendency needs to repeat before it is named. One occurrence is an event.
MIN_OCCURRENCES = 2

# Minutes after a losing exit within which a new entry is treated as reactive.
# Matches the coach's own revenge-trade window so the per-trade tag and the
# cross-trade finding can never disagree about the same entry.
REVENGE_WINDOW_MIN = 15.0

# Rate at which a tendency stops being incidental and becomes the finding's
# headline. Below it a detector still reports the occurrences, but as INFO.
DEFAULT_RATE_FLOOR = 0.15


@dataclass(frozen=True, slots=True)
class BehaviorSpec:
    """The catalogue entry for one behavior: what it is, how bad it is when
    real, and — critically — what data answering it requires. `needs` is what
    the finding quotes back when it has to decline."""

    id: str
    label: str
    question: str
    severity: Severity
    needs: str
    rate_floor: float = DEFAULT_RATE_FLOOR
    action: str = ""            # the corrective instruction, when detected


BEHAVIORS: dict[str, BehaviorSpec] = {
    "revenge_trading": BehaviorSpec(
        "revenge_trading", "Revenge trading",
        "Do you re-enter immediately after a loss?", Severity.SERIOUS,
        "entry and exit timestamps", 0.08,
        action="Set a hard 15-minute no-trade timer after every losing exit, "
               "and log what you would have taken during it."),
    "overtrading": BehaviorSpec(
        "overtrading", "Overtrading",
        "Do you take far more trades on some days than others?",
        Severity.MODERATE, "entry dates", 0.10,
        action="Cap yourself at a fixed number of entries per day — set it to "
               "your own median, not your best day."),
    "chasing": BehaviorSpec(
        "chasing", "Chasing extended moves",
        "Do you enter after the move has already happened?", Severity.MODERATE,
        "RSI at entry, or a coach review",
        action="Enter only on a retest of the level, never on the breakout "
               "candle itself. Let the ones that don't retest go."),
    "fomo_entries": BehaviorSpec(
        "fomo_entries", "FOMO entries",
        "Do you buy into unusually active, already-stretched moves?",
        Severity.MODERATE, "relative volume and RSI at entry", 0.10,
        action="When relative volume is above 2× and price is already extended, "
               "put the trade on a watchlist instead of an order ticket."),
    "hesitation": BehaviorSpec(
        "hesitation", "Hesitation",
        "Do you miss setups by entering late or not at all?", Severity.MINOR,
        "the delay between a signal appearing and your entry, which is not "
        "recorded — and the setups you skipped entirely, which by definition "
        "produce no trade"),
    "averaging_down": BehaviorSpec(
        "averaging_down", "Averaging down",
        "Do you add to losing positions?", Severity.SERIOUS,
        "a coach review of the order history",
        action="Ban adds below your entry price. Only add after a fresh "
               "confirmation signal in your direction."),
    "moving_stops": BehaviorSpec(
        "moving_stops", "Moving stops away from price",
        "Do you widen a stop once it is threatened?", Severity.SERIOUS,
        "a coach review of the order history",
        action="Make it a rule that a stop may only ever move toward profit. "
               "If the level was wrong, the size was wrong."),
    "trading_without_stops": BehaviorSpec(
        "trading_without_stops", "Trading without a stop",
        "Do you enter without a resting protective order?", Severity.SERIOUS,
        "a coach review of the order history", 0.10,
        action="Place the protective stop within 60 seconds of the fill, before "
               "doing anything else."),
    "no_target_defined": BehaviorSpec(
        "no_target_defined", "No profit target defined",
        "Do you enter without deciding where you get paid?", Severity.MINOR,
        "a coach review of the order history", 0.30,
        action="Write the target on the ticket before the entry. If you can't "
               "name one, the trade isn't ready."),
    "cutting_winners_early": BehaviorSpec(
        "cutting_winners_early", "Cutting winners short",
        "Do you close working trades before they pay?", Severity.MODERATE,
        "a coach review, plus hold times",
        action="Sell half at your first target and trail a stop on the rest "
               "instead of closing the whole position."),
    "letting_losers_run": BehaviorSpec(
        "letting_losers_run", "Letting losers run",
        "Do you hold losers past the point the thesis failed?", Severity.SERIOUS,
        "a coach review, plus hold times",
        action="Exit at the invalidation level, and set a hard -50% premium "
               "backstop on long options."),
    "inconsistent_sizing": BehaviorSpec(
        "inconsistent_sizing", "Inconsistent position sizing",
        "Is your position size steady, or does it lurch?", Severity.MODERATE,
        "premium paid per trade", 0.20,
        action="Fix one position size and use it for the next 20 trades. Vary "
               "your selectivity, not your size."),
    "oversizing": BehaviorSpec(
        "oversizing", "Oversized positions",
        "Do you commit too much of the account to one trade?", Severity.SERIOUS,
        "a coach review with account equity at entry", 0.10,
        action="Cap single-trade premium at a low single-digit percent of the "
               "account until the last 20 trades are profitable."),
    "open_chop_trading": BehaviorSpec(
        "open_chop_trading", "Trading the opening chop",
        "Do you trade the first fifteen minutes?", Severity.MINOR,
        "entry timestamps", 0.20,
        action="No entries before 9:45 ET. Use the first 15 minutes to mark the "
               "opening range instead of trading inside it."),
    "entering_too_early": BehaviorSpec(
        "entering_too_early", "Entering before confirmation",
        "Do you anticipate setups instead of letting them complete?",
        Severity.MODERATE, "a coach review with the entry setup grade",
        action="Skip entries while the setup grade reads 'poor'. Let the break "
               "of structure print before committing."),
    "counter_trend_trading": BehaviorSpec(
        "counter_trend_trading", "Fighting the higher-timeframe trend",
        "Do you trade against the dominant trend?", Severity.MODERATE,
        "the higher-timeframe trend at entry",
        action="For two weeks, take only trades whose direction matches the "
               "higher-timeframe trend."),
    "theta_neglect": BehaviorSpec(
        "theta_neglect", "Short-dated contracts",
        "Do you buy expiries that decay faster than your thesis resolves?",
        Severity.MODERATE, "days to expiration at entry", 0.20,
        action="Prefer 7–45 DTE unless you intend a same-day exit — and write "
               "that exit time on the ticket."),
    "iv_neglect": BehaviorSpec(
        "iv_neglect", "Buying expensive volatility",
        "Do you buy premium when implied volatility is already elevated?",
        Severity.MODERATE, "implied volatility at entry", 0.20,
        action="Check IV in the chain before every long-premium entry, and note "
               "it in the journal."),
    "lottery_tickets": BehaviorSpec(
        "lottery_tickets", "Far-OTM strikes",
        "Do you buy strikes that need a tail event to pay?", Severity.MODERATE,
        "contract delta at entry", 0.15,
        action="Restrict yourself to 0.30+ delta strikes for the next 15 trades."),
    "tilt_after_loss": BehaviorSpec(
        "tilt_after_loss", "Tilt after a loss",
        "Does your next trade get worse after you lose?", Severity.SERIOUS,
        "at least a few losses with a trade after each",
        action="After any loss, close the platform for 15 minutes and re-read "
               "your plan before the next entry."),
    "overconfidence_after_wins": BehaviorSpec(
        "overconfidence_after_wins", "Overconfidence after wins",
        "Do you size up or loosen standards on a hot streak?", Severity.MODERATE,
        "at least a few win streaks with a trade after each",
        action="Keep size fixed regardless of streak. A hot streak is evidence "
               "about the market, not about you."),
    "ignoring_the_plan": BehaviorSpec(
        "ignoring_the_plan", "Trading setups the analysis rated poor",
        "Do you take trades your own system advised against?", Severity.SERIOUS,
        "the setup grade captured at entry", 0.10,
        action="Treat a 'poor' setup grade as a hard veto for the next 20 "
               "trades, and journal each one you skipped."),
}


@dataclass(frozen=True, slots=True)
class _Ctx:
    """Everything a detector may look at. Precomputed once per run so twenty
    detectors don't each walk the history."""

    facts: tuple[TradeFact, ...]          # the window under analysis
    reviewed: tuple[TradeFact, ...]       # facts carrying a coach review
    baseline_expectancy: float | None
    baseline_win_rate: float | None
    prior_loss_gap: dict[str, float]      # trade_id -> minutes since last losing exit
    after_loss: tuple[TradeFact, ...]     # trades immediately following a loss
    after_win_streak: tuple[TradeFact, ...]
    median_outlay: float | None


def _cited(facts: list[TradeFact]) -> tuple[str, ...]:
    """The trade IDs a finding carries, capped at `MAX_CITED`.

    The cap takes the MOST RECENT occurrences, not the first. Two reasons: the
    journal's per-trade view can only flag a trade as evidence if the finding
    names it, and a user is far more likely to open a recent trade than their
    fiftieth-oldest; and recent occurrences are the ones that answer "am I still
    doing this". Taking the head of the list instead means the newest evidence
    is the evidence that disappears, which is exactly backwards.
    """
    return tuple(f.trade_id for f in facts[-MAX_CITED:])


def _impact(all_facts: tuple[TradeFact, ...], affected: list[TradeFact],
            basis: str) -> Impact | None:
    """Expectancy recomputed with `affected` removed from the same history.

    Returns None unless something is left to compare against: removing every
    trade leaves no counterfactual, and removing none leaves nothing to say.
    """
    if not affected or not all_facts:
        return None
    affected_ids = {f.trade_id for f in affected}
    remaining = [f.pnl for f in all_facts if f.trade_id not in affected_ids]
    if not remaining or len(remaining) == len(all_facts):
        return None
    baseline = stats.expectancy([f.pnl for f in all_facts])
    adjusted = stats.expectancy(remaining)
    if baseline is None or adjusted is None:
        return None
    return Impact(
        metric="expectancy", baseline=round(baseline, 2),
        adjusted=round(adjusted, 2), delta=round(adjusted - baseline, 2),
        unit="$", basis=basis, sample=len(all_facts),
    )


def _confidence(occurrences: int, sample: int) -> Confidence:
    """Confidence in a behavioral claim: the sample must be big enough AND the
    behavior must have repeated. Two occurrences in eighty trades is a real but
    low-confidence tendency; two in four is not high confidence just because the
    rate is 50%."""
    if occurrences < MIN_OCCURRENCES or sample < stats.MIN_SAMPLE_ANY:
        return Confidence.NONE if occurrences == 0 else Confidence.LOW
    by_sample = stats.sample_confidence(sample)
    if occurrences >= 6:
        by_count = Confidence.HIGH
    elif occurrences >= 3:
        by_count = Confidence.MEDIUM
    else:
        by_count = Confidence.LOW
    return stats.combine_confidence(by_sample, by_count)


def _severity(spec: BehaviorSpec, detected: bool, rate: float) -> Severity:
    if not detected:
        return Severity.POSITIVE
    if rate < spec.rate_floor:
        return Severity.MINOR if spec.severity.rank > 2 else Severity.INFO
    return spec.severity


def _unassessable(spec: BehaviorSpec, sample: int,
                  reason: str = "") -> BehaviorFinding:
    return BehaviorFinding(
        id=spec.id, label=spec.label, assessable=False, detected=False,
        severity=Severity.INFO, confidence=Confidence.NONE,
        occurrences=0, sample=sample, rate=0.0,
        summary=f"Not enough data to judge {spec.label.lower()}.",
        unassessable_reason=reason or f"Requires {spec.needs}.",
    )


def _finding(spec: BehaviorSpec, ctx: _Ctx, affected: list[TradeFact],
             sample: int, summary_detected: str, summary_clean: str,
             extra_evidence: tuple[Evidence, ...] = (),
             impact_basis: str = "") -> BehaviorFinding:
    """Assemble a finding from an occurrence list. The single place rate,
    confidence, severity, citation and impact are decided, so twenty detectors
    can't develop twenty slightly different ideas of what "detected" means."""
    occurrences = len(affected)
    rate = occurrences / sample if sample else 0.0
    detected = occurrences >= MIN_OCCURRENCES
    confidence = _confidence(occurrences, sample)
    evidence = (Evidence(
        label=f"trades showing {spec.label.lower()}",
        value=occurrences, sample=sample,
        detail=f"{occurrences} of {sample} trades ({rate:.0%})",
        trade_ids=_cited(affected),
    ),) + extra_evidence

    impact = None
    if detected:
        # The window matters and is stated: behavioural analysis runs over the
        # recent window, so this baseline is NOT the lifetime expectancy shown
        # on the dashboard. Saying so is the difference between a comparison the
        # user can check and two numbers that look like a contradiction.
        impact = _impact(
            ctx.facts, affected,
            impact_basis or f"your expectancy across the last "
                            f"{len(ctx.facts)} analysed trades, with the "
                            f"{occurrences} affected trades removed")

    return BehaviorFinding(
        id=spec.id, label=spec.label, assessable=True, detected=detected,
        severity=_severity(spec, detected, rate), confidence=confidence,
        occurrences=occurrences, sample=sample, rate=rate,
        summary=summary_detected if detected else summary_clean,
        evidence=evidence, impact=impact,
    )


# ── individual detectors ─────────────────────────────────────────────────────
# Each takes the context and returns exactly one finding. They are registered in
# DETECTORS at the bottom; adding a behavior means adding a spec and a function.


def _detect_revenge(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["revenge_trading"]
    sample = len(ctx.facts)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample, "Requires at least a few trades.")
    affected = [f for f in ctx.facts
                if ctx.prior_loss_gap.get(f.trade_id, 1e9) <= REVENGE_WINDOW_MIN]
    gaps = [ctx.prior_loss_gap[f.trade_id] for f in affected]
    extra: tuple[Evidence, ...] = ()
    if gaps:
        extra = (Evidence("median minutes after the losing exit",
                          round(stats.median(gaps) or 0.0, 1), len(gaps),
                          "how quickly the next entry followed"),)
        after_pnl = stats.expectancy([f.pnl for f in affected])
        if after_pnl is not None and ctx.baseline_expectancy is not None:
            extra += (Evidence("expectancy of those entries",
                               round(after_pnl, 2), len(affected),
                               f"vs {ctx.baseline_expectancy:+.2f} overall"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} entries came within {REVENGE_WINDOW_MIN:.0f} minutes "
        f"of a losing exit — the classic revenge-trade signature.",
        "No entries followed a loss inside the reactive window.",
        extra)


def _detect_overtrading(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["overtrading"]
    sample = len(ctx.facts)
    by_day: dict[str, list[TradeFact]] = {}
    for f in ctx.facts:
        by_day.setdefault(f.entry_date, []).append(f)
    if len(by_day) < stats.MIN_SAMPLE_LOW:
        return _unassessable(
            spec, sample,
            "Requires trades on at least five separate days before a normal "
            "day's volume can be established.")
    counts = [len(v) for v in by_day.values()]
    typical = stats.median(counts) or 1.0
    # A heavy day is one that doubles the trader's own typical day AND clears an
    # absolute floor — doubling a one-trade median is two trades, which is not
    # overtrading by any standard.
    threshold = max(typical * 2, typical + 2, 3)
    heavy_days = {d: v for d, v in by_day.items() if len(v) >= threshold}
    affected = [f for d in heavy_days for f in heavy_days[d]]
    extra = (
        Evidence("typical trades per active day", round(typical, 1),
                 len(by_day), "your own median"),
        Evidence("heavy days", len(heavy_days), len(by_day),
                 f"days with {threshold:.0f}+ entries"),
    )
    heavy_exp = stats.expectancy([f.pnl for f in affected])
    if heavy_exp is not None and ctx.baseline_expectancy is not None:
        extra += (Evidence("expectancy on heavy days", round(heavy_exp, 2),
                           len(affected),
                           f"vs {ctx.baseline_expectancy:+.2f} overall"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(heavy_days)} days carried {threshold:.0f}+ entries against a "
        f"typical {typical:.0f} — volume spikes on a minority of days.",
        "Trade volume is steady day to day.",
        extra,
        impact_basis="your expectancy over the same history with your "
                     "highest-volume days removed")


def _detect_chasing(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["chasing"]
    candidates = [f for f in ctx.facts if f.reviewed or f.rsi is not None]
    sample = len(candidates)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in candidates
                if f.has_mistake("chased_entry") or _stretched(f)]
    rsis = [f.rsi for f in affected if f.rsi is not None]
    extra: tuple[Evidence, ...] = ()
    if rsis:
        extra = (Evidence("median RSI at those entries",
                          round(stats.median(rsis) or 0.0, 1), len(rsis),
                          "already extended in the trade's own direction"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} entries were taken into an already-extended move.",
        "Entries are taken before the move is stretched.", extra)


def _stretched(f: TradeFact) -> bool:
    """RSI already extended in the direction being traded — the coach's own
    threshold, applied across the population."""
    if f.rsi is None:
        return False
    return ((f.rsi >= 72 and f.direction == "long")
            or (f.rsi <= 28 and f.direction == "short"))


def _detect_fomo(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["fomo_entries"]
    candidates = [f for f in ctx.facts if f.rvol is not None and f.rsi is not None]
    sample = len(candidates)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    # Distinct from plain chasing: the move is not merely extended, it is
    # unusually *busy*, which is what crowd-driven entries look like from data.
    affected = [f for f in candidates if f.rvol >= 2.0 and _stretched(f)]
    extra = (Evidence("median relative volume at those entries",
                      round(stats.median([f.rvol for f in affected]) or 0.0, 2),
                      len(affected), "1.0 is an average session"),) if affected else ()
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} entries bought into a stretched move on 2×+ normal "
        f"volume — buying the crowd rather than the setup.",
        "No entries chased unusually busy, already-extended moves.", extra)


def _detect_hesitation(ctx: _Ctx) -> BehaviorFinding:
    """Declined, always, and deliberately.

    Hesitation is the gap between a setup appearing and the trader acting, plus
    the setups they skipped entirely. The first is not recorded anywhere in this
    system; the second produces no trade and therefore no data at all. A
    detector that guessed at it from hold times or entry RSI would be inventing
    a psychological claim out of unrelated numbers — precisely what this package
    exists not to do.
    """
    return _unassessable(BEHAVIORS["hesitation"], len(ctx.facts))


def _tag_detector(behavior_id: str, tag: str, detected_text: str,
                  clean_text: str) -> Callable[[_Ctx], BehaviorFinding]:
    """Build a detector for a behavior whose only reliable evidence is a coach
    mistake tag observed on the order history. The sample is restricted to
    reviewed trades — an unreviewed trade cannot contribute a negative."""
    def detect(ctx: _Ctx) -> BehaviorFinding:
        spec = BEHAVIORS[behavior_id]
        sample = len(ctx.reviewed)
        if sample < stats.MIN_SAMPLE_ANY:
            return _unassessable(spec, sample)
        affected = [f for f in ctx.reviewed if f.has_mistake(tag)]
        return _finding(spec, ctx, affected, sample,
                        detected_text.format(n=len(affected), sample=sample),
                        clean_text)
    return detect


def _detect_moving_stops(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["moving_stops"]
    observed = [f for f in ctx.reviewed if f.widened_stop is not None]
    sample = len(observed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in observed if f.widened_stop or f.has_mistake("moved_stop")]
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} trades had their stop widened once price threatened "
        f"it — converting a planned loss into an unplanned one.",
        "Stops were never widened against a position.")


def _detect_no_stop(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["trading_without_stops"]
    observed = [f for f in ctx.reviewed if f.had_stop is not None]
    sample = len(observed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in observed if not f.had_stop]
    extra: tuple[Evidence, ...] = ()
    unprotected_loss = [f.pnl for f in affected if f.pnl < 0]
    protected_loss = [f.pnl for f in observed
                      if f.had_stop and f.pnl < 0]
    if unprotected_loss and protected_loss:
        extra = (Evidence(
            "average loss without a stop",
            round(stats.mean(unprotected_loss) or 0.0, 2), len(unprotected_loss),
            f"vs {stats.mean(protected_loss):.2f} with one"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} of {sample} trades ran with no resting protective "
        f"order at any point.",
        "Every reviewed trade carried a resting protective stop.", extra)


def _detect_no_target(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["no_target_defined"]
    observed = [f for f in ctx.reviewed if f.had_target is not None]
    sample = len(observed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in observed if not f.had_target]
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} of {sample} trades were entered with no profit target "
        f"defined — the exit decision was left to the moment.",
        "Every reviewed trade had a target defined in advance.")


def _detect_cut_winners(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["cutting_winners_early"]
    sample = len(ctx.reviewed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in ctx.reviewed if f.has_mistake("cut_winner_early")]
    extra = _hold_asymmetry_evidence(ctx)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} winners were closed while the setup was still "
        f"working.",
        "Winners were allowed to complete.", extra)


def _detect_held_losers(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["letting_losers_run"]
    sample = len(ctx.reviewed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in ctx.reviewed if f.has_mistake("held_loser")]
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} losers were carried past the point the thesis had "
        f"already failed.",
        "Losers were cut at invalidation.", _hold_asymmetry_evidence(ctx))


def _hold_asymmetry_evidence(ctx: _Ctx) -> tuple[Evidence, ...]:
    """Winner-vs-loser hold time, the structural tell behind both exit
    behaviors: holding losers longer than winners is the arithmetic shape of
    hoping on losses and flinching on gains."""
    win_holds = [f.hold_minutes for f in ctx.facts if f.pnl > 0 and f.hold_minutes > 0]
    loss_holds = [f.hold_minutes for f in ctx.facts if f.pnl < 0 and f.hold_minutes > 0]
    if len(win_holds) < 2 or len(loss_holds) < 2:
        return ()
    mw, ml = stats.mean(win_holds), stats.mean(loss_holds)
    if mw is None or not ml:
        return ()
    return (Evidence(
        "winner vs loser hold time", round(mw / ml, 2),
        len(win_holds) + len(loss_holds),
        f"winners held {mw:.0f} min, losers {ml:.0f} min"),)


def _detect_inconsistent_sizing(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["inconsistent_sizing"]
    sized = [f for f in ctx.facts if f.outlay > 0]
    sample = len(sized)
    if sample < stats.MIN_SAMPLE_LOW:
        return _unassessable(
            spec, sample, "Requires at least five trades with a recorded "
                          "premium outlay.")
    outlays = [f.outlay for f in sized]
    typical = stats.median(outlays) or 0.0
    consistency = stats.consistency(outlays)
    # An outlier is a trade more than half again the trader's own typical size.
    affected = [f for f in sized if typical > 0 and f.outlay > typical * 1.5]
    extra = (
        Evidence("typical position size", round(typical, 2), sample,
                 "your own median premium outlay"),
        Evidence("sizing consistency", consistency, sample,
                 "100 is identical every trade"),
    )
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} trades were sized more than 50% above your typical "
        f"{typical:,.0f} — one impulsive size can undo ten disciplined ones.",
        "Position sizing is steady.", extra)


def _detect_open_chop(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["open_chop_trading"]
    timed = [f for f in ctx.facts if f.hour_et is not None]
    sample = len(timed)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in timed
                if f.hour_et == 9 and (f.minute_et or 0) >= 30 and (f.minute_et or 0) < 45]
    extra: tuple[Evidence, ...] = ()
    chop_exp = stats.expectancy([f.pnl for f in affected])
    if chop_exp is not None and ctx.baseline_expectancy is not None:
        extra = (Evidence("expectancy in the first 15 minutes",
                          round(chop_exp, 2), len(affected),
                          f"vs {ctx.baseline_expectancy:+.2f} overall"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} entries landed in the opening 15 minutes, where the "
        f"session prints fake breakouts in both directions.",
        "The opening range is left to settle before entering.", extra)


def _detect_counter_trend(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["counter_trend_trading"]
    known = [f for f in ctx.facts
             if f.reviewed or (f.htf_trend or "").lower() in ("up", "down")]
    sample = len(known)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in known if f.has_mistake("counter_trend") or _against(f)]
    with_trend = [f for f in known if not _against(f) and f.htf_trend]
    extra: tuple[Evidence, ...] = ()
    against_exp = stats.expectancy([f.pnl for f in affected])
    with_exp = stats.expectancy([f.pnl for f in with_trend])
    if against_exp is not None and with_exp is not None:
        extra = (Evidence("expectancy against the trend", round(against_exp, 2),
                          len(affected), f"vs {with_exp:+.2f} with it"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} trades were taken against the higher-timeframe trend.",
        "Trades align with the higher-timeframe trend.", extra)


def _against(f: TradeFact) -> bool:
    trend = (f.htf_trend or "").lower()
    if trend not in ("up", "down"):
        return False
    return (trend == "up" and f.direction == "short") or \
           (trend == "down" and f.direction == "long")


def _threshold_detector(behavior_id: str, field: str,
                        predicate: Callable[[float], bool],
                        detected_text: str, clean_text: str,
                        evidence_label: str
                        ) -> Callable[[_Ctx], BehaviorFinding]:
    """Build a detector over one numeric contract field (DTE, IV, delta).

    The sample is restricted to trades that actually recorded the field, which
    is what keeps a partially-instrumented history from reporting a flattering
    0% rate simply because most trades never captured the number.
    """
    def detect(ctx: _Ctx) -> BehaviorFinding:
        spec = BEHAVIORS[behavior_id]
        known = [f for f in ctx.facts if getattr(f, field) is not None]
        sample = len(known)
        if sample < stats.MIN_SAMPLE_ANY:
            return _unassessable(spec, sample)
        affected = [f for f in known if predicate(float(getattr(f, field)))]
        extra: tuple[Evidence, ...] = ()
        if affected:
            values = [float(getattr(f, field)) for f in affected]
            extra = (Evidence(evidence_label,
                              round(stats.median(values) or 0.0, 3),
                              len(affected), "median across those trades"),)
            exp = stats.expectancy([f.pnl for f in affected])
            rest = stats.expectancy([f.pnl for f in known if f not in affected])
            if exp is not None and rest is not None:
                extra += (Evidence("expectancy of those trades", round(exp, 2),
                                   len(affected), f"vs {rest:+.2f} for the rest"),)
        return _finding(spec, ctx, affected, sample,
                        detected_text.format(n=len(affected), sample=sample),
                        clean_text, extra)
    return detect


def _detect_tilt(ctx: _Ctx) -> BehaviorFinding:
    """Does the trade *after* a loss underperform the trader's own baseline?

    This is the one detector whose finding is a comparison rather than a count,
    so `detected` needs both a material gap and enough post-loss trades for the
    gap to mean anything — otherwise every trader with three losses would be
    told they tilt.
    """
    spec = BEHAVIORS["tilt_after_loss"]
    cohort = list(ctx.after_loss)
    sample = len(ctx.facts)
    if len(cohort) < stats.MIN_SAMPLE_LOW:
        return _unassessable(
            spec, sample,
            "Requires at least five losses each followed by another trade.")
    cohort_exp = stats.expectancy([f.pnl for f in cohort])
    others = [f.pnl for f in ctx.facts
              if f.trade_id not in {c.trade_id for c in cohort}]
    other_exp = stats.expectancy(others)
    if cohort_exp is None or other_exp is None:
        return _unassessable(spec, sample)

    worse = cohort_exp < other_exp
    # "Materially" = the gap is at least a quarter of the typical result size,
    # so a few cents of difference across a noisy sample is not called tilt.
    scale = max(abs(other_exp), 1e-9)
    material = worse and (other_exp - cohort_exp) / scale >= 0.25
    cohort_sizes = [f.outlay for f in cohort if f.outlay > 0]
    size_up = None
    if cohort_sizes and ctx.median_outlay:
        size_up = (stats.median(cohort_sizes) or 0.0) / ctx.median_outlay

    evidence = (
        Evidence("expectancy of the trade after a loss", round(cohort_exp, 2),
                 len(cohort), f"vs {other_exp:+.2f} for every other trade",
                 _cited(cohort)),
    )
    if size_up is not None:
        evidence += (Evidence("size of the trade after a loss",
                              round(size_up, 2), len(cohort_sizes),
                              "as a multiple of your typical position"),)

    confidence = _confidence(len(cohort), sample) if material else Confidence.NONE
    return BehaviorFinding(
        id=spec.id, label=spec.label, assessable=True, detected=material,
        severity=spec.severity if material else Severity.POSITIVE,
        confidence=confidence if material else stats.sample_confidence(len(cohort)),
        occurrences=len(cohort) if material else 0, sample=sample,
        rate=len(cohort) / sample if sample and material else 0.0,
        summary=(
            f"The trade taken straight after a loss averages {cohort_exp:+.2f} "
            f"against {other_exp:+.2f} everywhere else — the loss is costing you "
            f"the next trade as well." if material else
            f"Trades taken after a loss perform in line with the rest "
            f"({cohort_exp:+.2f} vs {other_exp:+.2f})."),
        evidence=evidence,
        impact=_impact(ctx.facts, cohort,
                       "your expectancy across the recent analysed window with "
                       "the trade "
                       "immediately following each loss removed") if material else None,
    )


def _detect_overconfidence(ctx: _Ctx) -> BehaviorFinding:
    """Size and result of the trade taken after two or more consecutive wins."""
    spec = BEHAVIORS["overconfidence_after_wins"]
    cohort = list(ctx.after_win_streak)
    sample = len(ctx.facts)
    if len(cohort) < stats.MIN_SAMPLE_LOW:
        return _unassessable(
            spec, sample,
            "Requires at least five win streaks each followed by another trade.")
    sizes = [f.outlay for f in cohort if f.outlay > 0]
    cohort_exp = stats.expectancy([f.pnl for f in cohort])
    others = [f.pnl for f in ctx.facts
              if f.trade_id not in {c.trade_id for c in cohort}]
    other_exp = stats.expectancy(others)
    size_ratio = ((stats.median(sizes) or 0.0) / ctx.median_outlay
                  if sizes and ctx.median_outlay else None)

    sized_up = size_ratio is not None and size_ratio >= 1.25
    underperforms = (cohort_exp is not None and other_exp is not None
                     and cohort_exp < other_exp)
    detected = sized_up and underperforms

    evidence: tuple[Evidence, ...] = ()
    if size_ratio is not None:
        evidence += (Evidence("size after a winning streak", round(size_ratio, 2),
                              len(sizes), "as a multiple of your typical position",
                              _cited(cohort)),)
    if cohort_exp is not None and other_exp is not None:
        evidence += (Evidence("expectancy after a winning streak",
                              round(cohort_exp, 2), len(cohort),
                              f"vs {other_exp:+.2f} otherwise"),)

    return BehaviorFinding(
        id=spec.id, label=spec.label, assessable=True, detected=detected,
        severity=spec.severity if detected else Severity.POSITIVE,
        confidence=(_confidence(len(cohort), sample) if detected
                    else stats.sample_confidence(len(cohort))),
        occurrences=len(cohort) if detected else 0, sample=sample,
        rate=len(cohort) / sample if sample and detected else 0.0,
        summary=(
            f"After two or more wins you size up {size_ratio:.1f}× and those "
            f"trades average {cohort_exp:+.2f} against {other_exp:+.2f} "
            f"otherwise." if detected else
            "Position size and results hold steady after a winning streak."),
        evidence=evidence,
        impact=_impact(ctx.facts, cohort,
                       "your expectancy across the recent analysed window with "
                       "the trade "
                       "following each winning streak removed") if detected else None,
    )


def _detect_ignoring_plan(ctx: _Ctx) -> BehaviorFinding:
    spec = BEHAVIORS["ignoring_the_plan"]
    graded = [f for f in ctx.facts if f.setup_quality
              and f.setup_quality != "unknown"]
    sample = len(graded)
    if sample < stats.MIN_SAMPLE_ANY:
        return _unassessable(spec, sample)
    affected = [f for f in graded if f.setup_quality == "poor"]
    extra: tuple[Evidence, ...] = ()
    poor_exp = stats.expectancy([f.pnl for f in affected])
    good = [f.pnl for f in graded if f.setup_quality in ("excellent", "good")]
    good_exp = stats.expectancy(good)
    if poor_exp is not None and good_exp is not None:
        extra = (Evidence("expectancy on setups graded poor", round(poor_exp, 2),
                          len(affected),
                          f"vs {good_exp:+.2f} on good or excellent ones"),)
    return _finding(
        spec, ctx, affected, sample,
        f"{len(affected)} trades were taken on setups your own analysis graded "
        f"poor.",
        "Trades were taken on setups the analysis supported.", extra)


DETECTORS: dict[str, Callable[[_Ctx], BehaviorFinding]] = {
    "revenge_trading": _detect_revenge,
    "overtrading": _detect_overtrading,
    "chasing": _detect_chasing,
    "fomo_entries": _detect_fomo,
    "hesitation": _detect_hesitation,
    "averaging_down": _tag_detector(
        "averaging_down", "averaged_down",
        "{n} trades were added to while already losing — doubling theta burn "
        "on a thesis the market was disputing.",
        "No position was added to while losing."),
    "moving_stops": _detect_moving_stops,
    "trading_without_stops": _detect_no_stop,
    "no_target_defined": _detect_no_target,
    "cutting_winners_early": _detect_cut_winners,
    "letting_losers_run": _detect_held_losers,
    "inconsistent_sizing": _detect_inconsistent_sizing,
    "oversizing": _tag_detector(
        "oversizing", "oversized",
        "{n} trades committed more than 5% of the account to a single premium.",
        "Position sizes stayed inside a sane share of the account."),
    "open_chop_trading": _detect_open_chop,
    "entering_too_early": _tag_detector(
        "entering_too_early", "no_confirmation",
        "{n} entries were taken before the setup completed.",
        "Entries waited for the setup to confirm."),
    "counter_trend_trading": _detect_counter_trend,
    "theta_neglect": _threshold_detector(
        "theta_neglect", "dte", lambda v: v < 5,
        "{n} of {sample} trades used contracts with under 5 days to expiration.",
        "Expirations left room for the thesis to work.",
        "median days to expiration"),
    "iv_neglect": _threshold_detector(
        "iv_neglect", "iv", lambda v: v > 0.60,
        "{n} of {sample} trades bought premium above 60% implied volatility.",
        "Premium was bought at reasonable implied volatility.",
        "median implied volatility"),
    "lottery_tickets": _threshold_detector(
        "lottery_tickets", "delta", lambda v: abs(v) < 0.25,
        "{n} of {sample} trades used strikes under 0.25 delta, which need an "
        "outsized move just to break even.",
        "Strikes were chosen with a workable delta.",
        "median delta"),
    "tilt_after_loss": _detect_tilt,
    "overconfidence_after_wins": _detect_overconfidence,
    "ignoring_the_plan": _detect_ignoring_plan,
}


def _build_context(facts: tuple[TradeFact, ...]) -> _Ctx:
    """Precompute the sequence-sensitive views every detector shares.

    `prior_loss_gap` is the interesting one: for each trade it is the minutes
    between the most recent *losing exit* and this trade's entry. Computed with
    a sorted list and a binary search rather than a nested scan, so a 100k-trade
    history costs O(n log n) instead of O(n²) — this was measured, not assumed
    (see `scripts/intelligence_benchmark.py`).
    """
    pnls = [f.pnl for f in facts]
    loss_exits = sorted(f.exit_ts for f in facts if f.pnl < 0)
    gap: dict[str, float] = {}
    for f in facts:
        idx = bisect.bisect_left(loss_exits, f.entry_ts)
        if idx > 0:
            delta = (f.entry_ts - loss_exits[idx - 1]).total_seconds() / 60.0
            if delta >= 0:
                gap[f.trade_id] = delta

    after_loss: list[TradeFact] = []
    after_streak: list[TradeFact] = []
    streak = 0
    previous: TradeFact | None = None
    for f in facts:
        if previous is not None:
            if previous.pnl < 0:
                after_loss.append(f)
            if streak >= 2:
                after_streak.append(f)
        if f.pnl > 0:
            streak += 1
        elif f.pnl < 0:
            streak = 0
        previous = f

    outlays = [f.outlay for f in facts if f.outlay > 0]
    return _Ctx(
        facts=facts,
        reviewed=tuple(f for f in facts if f.reviewed),
        baseline_expectancy=stats.expectancy(pnls),
        baseline_win_rate=stats.win_rate(pnls),
        prior_loss_gap=gap,
        after_loss=tuple(after_loss),
        after_win_streak=tuple(after_streak),
        median_outlay=stats.median(outlays),
    )


class BehaviorEngine:
    """Runs every registered detector over one window of trades."""

    def analyze(self, facts: tuple[TradeFact, ...] | list[TradeFact],
                previous: tuple[TradeFact, ...] | list[TradeFact] | None = None,
                ) -> tuple[BehaviorFinding, ...]:
        """Findings for `facts`, ordered worst-first.

        When `previous` (an earlier, comparable window) is supplied, each
        finding also carries a `Trend`: whether the rate of that behavior is
        falling, holding or rising. That is what turns "you revenge trade" into
        "you revenge trade, but half as often as last month" — the single most
        motivating thing a coaching system can say, and one that requires
        comparing like with like rather than re-reading all of history.
        """
        facts = tuple(facts)
        ctx = _build_context(facts)
        findings = [detect(ctx) for detect in DETECTORS.values()]

        if previous:
            prior = {f.id: f for f in
                     [d(_build_context(tuple(previous))) for d in DETECTORS.values()]}
            findings = [_with_trend(f, prior.get(f.id)) for f in findings]

        findings.sort(key=lambda f: (-f.severity.rank, -f.confidence.value,
                                     -f.rate, f.id))
        return tuple(findings)


def _with_trend(current: BehaviorFinding,
                previous: BehaviorFinding | None) -> BehaviorFinding:
    """Attach the direction of travel, comparing rate against rate.

    Both windows must be assessable and carry a real sample; comparing an
    assessable window against an unassessable one would read a data-coverage
    change as behavioral improvement, which is the most flattering possible lie.
    """
    if (previous is None or not current.assessable or not previous.assessable
            or previous.sample < stats.MIN_SAMPLE_ANY
            or current.sample < stats.MIN_SAMPLE_ANY):
        return current
    delta = current.rate - previous.rate
    if abs(delta) < 0.05:
        trend = Trend.STABLE
    else:
        trend = Trend.IMPROVING if delta < 0 else Trend.DECLINING
    return BehaviorFinding(
        id=current.id, label=current.label, assessable=current.assessable,
        detected=current.detected, severity=current.severity,
        confidence=current.confidence, occurrences=current.occurrences,
        sample=current.sample, rate=current.rate, summary=current.summary,
        evidence=current.evidence + (Evidence(
            "rate in the previous comparable window",
            round(previous.rate, 4), previous.sample,
            f"{previous.occurrences} of {previous.sample} trades"),),
        impact=current.impact,
        unassessable_reason=current.unassessable_reason, trend=trend,
    )
