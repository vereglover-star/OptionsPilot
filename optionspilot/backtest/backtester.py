"""Event-driven backtester.

Replays historical candles bar-by-bar through the *exact same* components the
live system uses — DecisionEngine, RiskManager, PaperBroker, PositionManager —
so a strategy cannot behave differently in a backtest than it would live.

No-lookahead discipline:
  - The engine only ever sees candles that were CLOSED by the end of the
    current entry bar (`_slice_closed`): a 1h bar that is still forming when a
    5m bar closes is excluded.
  - Swing/BOS logic already carries its own confirmation lag (Phase 2).

Option pricing (documented limitation): free historical option chains don't
exist, so contracts are synthesized and priced with Black-Scholes using
realized volatility estimated from history *up to the current bar*. Fills at
stop/target levels assume no gap through the level. Reports carry these notes.

Intrabar sequencing is conservative: the adverse extreme of each bar is
tested before the favorable one, so a bar that touches both stop and target
counts as a stop-out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from optionspilot.analysis.options_metrics import bs_greeks
from optionspilot.analysis.structure import detect_events, find_swings
from optionspilot.backtest.report import BacktestReport
from optionspilot.broker import PaperBroker, PositionManager
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import (
    Direction, Fill, OptionContract, OptionRight, Timeframe, TradePlan,
)
from optionspilot.engine import DecisionEngine
from optionspilot.journal import TradeJournal, build_trade_record
from optionspilot.risk import RiskManager

log = get_logger("backtest")

WARMUP_BARS = 60
DEFAULT_VOL = 0.25
SYNTH_SPREAD = 0.02          # 2% of mid, split around the model price
CHOCH_LOOKBACK_BARS = 120    # window for invalidation detection while in a trade


@dataclass(slots=True)
class _OpenTrade:
    plan: TradePlan
    quantity: int
    entry_fill: Fill
    exits: list[tuple[Fill, str]] = field(default_factory=list)
    conditions: dict[str, str] = field(default_factory=dict)


class Backtester:
    def __init__(self, config: AppConfig,
                 learned_weights: dict[str, float] | None = None):
        self._cfg = config
        self._learned = learned_weights

    def run(
        self,
        symbol: str,
        candles_by_tf: dict[Timeframe, pd.DataFrame],
        journal: TradeJournal | None = None,
    ) -> BacktestReport:
        cfg = self._cfg
        entry_tf = next(
            (Timeframe.from_string(s) for s in cfg.engine.entry_timeframes
             if Timeframe.from_string(s) in candles_by_tf),
            None,
        )
        if entry_tf is None:
            raise ValueError(
                f"none of the configured entry timeframes "
                f"{cfg.engine.entry_timeframes} present in supplied data"
            )
        entry_df = candles_by_tf[entry_tf]
        if len(entry_df) <= WARMUP_BARS:
            raise ValueError(
                f"need more than {WARMUP_BARS} {entry_tf} bars, got {len(entry_df)}"
            )

        engine = DecisionEngine(cfg, self._learned)
        risk = RiskManager(cfg.risk)
        broker = PaperBroker(cfg.broker, ":memory:", cfg.risk.starting_balance)
        pm = PositionManager()
        journal = journal or TradeJournal(":memory:")

        open_trade: _OpenTrade | None = None
        equity_curve: list = []
        trades = []
        entry_duration = pd.Timedelta(minutes=entry_tf.minutes)

        for i in range(WARMUP_BARS, len(entry_df)):
            ts = entry_df.index[i]
            bar = entry_df.iloc[i]
            spot = float(bar["close"])
            bar_close_time = (ts + entry_duration).to_pydatetime()
            vol = self._realized_vol(candles_by_tf, ts)

            # 1. manage the open position (intrabar, conservative order)
            if open_trade is not None and broker.get_positions():
                open_trade, closed = self._manage(
                    broker, pm, risk, journal, open_trade,
                    bar, ts.to_pydatetime(), vol,
                    entry_df.iloc[max(0, i - CHOCH_LOOKBACK_BARS): i + 1],
                )
                if closed is not None:
                    trades.append(closed)

            # 2. mark to model and track equity
            positions = broker.get_positions()
            if positions:
                p = positions[0]
                broker.mark_positions({
                    p.contract.symbol:
                        self._price(p.contract, spot, ts.date(), vol)
                })
            equity = broker.get_account().equity
            risk.update_equity(equity, ts.to_pydatetime())
            equity_curve.append((ts.to_pydatetime(), equity))

            # 3. look for a new entry when flat
            if not broker.get_positions():
                open_trade = self._try_enter(
                    engine, risk, broker, symbol, candles_by_tf,
                    entry_tf, ts, bar_close_time, spot, vol,
                )

        # Force-close anything still open at the end so metrics are complete.
        if open_trade is not None and broker.get_positions():
            p = broker.get_positions()[0]
            last_ts = entry_df.index[-1].to_pydatetime()
            price = self._price(p.contract, float(entry_df["close"].iloc[-1]),
                                entry_df.index[-1].date(), vol)
            fill, _ = broker.close_position(
                p.contract.symbol, p.quantity,
                bid=price * (1 - SYNTH_SPREAD / 2), ts=last_ts,
                reason="backtest end",
            )
            open_trade.exits.append((fill, "backtest end"))
            trades.append(self._journal_trade(journal, risk, open_trade))
            equity_curve[-1] = (last_ts, broker.get_account().equity)

        report = BacktestReport(
            symbol=symbol,
            strategy=f"confluence_v1 (min_conf {cfg.engine.min_confidence:.0f}%)",
            start=entry_df.index[WARMUP_BARS].to_pydatetime(),
            end=entry_df.index[-1].to_pydatetime(),
            initial_balance=cfg.risk.starting_balance,
            final_equity=broker.get_account().equity,
            trades=trades,
            equity_curve=equity_curve,
            notes=[
                "Options synthesized and priced via Black-Scholes on realized "
                "volatility (no free historical chains exist).",
                "Stop/target fills assume no gap through the level.",
                "Adverse bar extreme tested before favorable (conservative).",
                f"Risk status at end: {risk.status()}",
            ],
        )
        log.info(
            "backtest %s: %d trades, net %+.2f (%.2f%%), win rate %.1f%%, PF %s, "
            "max DD %.2f%%",
            symbol, report.n_trades, report.net_profit, report.net_profit_pct,
            report.win_rate * 100, report.profit_factor, report.max_drawdown_pct,
        )
        return report

    # ── entry ────────────────────────────────────────────────────────────────

    def _try_enter(self, engine, risk, broker, symbol, candles_by_tf,
                   entry_tf, ts, bar_close_time, spot, vol) -> _OpenTrade | None:
        sliced = {
            tf: _slice_closed(df, bar_close_time, tf)
            for tf, df in candles_by_tf.items()
        }
        decision = engine.evaluate(symbol, sliced)
        if not decision.tradeable:
            return None
        chain = self._synthetic_chain(spot, ts.date(), vol)
        plan = engine.build_plan(decision, chain, spot=spot, today=ts.date())
        if plan is None:
            return None
        approval = risk.approve(plan, open_positions=0, now=ts.to_pydatetime())
        if not approval.approved:
            return None
        fill = broker.open_position(plan, approval.quantity, ts=ts.to_pydatetime())
        risk.record_entry(ts.to_pydatetime())
        views = decision.views
        htf = next((views[tf] for tf in views if tf.minutes > entry_tf.minutes), None)
        return _OpenTrade(
            plan=plan, quantity=approval.quantity, entry_fill=fill,
            conditions={
                "htf_trend": htf.trend.value if htf else "unknown",
                "entry_timeframe": str(entry_tf),
                "sizing": approval.notes[0] if approval.notes else "",
            },
        )

    # ── position management ──────────────────────────────────────────────────

    def _manage(self, broker, pm, risk, journal, open_trade,
                bar, ts, vol, recent_entry_df):
        position = broker.get_positions()[0]
        is_long = position.direction is Direction.LONG
        adverse = float(bar["low"] if is_long else bar["high"])
        favorable = float(bar["high"] if is_long else bar["low"])
        first_partial = (position.partials_remaining[0]
                         if position.partials_remaining else None)

        intents = pm.review(position, adverse, ts)
        if not intents:
            intents = pm.review(position, favorable, ts)
        if not intents:
            opposing = _fresh_opposing_choch(recent_entry_df, position.direction)
            intents = pm.review(position, float(bar["close"]), ts,
                                opposing_choch=opposing)

        if not intents:
            return open_trade, None

        intent = intents[0]
        exit_underlying = {
            "stop": position.stop_current,
            "target": position.target,
            "partial": first_partial,
            "invalidation": float(bar["close"]),
        }[intent.kind]
        price = self._price(position.contract, exit_underlying,
                            ts.date(), vol)
        fill, realized = broker.close_position(
            position.contract.symbol, intent.quantity,
            bid=max(price * (1 - SYNTH_SPREAD / 2), 0.01), ts=ts,
            reason=intent.reason,
        )
        open_trade.exits.append((fill, intent.reason))

        if intent.kind == "partial" and broker.get_positions():
            broker.update_position_management(position)
            return open_trade, None

        # fully closed
        closed = self._journal_trade(journal, risk, open_trade)
        return None, closed

    def _journal_trade(self, journal, risk, open_trade: _OpenTrade):
        trade_id = (f"{open_trade.plan.signal.symbol}-"
                    f"{open_trade.entry_fill.ts:%Y%m%d-%H%M%S}")
        record = build_trade_record(
            trade_id, open_trade.plan, open_trade.quantity,
            open_trade.entry_fill, open_trade.exits, open_trade.conditions,
        )
        journal.record(record)
        risk.record_closed_trade(record.exit_ts, record.pnl)
        return record

    # ── option synthesis ─────────────────────────────────────────────────────

    def _synthetic_chain(self, spot: float, today: date, vol: float
                         ) -> list[OptionContract]:
        cfg = self._cfg.engine
        expiration = today + timedelta(days=(cfg.min_dte + cfg.max_dte) // 2)
        t_years = max((expiration - today).days, 1) / 365
        step = max(round(spot * 0.01, 2), 0.5)
        chain = []
        for k in range(-10, 11):
            strike = round(spot + k * step, 2)
            if strike <= 0:
                continue
            for right in (OptionRight.CALL, OptionRight.PUT):
                g = bs_greeks(spot, strike, t_years, vol, right)
                if g.price < 0.02:
                    continue
                mid = g.price
                chain.append(OptionContract(
                    underlying="BACKTEST", expiration=expiration, strike=strike,
                    right=right,
                    bid=round(mid * (1 - SYNTH_SPREAD / 2), 4),
                    ask=round(mid * (1 + SYNTH_SPREAD / 2), 4),
                    last=round(mid, 4),
                    volume=1000, open_interest=5000,
                    implied_volatility=vol,
                    delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega,
                ))
        return chain

    def _price(self, contract: OptionContract, spot: float, on: date,
               vol: float) -> float:
        t_years = max((contract.expiration - on).days, 0) / 365
        sigma = contract.implied_volatility or vol
        return bs_greeks(spot, contract.strike, t_years, sigma, contract.right).price

    def _realized_vol(self, candles_by_tf, ts) -> float:
        """Annualized realized vol from the highest available timeframe,
        using only bars closed before `ts` (no lookahead)."""
        tf = max(candles_by_tf, key=lambda t: t.minutes)
        df = candles_by_tf[tf]
        closes = df["close"][df.index < ts].tail(30)
        if len(closes) < 10:
            return DEFAULT_VOL
        rets = np.log(closes / closes.shift()).dropna()
        if rets.std() == 0 or math.isnan(rets.std()):
            return DEFAULT_VOL
        bars_per_year = 252 * (1440 / tf.minutes) if tf.minutes < 1440 else 252
        vol = float(rets.std() * math.sqrt(bars_per_year))
        return min(max(vol, 0.05), 2.0)


def _slice_closed(df: pd.DataFrame, now, tf: Timeframe) -> pd.DataFrame:
    """Bars fully closed by `now`: bar open time + bar duration <= now."""
    cutoff = pd.Timestamp(now)
    return df[df.index + pd.Timedelta(minutes=tf.minutes) <= cutoff]


def _fresh_opposing_choch(recent_df: pd.DataFrame, direction: Direction) -> bool:
    """True when the latest structure event in the recent window is a CHoCH
    against the position and it fired on the most recent bar."""
    if len(recent_df) < 10:
        return False
    swings = find_swings(recent_df, strength=2)
    events = detect_events(recent_df, swings)
    if not events:
        return False
    last = events[-1]
    return (last.kind == "CHOCH"
            and last.direction is not direction
            and last.ts == recent_df.index[-1])
