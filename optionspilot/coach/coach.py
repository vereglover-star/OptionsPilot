"""TradeCoach — deterministic post-trade review for Human Mode.

Every manual round trip gets a structured review built from the same analysis
engine the AI trades with: a before / during / after breakdown, a mistake-tag
list, strengths, improvements, "what a professional would likely do", and a
score out of 100.

Scoring philosophy (deliberate, documented): the coach scores the PROCESS,
not the outcome. A winning trade taken with no stop, against the higher
timeframe, at a poor setup still scores badly; a disciplined loser scores
well. Rewarding outcomes teaches gambling; rewarding process teaches trading.

Inputs are plain dicts (entry/exit context snapshots captured by the
orchestrator near entry and at close, plus the order history for the
contract), so reviews are reproducible and testable without live objects.

Honest limits: emotions can't be observed directly — tags like revenge
trading and stop-moving are inferred from observable behaviour (timing after
a loss, order modifications). Context is sampled on the scan cycle nearest
entry (delayed data), and "what a pro would do" lines are curated heuristics,
not gospel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import TradeRecord

log = get_logger("journal")

# Mistake taxonomy -> (label, what a professional would likely do, exercise)
MISTAKES: dict[str, tuple[str, str, str]] = {
    "no_stop": (
        "Traded without a stop order",
        "Pros define the exit before the entry — a resting stop order, not a "
        "mental one, because mental stops fail exactly when they matter.",
        "For your next 10 trades, place the stop-loss order within 60 seconds "
        "of the fill, before doing anything else.",
    ),
    "moved_stop": (
        "Moved the stop against the position",
        "Pros widen targets, never stops. A stop that moves away from price "
        "converts a small planned loss into an unplanned large one.",
        "Rule: a stop may only ever move in the direction of profit. Review "
        "each trade where you touched the stop and write down why.",
    ),
    "averaged_down": (
        "Added to a losing position",
        "Pros scale into strength, not weakness. Averaging a losing long call "
        "doubles theta burn on a thesis the market is disputing.",
        "Ban adds below your entry price for 20 trades; only add after a new "
        "confirmation signal (fresh BOS in your direction).",
    ),
    "chased_entry": (
        "Chased an extended move",
        "Pros wait for the pullback or the retest; entering after the move is "
        "stretched buys the worst price with the widest stop distance.",
        "Practice in replay: only enter on a retest of the broken level, "
        "never on the breakout candle itself.",
    ),
    "counter_trend": (
        "Fought the higher-timeframe trend",
        "Pros treat the daily/4h trend as the current; counter-trend trades "
        "need exceptional setups and quicker exits.",
        "For 2 weeks, only take trades whose direction matches the "
        "higher-timeframe trend shown in the AI analysis.",
    ),
    "no_confirmation": (
        "Entered before confirmation",
        "Pros let the setup complete — a break of structure, a volume push — "
        "before committing. Anticipating saves pennies and costs dollars.",
        "Use the setup-quality readout: skip entries while it reads 'poor'.",
    ),
    "theta_ignored": (
        "Short-dated contract (theta risk)",
        "Pros buying sub-week expiries treat them as intraday scalps and are "
        "out the same day; holding them is paying rent on melting ice.",
        "Prefer 7–45 DTE unless you intend a same-day exit — write the exit "
        "time on the ticket before entering.",
    ),
    "high_iv_entry": (
        "Bought expensive volatility",
        "Pros check IV before buying premium; entering longs when IV is "
        "elevated means the underlying must move just to offset the crush.",
        "Check the IV column in the chain before every entry this week; note "
        "it in the journal.",
    ),
    "lottery_ticket": (
        "Far-OTM strike (lottery ticket)",
        "Pros buy deltas that pay on an ordinary favorable move (0.35–0.60); "
        "sub-0.20-delta contracts need a tail event to profit.",
        "Restrict yourself to 0.30+ delta strikes for the next 15 trades.",
    ),
    "oversized": (
        "Position too large for the account",
        "Pros size so a full premium loss is an annoyance, not an event — "
        "typically a low single-digit percent of equity per trade.",
        "Cap any single premium outlay at 5% of the account until your last "
        "20-trade win rate exceeds 50%.",
    ),
    "revenge_trade": (
        "Re-entered minutes after a loss",
        "Pros step away after a stop-out; the next trade taken in frustration "
        "is statistically the worst of the day.",
        "Enforce a 15-minute no-trade timer after every losing exit.",
    ),
    "open_chop": (
        "Traded the opening chop",
        "Pros let the first 15 minutes establish a range; the open prints "
        "fake breakouts in both directions.",
        "No entries before 9:45 ET for 2 weeks — watch what you would have "
        "done and journal it.",
    ),
    "held_loser": (
        "Rode the loser too far",
        "Pros cut at the invalidation level; past ~50% premium loss on a long "
        "option the trade has usually long since told you it failed.",
        "Set a hard rule: exit any long option at -50% premium, no exceptions, "
        "for the next month.",
    ),
    "cut_winner_early": (
        "Sold a working winner into strength",
        "Pros scale out — bank half, trail the rest — so winners can pay for "
        "the inevitable string of stop-outs.",
        "On the next 5 winners, sell half at your first target and trail a "
        "stop on the remainder instead of closing everything.",
    ),
}


@dataclass(frozen=True, slots=True)
class Finding:
    check: str            # what was examined
    passed: bool | None   # None = informational
    detail: str

    def to_dict(self) -> dict:
        return {"check": self.check, "passed": self.passed, "detail": self.detail}


@dataclass(slots=True)
class CoachReview:
    trade_id: str
    score: int                          # 0-100, process-based
    verdict: str                        # won | lost | scratch
    setup_quality: str
    summary: str
    before: list[Finding] = field(default_factory=list)
    during: list[Finding] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)      # taxonomy tags
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    pro_notes: list[str] = field(default_factory=list)
    ev_note: str = ""

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id, "score": self.score,
            "verdict": self.verdict, "setup_quality": self.setup_quality,
            "summary": self.summary,
            "before": [f.to_dict() for f in self.before],
            "during": [f.to_dict() for f in self.during],
            "after": self.after,
            "mistakes": self.mistakes,
            "mistake_labels": [MISTAKES[m][0] for m in self.mistakes
                               if m in MISTAKES],
            "strengths": self.strengths, "improvements": self.improvements,
            "pro_notes": self.pro_notes, "ev_note": self.ev_note,
        }


class TradeCoach:
    """Reviews one closed manual trade from its context snapshots.

    entry_context / exit_context shape (captured by the orchestrator):
      {"captured_ts", "spot", "confidence", "direction", "gate": {...},
       "entry_tf": {"rsi", "adx", "rvol", "pressure", "trend", "event",
                     "bars_since_event", "consolidating", "atr"},
       "htf_trend", "contract": {"dte", "delta", "iv", "spread_pct"},
       "hour_et", "minute_et"}
    orders: list of order dicts for this contract (OrderManager history).
    """

    def __init__(self, reviews_dir: str | Path, equity_at_entry: float = 0.0):
        self._dir = Path(reviews_dir)
        self._equity = equity_at_entry

    # ── main entry point ─────────────────────────────────────────────────────

    def review(
        self,
        trade: TradeRecord,
        entry_context: dict | None,
        exit_context: dict | None,
        orders: list[dict],
        recent_loss_minutes_before_entry: float | None = None,
        equity_at_entry: float = 0.0,
    ) -> CoachReview:
        equity = equity_at_entry or self._equity
        entry = entry_context or {}
        exits = exit_context or {}
        mistakes: list[str] = []
        strengths: list[str] = []

        before = self._before_checks(trade, entry, mistakes, strengths,
                                      recent_loss_minutes_before_entry, equity)
        during = self._during_checks(trade, orders, mistakes, strengths)
        after, verdict = self._after_analysis(trade, entry, exits, mistakes)

        quality = (entry.get("gate") or {}).get("setup_quality", "unknown")
        score = self._score(trade, entry, mistakes, strengths)
        improvements = [MISTAKES[m][2] for m in mistakes if m in MISTAKES]
        pro_notes = [MISTAKES[m][1] for m in mistakes if m in MISTAKES]

        review = CoachReview(
            trade_id=trade.id, score=score, verdict=verdict,
            setup_quality=quality,
            summary=self._summary(trade, verdict, quality, mistakes, score),
            before=before, during=during, after=after,
            mistakes=mistakes, strengths=strengths,
            improvements=improvements, pro_notes=pro_notes,
            ev_note=self._ev_note(entry),
        )
        self._persist(review)
        return review

    # ── before the trade ─────────────────────────────────────────────────────

    def _before_checks(self, trade, entry, mistakes, strengths,
                       loss_minutes, equity) -> list[Finding]:
        out: list[Finding] = []
        if not entry:
            out.append(Finding("entry context", None,
                               "no context snapshot captured near entry — "
                               "before-the-trade checks unavailable"))
            return out

        gate = entry.get("gate") or {}
        quality = gate.get("setup_quality", "unknown")
        # signed confidence: + when the engine agreed with the user's direction
        agreed = entry.get("direction") == trade.direction.value
        conf = entry.get("confidence", 0.0)
        signed = conf if agreed else -conf

        good_setup = quality in ("excellent", "good") and agreed
        out.append(Finding(
            "setup quality", good_setup if quality != "unknown" else None,
            f"engine read the setup as {quality} "
            f"({'same' if agreed else 'OPPOSITE'} direction as your trade, "
            f"confidence {conf:.0f}%)"))
        if quality == "poor" or not agreed and conf >= 40:
            mistakes.append("no_confirmation" if quality == "poor"
                            else "counter_trend")
        elif good_setup:
            strengths.append(f"entered on a {quality} setup the analysis agreed with")

        htf = entry.get("htf_trend", "unknown")
        with_trend = ((htf == "up" and trade.direction.value == "long")
                      or (htf == "down" and trade.direction.value == "short"))
        out.append(Finding("trend confirmation",
                           with_trend if htf != "unknown" else None,
                           f"higher-timeframe trend was {htf}"))
        if htf in ("up", "down") and not with_trend \
                and "counter_trend" not in mistakes:
            mistakes.append("counter_trend")
        elif with_trend:
            strengths.append("traded with the higher-timeframe trend")

        tf = entry.get("entry_tf") or {}
        rsi = tf.get("rsi")
        if rsi is not None:
            stretched = (rsi >= 72 and trade.direction.value == "long") or \
                        (rsi <= 28 and trade.direction.value == "short")
            out.append(Finding("entry not chased", not stretched,
                               f"RSI at entry: {rsi:.0f}"))
            if stretched:
                mistakes.append("chased_entry")

        rvol = tf.get("rvol")
        if rvol is not None:
            out.append(Finding("volume sufficient", rvol >= 0.7,
                               f"relative volume {rvol:.1f}x at entry"))

        c = entry.get("contract") or {}
        dte = c.get("dte")
        if dte is not None:
            out.append(Finding("expiration appropriate", dte >= 5,
                               f"{dte} days to expiration"))
            if dte < 5:
                mistakes.append("theta_ignored")
        iv = c.get("iv")
        if iv:
            out.append(Finding("IV reasonable", iv <= 0.60,
                               f"implied volatility {iv:.0%} at entry"))
            if iv > 0.60:
                mistakes.append("high_iv_entry")
        delta = c.get("delta")
        if delta is not None and delta != 0:
            ok = abs(delta) >= 0.25
            out.append(Finding("strike selection", ok,
                               f"delta {abs(delta):.2f} "
                               f"({'tradeable' if ok else 'far OTM'})"))
            if not ok:
                mistakes.append("lottery_ticket")

        if equity > 0:
            outlay = trade.entry_price * 100 * trade.quantity
            pct = outlay / equity * 100
            out.append(Finding("position sizing", pct <= 5.0,
                               f"premium outlay {pct:.1f}% of the account"))
            if pct > 5.0:
                mistakes.append("oversized")
            else:
                strengths.append(f"sized sanely ({pct:.1f}% of account)")

        hour = entry.get("hour_et")
        minute = entry.get("minute_et", 0)
        if hour is not None:
            in_chop = hour == 9 and minute < 45
            out.append(Finding("avoided opening chop", not in_chop,
                               f"entered {hour:02d}:{minute:02d} ET"))
            if in_chop:
                mistakes.append("open_chop")

        if loss_minutes is not None and loss_minutes <= 15:
            out.append(Finding("emotional state", False,
                               f"entered {loss_minutes:.0f} min after a losing "
                               f"exit — revenge-trade pattern"))
            mistakes.append("revenge_trade")
        return out

    # ── during the trade ─────────────────────────────────────────────────────

    def _during_checks(self, trade, orders, mistakes, strengths) -> list[Finding]:
        out: list[Finding] = []
        protective = [o for o in orders
                      if o.get("kind") in ("stop_loss", "trailing_stop")
                      and o.get("side") == "sell_to_close"]
        targets = [o for o in orders if o.get("kind") in ("take_profit", "limit")
                   and o.get("side") == "sell_to_close"]
        out.append(Finding("stop in place", bool(protective),
                           f"{len(protective)} protective order(s) during the trade"
                           if protective else "no stop order was ever placed"))
        if not protective:
            mistakes.append("no_stop")
        else:
            strengths.append("protected the position with a resting stop")
        if targets:
            strengths.append("defined a profit target in advance")
        out.append(Finding("profit target defined", bool(targets),
                           f"{len(targets)} target order(s)" if targets
                           else "no take-profit / exit limit was placed"))

        # moved stop against the position: successive stop_loss orders on the
        # same contract with a worse level (lower for calls, higher for puts)
        stops = [o for o in orders if o.get("kind") == "stop_loss"]
        stops = [o for o in stops if o.get("stop_level")]
        if len(stops) >= 2:
            levels = [o["stop_level"] for o in stops]
            worse = (any(b < a for a, b in zip(levels, levels[1:]))
                     if trade.direction.value == "long"
                     else any(b > a for a, b in zip(levels, levels[1:])))
            out.append(Finding("stop discipline", not worse,
                               f"stop levels over time: {levels}"))
            if worse:
                mistakes.append("moved_stop")

        buys = [o for o in orders if o.get("side") == "buy_to_open"
                and o.get("status") == "filled"]
        if len(buys) >= 2:
            prices = [o.get("fill_price") or 0 for o in buys]
            if prices[0] and min(prices[1:]) < prices[0] * 0.9:
                out.append(Finding("no averaging down", False,
                                   f"added at {min(prices[1:]):.2f} after "
                                   f"opening at {prices[0]:.2f}"))
                mistakes.append("averaged_down")
        return out

    # ── after the trade ──────────────────────────────────────────────────────

    def _after_analysis(self, trade, entry, exits, mistakes
                        ) -> tuple[list[str], str]:
        pnl = trade.pnl
        outlay = trade.entry_price * 100 * trade.quantity
        pct = pnl / outlay * 100 if outlay else 0.0
        verdict = "won" if pct > 2 else "lost" if pct < -2 else "scratch"
        lines = [
            f"P/L {pnl:+.2f} ({pct:+.1f}% of premium) over "
            f"{trade.hold_minutes:.0f} minutes.",
        ]
        entry_spot = entry.get("spot") or 0
        exit_spot = exits.get("spot") or 0
        if entry_spot and exit_spot:
            move = (exit_spot / entry_spot - 1) * 100
            helped = (move > 0) == (trade.direction.value == "long")
            lines.append(
                f"The underlying moved {move:+.2f}% "
                f"({'with' if helped else 'against'} the position) — the trade "
                f"{verdict} primarily because of "
                f"{'direction' if abs(move) > 0.15 else 'premium decay/spread, not direction'}."
            )
        if verdict == "lost" and pct <= -50:
            lines.append("More than half the premium was surrendered before "
                         "exiting — the invalidation came far earlier.")
            mistakes.append("held_loser")
        if verdict == "won":
            exit_conf = exits.get("confidence", 0)
            exit_agrees = exits.get("direction") == trade.direction.value
            if exit_agrees and exit_conf >= 40:
                lines.append("The setup was still working at your exit — "
                             "consider scaling out instead of closing fully.")
                mistakes.append("cut_winner_early")
        return lines, verdict

    # ── scoring & output ─────────────────────────────────────────────────────

    def _score(self, trade, entry, mistakes, strengths) -> int:
        score = 50.0
        gate = (entry or {}).get("gate") or {}
        quality = gate.get("setup_quality")
        agreed = (entry or {}).get("direction") == trade.direction.value
        conf = (entry or {}).get("confidence", 0.0)
        if quality:
            signed = conf if agreed else -conf
            score += max(-25.0, min(25.0, signed / 4))
            score += {"excellent": 10, "good": 6, "average": 0,
                      "poor": -10}.get(quality, 0)
        score -= 10 * min(len(set(mistakes)), 4)
        if "no_stop" not in mistakes:
            score += 8
        score += min(len(strengths), 3) * 3
        return int(max(5, min(95, round(score))))

    def _summary(self, trade, verdict, quality, mistakes, score) -> str:
        tags = ", ".join(MISTAKES[m][0].lower() for m in mistakes[:3]
                         if m in MISTAKES) or "clean execution"
        return (f"{trade.symbol} {trade.direction.value} {verdict} "
                f"{trade.pnl:+.2f} on a {quality} setup — {tags}. "
                f"Process score {score}/100.")

    def _ev_note(self, entry) -> str:
        gate = (entry or {}).get("gate") or {}
        quality = gate.get("setup_quality")
        if quality in ("excellent", "good"):
            return (f"Positive expected value is plausible: {quality} setups "
                    f"are the bucket the AI itself trades.")
        if quality == "average":
            return "Marginal expected value: average setups need strong risk/reward."
        if quality == "poor":
            return ("Likely negative expected value: the analysis found more "
                    "conflicts than confirmations at entry.")
        return "Expected value unknown — no entry context was captured."

    def _persist(self, review: CoachReview) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{review.trade_id}.json"
        path.write_text(json.dumps(review.to_dict(), indent=1), encoding="utf-8")
        log.info("coach review %s: score %d (%s)", review.trade_id,
                 review.score, ", ".join(review.mistakes) or "no mistakes")

    def load(self, trade_id: str) -> dict | None:
        path = self._dir / f"{trade_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def load_all(self) -> list[dict]:
        if not self._dir.exists():
            return []
        out = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return out
