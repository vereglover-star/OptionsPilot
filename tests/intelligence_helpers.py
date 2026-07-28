"""Shared builders for the Trading Intelligence Engine tests.

`fact()` produces a fully-populated `TradeFact` whose every field can be
overridden by keyword, so a test that cares about one axis states only that axis
and the rest stays realistic. That matters here more than usual: nearly every
engine's behaviour depends on whether a field is None, so a builder that left
things unset by default would make "the detector ignored it" and "the detector
had nothing to look at" indistinguishable in a failure.

`series()` builds a chronological run of trades with a controllable win pattern,
which is what the streak, drawdown, period and behaviour tests need.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from optionspilot.intelligence.facts import ET, WEEKDAY_NAMES, FactSet, TradeFact

# A Monday at 10:00 ET, deliberately AFTER the 2026 DST switch (8 March) so the
# UTC offset is a stable −4 for every test in the suite. Picking a date in early
# March would put BASE at 09:00 ET (pre-market) instead of 10:00, which silently
# changes what the session-window detectors see.
# 10:00 ET is outside the opening-chop window, so a test that isn't about the
# open never accidentally trips the open-chop detector.
BASE = datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc)


def fact(trade_id: str = "T1", *, pnl: float = 100.0,
         entry: datetime | None = None, hold_minutes: float = 60.0,
         **overrides) -> TradeFact:
    """One realistic, fully-populated fact. Calendar fields are derived from
    `entry` in exchange time unless explicitly overridden."""
    entry = entry or BASE
    et = entry.astimezone(ET)
    defaults = dict(
        trade_id=trade_id, symbol="SPY", direction="long",
        strategy="confluence_v1", managed_by="manual",
        entry_ts=entry, exit_ts=entry + timedelta(minutes=hold_minutes),
        quantity=1, entry_price=2.0, exit_price=3.0, pnl=pnl, is_win=pnl > 0,
        hold_minutes=hold_minutes, exit_reason="target: reached",
        return_pct=round(pnl / 200 * 100, 2),
        entry_date=et.date().isoformat(), weekday=WEEKDAY_NAMES[et.weekday()],
        hour_et=et.hour, minute_et=et.minute, session="regular",
        confidence=65.0, setup_quality="good",
        market_regime="trending-up/low-vol", htf_trend="up", timeframe="15m",
        risk_reward=2.0, rsi=55.0, adx=25.0, rvol=1.2, iv=0.35, delta=0.45,
        dte=21, spread_pct=0.04, outlay=200.0, r_multiple=1.0,
        process_score=70, verdict="won" if pnl > 0 else "lost",
        mistakes=(), lessons=(), evidence_names=("htf_trend_alignment",),
        category_scores={"Entry Quality": 75.0, "Exit Quality": 70.0,
                         "Risk Management": 80.0, "Rule Following": 85.0,
                         "Emotional Discipline": 80.0, "Patience": 70.0,
                         "Position Size": 75.0, "Trend Alignment": 80.0,
                         "Reward/Risk Ratio": 65.0, "Timing": 70.0},
        had_stop=True, widened_stop=False, had_target=True, reviewed=True,
    )
    defaults.update(overrides)
    # Keep the calendar fields honest when a caller overrides `entry_ts`
    # directly rather than passing `entry`.
    if "entry_ts" in overrides and "entry_date" not in overrides:
        et = defaults["entry_ts"].astimezone(ET)
        defaults["entry_date"] = et.date().isoformat()
        defaults["weekday"] = WEEKDAY_NAMES[et.weekday()]
        defaults.setdefault("hour_et", et.hour)
    return TradeFact(**defaults)


def series(n: int, *, wins: str | None = None, pnl_win: float = 100.0,
           pnl_loss: float = -80.0, start: datetime | None = None,
           spacing_days: float = 1.0, prefix: str = "T",
           **overrides) -> list[TradeFact]:
    """`n` chronological facts.

    `wins` is an optional pattern string of 'W'/'L' that repeats — `series(6,
    wins="WWL")` gives win, win, loss, win, win, loss. Without it every trade
    wins, which keeps a test that only cares about timing from accidentally
    depending on an outcome mix.
    """
    start = start or BASE
    out: list[TradeFact] = []
    for i in range(n):
        entry = start + timedelta(days=spacing_days * i)
        if wins:
            won = wins[i % len(wins)].upper() == "W"
        else:
            won = True
        out.append(fact(f"{prefix}{i:03d}", pnl=pnl_win if won else pnl_loss,
                        entry=entry, **overrides))
    return out


def factset(facts: list[TradeFact], **kwargs) -> FactSet:
    return FactSet(facts=tuple(facts), **kwargs)


def review(trade_id: str = "T1", *, score: int = 70, verdict: str = "won",
           mistakes: list[str] | None = None, had_stop: bool = True,
           widened: bool = False, had_target: bool = True,
           r_multiple: float | None = 1.5, categories: dict | None = None
           ) -> dict:
    """A coach review dict in the shape `TradeCoach.load_all()` returns."""
    cats = categories or {"Entry Quality": 75, "Risk Management": 80}
    return {
        "trade_id": trade_id, "score": score, "verdict": verdict,
        "setup_quality": "good", "summary": "…",
        "before": [{"check": "setup quality", "passed": True, "detail": "good"}],
        "during": [
            {"check": "stop in place", "passed": had_stop,
             "detail": "1 protective order"},
            {"check": "stop discipline", "passed": not widened,
             "detail": "levels [1.0]"},
            {"check": "profit target defined", "passed": had_target,
             "detail": "1 target order"},
        ],
        "after": ["…"], "mistakes": mistakes or [],
        "strengths": [], "improvements": [], "pro_notes": [], "ev_note": "",
        "categories": [{"name": k, "score": v, "grade": "B",
                        "explanation": "", "suggestion": ""}
                       for k, v in cats.items()],
        "pnl": 100.0, "return_pct": 50.0, "hold_minutes": 60.0,
        "r_multiple": r_multiple, "entry_ts": "2026-03-02T14:00:00+00:00",
        "symbol": "SPY", "direction": "long",
    }
