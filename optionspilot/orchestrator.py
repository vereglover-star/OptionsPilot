"""Orchestrator — the live event loop that composes every subsystem.

One cycle (`run_cycle`):
  1. fetch fresh candles for the watchlist (all configured timeframes)
  2. manage open positions: stop / target / CHoCH invalidation / partials,
     filling against real option quotes (model-price fallback)
  3. mark positions, feed equity to the risk manager, surface halt events
  4. scan flat symbols: engine.evaluate -> risk.approve -> broker.open
  5. large-move detection on the entry timeframe
  6. journal completed round trips; notify on everything notable

Restart safety: the broker persists the account and positions; the
orchestrator persists per-trade journal context (entry fill, reasons,
partial exits so far) in data/state/open_trades.json, and rebuilds the risk
manager's weekly P&L state from the journal at startup. Kill the process
mid-trade and it resumes exactly where it was.

Exits are never risk-gated (a stop must always be honored); only entries are.
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from optionspilot.analysis.options_metrics import bs_greeks
from optionspilot.analysis.structure import detect_events, find_swings
from optionspilot.broker import PositionManager, create_broker
from optionspilot.broker.base import Broker
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import (
    Direction, Fill, Position, Timeframe, TradePlan, TradeRecord, utcnow,
)
from optionspilot.data import MarketDataProvider, YFinanceProvider
from optionspilot.engine import DecisionEngine
from optionspilot.journal import TradeJournal
from optionspilot.learning import WeightStore
from optionspilot.notify import NotificationCenter, build_notification_center
from optionspilot.risk import RiskManager

log = get_logger("engine")

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

_WINDOW_DAYS = {
    Timeframe.M1: 5, Timeframe.M5: 10, Timeframe.M15: 25,
    Timeframe.H1: 60, Timeframe.H4: 100, Timeframe.D1: 300,
}
LARGE_MOVE_RVOL = 3.0
LARGE_MOVE_ATR_MULT = 2.0
CHOCH_LOOKBACK_BARS = 120
FALLBACK_VOL = 0.25


@dataclass(slots=True)
class _TradeMeta:
    """Serializable journal context for one open trade — everything a
    TradeRecord needs that the broker doesn't persist."""

    trade_id: str
    symbol: str
    contract_symbol: str
    direction: str
    strategy: str
    confidence: float
    entry_reasons: list[str]
    evidence_names: list[str]
    quantity: int
    entry_ts: str
    entry_price: float
    entry_commission: float
    conditions: dict[str, str] = field(default_factory=dict)
    exits: list[dict] = field(default_factory=list)

    def add_exit(self, fill: Fill, reason: str) -> None:
        self.exits.append({
            "ts": fill.ts.isoformat(), "quantity": fill.quantity,
            "price": fill.price, "commission": fill.commission, "reason": reason,
        })

    def to_record(self) -> TradeRecord:
        total = sum(e["quantity"] for e in self.exits)
        exit_price = sum(e["price"] * e["quantity"] for e in self.exits) / total
        return TradeRecord(
            id=self.trade_id, symbol=self.symbol,
            contract_symbol=self.contract_symbol,
            direction=Direction(self.direction), strategy=self.strategy,
            quantity=self.quantity,
            entry_ts=datetime.fromisoformat(self.entry_ts),
            entry_price=self.entry_price,
            exit_ts=datetime.fromisoformat(self.exits[-1]["ts"]),
            exit_price=exit_price,
            commissions=self.entry_commission + sum(e["commission"] for e in self.exits),
            confidence=self.confidence,
            entry_reasons=self.entry_reasons,
            exit_reason=self.exits[-1]["reason"],
            market_conditions=self.conditions,
            indicators_used=self.evidence_names,
        )


class _MetaStore:
    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, _TradeMeta]:
        if not self._path.exists():
            return {}
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        return {k: _TradeMeta(**v) for k, v in doc.items()}

    def save(self, metas: dict[str, _TradeMeta]) -> None:
        self._path.write_text(
            json.dumps({k: asdict(m) for k, m in metas.items()}, indent=2),
            encoding="utf-8",
        )


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        provider: MarketDataProvider | None = None,
        broker: Broker | None = None,
        journal: TradeJournal | None = None,
        notifier: NotificationCenter | None = None,
        data_dir: str | Path = "data",
        learned_weights: dict[str, float] | None = None,
    ):
        self.cfg = config
        data_dir = Path(data_dir)
        self.provider = provider or YFinanceProvider()
        self.broker = broker or create_broker(
            config, data_dir / "paper.db", config.risk.starting_balance
        )
        self.journal = journal or TradeJournal(data_dir / "journal.db")
        self.notifier = notifier or build_notification_center(config.notify)
        if learned_weights is None:
            learned_weights = WeightStore(data_dir / "learning" / "weights.json").current()
        self.engine = DecisionEngine(config, learned_weights)
        self.risk = RiskManager(config.risk)
        self.pm = PositionManager()
        self._meta_store = _MetaStore(data_dir / "state" / "open_trades.json")
        self._metas = self._meta_store.load()
        self._timeframes = [Timeframe.from_string(s) for s in config.data.timeframes]
        self._entry_tf = Timeframe.from_string(config.engine.entry_timeframes[-1])
        self._last_halt_notified = ""
        self._last_large_move: dict[str, str] = {}
        self._last_daily_summary: date | None = None
        self._last_weekly_summary: tuple[int, int] | None = None
        self._rebuild_risk_state()

    # ── startup ──────────────────────────────────────────────────────────────

    def _rebuild_risk_state(self) -> None:
        """Rebuild streaks/limits from the journal so a restart can't be used
        (accidentally or otherwise) to dodge the circuit breaker."""
        now_et = utcnow().astimezone(ET)
        week = now_et.isocalendar()[:2]
        replayed = 0
        for t in self.journal.all():
            if t.exit_ts.astimezone(ET).isocalendar()[:2] == week:
                self.risk.record_closed_trade(t.exit_ts, t.pnl)
                replayed += 1
            if t.entry_ts.astimezone(ET).date() == now_et.date():
                self.risk.record_entry(t.entry_ts)
        for meta in self._metas.values():
            entry_ts = datetime.fromisoformat(meta.entry_ts)
            if entry_ts.astimezone(ET).date() == now_et.date():
                self.risk.record_entry(entry_ts)
        self.risk.update_equity(self.broker.get_account().equity, utcnow())
        if replayed:
            log.info("risk state rebuilt from journal: %d trade(s) this week", replayed)

    # ── the loop ─────────────────────────────────────────────────────────────

    def run_forever(self) -> None:  # pragma: no cover - infinite loop
        log.info("orchestrator starting: watchlist %s, scan every %ds",
                 self.cfg.data.watchlist, self.cfg.engine.scan_interval_seconds)
        while True:
            now = utcnow()
            try:
                if self.market_open(now):
                    self.run_cycle(now)
                self._maybe_send_summaries(now)
            except Exception as exc:  # noqa: BLE001 — the loop must survive
                log.exception("cycle failed: %s", exc)
            _time.sleep(self.cfg.engine.scan_interval_seconds
                        if self.market_open(utcnow()) else 60)

    @staticmethod
    def market_open(now: datetime) -> bool:
        et = now.astimezone(ET)
        return et.weekday() < 5 and MARKET_OPEN <= et.time() < MARKET_CLOSE

    def run_cycle(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        summary: dict = {"ts": now.isoformat(), "opened": [], "closed": [],
                         "signals": {}, "skipped": {}}
        candles = {
            sym: self._fetch_candles(sym) for sym in self.cfg.data.watchlist
        }

        self._manage_positions(now, candles, summary)
        self._mark_and_update_risk(now)
        self._surface_halt()
        self._scan_for_entries(now, candles, summary)
        self._check_large_moves(candles)
        return summary

    def scan_single(self, symbol: str, now: datetime | None = None) -> dict:
        """On-demand scan of one symbol through the full pipeline — used by the
        TradingView webhook. An external alert changes *when* the system looks,
        never *whether* it trades: confidence threshold, contract filters, and
        every risk gate apply exactly as in a scheduled scan."""
        now = now or utcnow()
        symbol = symbol.upper()
        summary: dict = {"ts": now.isoformat(), "opened": [], "closed": [],
                         "signals": {}, "skipped": {}}
        held = {p.contract.underlying for p in self.broker.get_positions()}
        if symbol in held:
            summary["skipped"][symbol] = "position already open"
            return summary
        try:
            self._scan_symbol(symbol, now, self._fetch_candles(symbol), summary)
        except Exception as exc:  # noqa: BLE001 — webhook must not crash the app
            log.exception("single-symbol scan failed for %s: %s", symbol, exc)
            summary["skipped"][symbol] = f"scan error: {exc}"
        return summary

    # ── data ─────────────────────────────────────────────────────────────────

    def _fetch_candles(self, symbol: str) -> dict[Timeframe, pd.DataFrame]:
        out = {}
        end = utcnow()
        for tf in self._timeframes:
            try:
                out[tf] = self.provider.get_candles(
                    symbol, tf, end - timedelta(days=_WINDOW_DAYS[tf]), end
                )
            except Exception as exc:  # noqa: BLE001
                log.error("candle fetch failed %s %s: %s", symbol, tf, exc)
                out[tf] = pd.DataFrame()
        return out

    # ── position management ──────────────────────────────────────────────────

    def _manage_positions(self, now, candles, summary) -> None:
        for position in list(self.broker.get_positions()):
            symbol = position.contract.underlying
            try:
                spot = self.provider.get_quote(symbol).last
            except Exception as exc:  # noqa: BLE001
                log.error("quote failed for %s — skipping management: %s", symbol, exc)
                continue
            entry_df = candles.get(symbol, {}).get(self._entry_tf, pd.DataFrame())
            opposing = self._fresh_opposing_choch(entry_df, position.direction)

            intents = self.pm.review(position, spot, now, opposing_choch=opposing)
            for intent in intents:
                bid = self._option_bid(position, spot)
                if bid <= 0:
                    log.error("no sellable bid for %s — will retry next cycle",
                              position.contract.symbol)
                    continue
                fill, realized = self.broker.close_position(
                    position.contract.symbol, intent.quantity, bid, now,
                    reason=intent.reason,
                )
                meta = self._metas.get(position.contract.symbol)
                if meta is not None:
                    meta.add_exit(fill, intent.reason)
                still_open = any(
                    p.contract.symbol == position.contract.symbol
                    for p in self.broker.get_positions()
                )
                if still_open:
                    self.broker.update_position_management(position)
                    self._meta_store.save(self._metas)
                    self.notifier.notify(
                        "trade_closed",
                        f"Partial: {position.contract.symbol} x{intent.quantity} "
                        f"{realized:+.2f}",
                        intent.reason,
                    )
                else:
                    self._finalize_trade(position, meta, realized, summary)

    def _finalize_trade(self, position: Position, meta: _TradeMeta | None,
                        realized: float, summary: dict) -> None:
        symbol = position.contract.symbol
        if meta is not None:
            record = meta.to_record()
            self.journal.record(record)
            self.risk.record_closed_trade(record.exit_ts, record.pnl)
            summary["closed"].append({"symbol": symbol, "pnl": record.pnl})
            self.notifier.notify(
                "trade_closed",
                f"Closed {record.symbol} {record.direction.value} "
                f"x{record.quantity}: {record.pnl:+.2f}",
                f"{record.exit_reason}\nconfidence at entry: {record.confidence:.0f}%",
            )
            del self._metas[symbol]
            self._meta_store.save(self._metas)
        else:  # position predates meta tracking — still record risk impact
            log.warning("closed %s without journal meta (realized %+.2f)",
                        symbol, realized)
            self.risk.record_closed_trade(utcnow(), realized)

    # ── marking / risk ───────────────────────────────────────────────────────

    def _mark_and_update_risk(self, now: datetime) -> None:
        marks = {}
        for p in self.broker.get_positions():
            try:
                spot = self.provider.get_quote(p.contract.underlying).last
                marks[p.contract.symbol] = self._option_mid(p, spot)
            except Exception as exc:  # noqa: BLE001
                log.error("mark failed for %s: %s", p.contract.symbol, exc)
        if marks:
            self.broker.mark_positions(marks)
        self.risk.update_equity(self.broker.get_account().equity, now)

    def _surface_halt(self) -> None:
        status = self.risk.status()
        reason = status["halt_reason"]
        if status["halted"] and reason != self._last_halt_notified:
            self._last_halt_notified = reason
            self.notifier.notify(
                "risk_limit", "TRADING HALTED",
                f"{reason}\nresumes: {status['halt_until'] or 'manual reset required'}",
            )
        elif not status["halted"]:
            self._last_halt_notified = ""

    # ── entries ──────────────────────────────────────────────────────────────

    def _scan_for_entries(self, now, candles, summary) -> None:
        held = {p.contract.underlying for p in self.broker.get_positions()}
        for symbol in self.cfg.data.watchlist:
            if symbol in held:
                continue
            try:
                self._scan_symbol(symbol, now, candles[symbol], summary)
            except Exception as exc:  # noqa: BLE001
                log.exception("scan failed for %s: %s", symbol, exc)

    def _scan_symbol(self, symbol, now, symbol_candles, summary) -> None:
        decision = self.engine.evaluate(symbol, symbol_candles)
        if decision.signal is None:
            summary["skipped"][symbol] = "insufficient data"
            return
        summary["signals"][symbol] = {
            "direction": decision.signal.direction.value,
            "confidence": decision.signal.confidence,
        }
        if not decision.tradeable:
            return
        spot = self.provider.get_quote(symbol).last
        chain = self._chain_in_dte_window(symbol, now.date())
        plan = self.engine.build_plan(decision, chain, spot=spot, today=now.date())
        if plan is None:
            summary["skipped"][symbol] = "no viable contract/plan"
            return
        approval = self.risk.approve(
            plan, open_positions=len(self.broker.get_positions()), now=now
        )
        if not approval.approved:
            summary["skipped"][symbol] = f"risk veto: {approval.veto}"
            return
        fill = self.broker.open_position(plan, approval.quantity, now)
        self.risk.record_entry(now)
        self._register_meta(plan, approval.quantity, fill)
        summary["opened"].append({
            "symbol": symbol, "contract": plan.contract.symbol,
            "quantity": approval.quantity, "confidence": decision.signal.confidence,
        })
        self.notifier.notify(
            "trade_opened",
            f"Opened {symbol} {plan.signal.direction.value} "
            f"x{approval.quantity} {plan.contract.symbol} @ ~{fill.price:.2f}",
            f"confidence {plan.signal.confidence:.0f}%\n"
            f"stop {plan.stop_underlying} | target {plan.target_underlying} "
            f"| RR {plan.risk_reward}\n" + "\n".join(plan.signal.reasons[:6]),
        )

    def _register_meta(self, plan: TradePlan, quantity: int, fill: Fill) -> None:
        signal = plan.signal
        meta = _TradeMeta(
            trade_id=f"{signal.symbol}-{fill.ts:%Y%m%d-%H%M%S}",
            symbol=signal.symbol,
            contract_symbol=plan.contract.symbol,
            direction=signal.direction.value,
            strategy=signal.strategy,
            confidence=signal.confidence,
            entry_reasons=signal.reasons,
            evidence_names=[e.name for e in signal.evidence if e.score > 0],
            quantity=quantity,
            entry_ts=fill.ts.isoformat(),
            entry_price=fill.price,
            entry_commission=fill.commission,
            conditions={
                "hour_et": str(fill.ts.astimezone(ET).hour),
                "dte": str(plan.contract.dte(fill.ts.date())),
                "risk_reward": f"{plan.risk_reward:.2f}",
            },
        )
        self._metas[plan.contract.symbol] = meta
        self._meta_store.save(self._metas)

    def _chain_in_dte_window(self, symbol: str, today: date) -> list:
        cfg = self.cfg.engine
        expirations = self.provider.get_expirations(symbol)
        target = next(
            (e for e in expirations if cfg.min_dte <= (e - today).days <= cfg.max_dte),
            None,
        )
        if target is None:
            return []
        return self.provider.get_option_chain(symbol, target)

    # ── option quotes with model fallback ────────────────────────────────────

    def _lookup_contract(self, position: Position):
        try:
            chain = self.provider.get_option_chain(
                position.contract.underlying, position.contract.expiration
            )
            return next(
                (c for c in chain if c.symbol == position.contract.symbol), None
            )
        except Exception as exc:  # noqa: BLE001
            log.error("chain lookup failed for %s: %s",
                      position.contract.symbol, exc)
            return None

    def _option_bid(self, position: Position, spot: float) -> float:
        live = self._lookup_contract(position)
        if live is not None and live.bid > 0:
            return live.bid
        return round(self._model_price(position, spot) * 0.99, 4)

    def _option_mid(self, position: Position, spot: float) -> float:
        live = self._lookup_contract(position)
        if live is not None and live.mid > 0:
            return live.mid
        return self._model_price(position, spot)

    def _model_price(self, position: Position, spot: float) -> float:
        c = position.contract
        sigma = c.implied_volatility or FALLBACK_VOL
        t_years = max(c.dte(utcnow().date()), 0) / 365
        return bs_greeks(spot, c.strike, t_years, sigma, c.right).price

    # ── extras ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fresh_opposing_choch(entry_df: pd.DataFrame, direction: Direction) -> bool:
        if len(entry_df) < 20:
            return False
        recent = entry_df.iloc[-CHOCH_LOOKBACK_BARS:]
        events = detect_events(recent, find_swings(recent, strength=2))
        if not events:
            return False
        last = events[-1]
        return (last.kind == "CHOCH" and last.direction is not direction
                and last.ts == recent.index[-1])

    def _check_large_moves(self, candles) -> None:
        from optionspilot.analysis.indicators import atr, relative_volume

        for symbol, tfs in candles.items():
            df = tfs.get(self._entry_tf, pd.DataFrame())
            if len(df) < 30:
                continue
            bar = df.iloc[-1]
            bar_id = str(df.index[-1])
            if self._last_large_move.get(symbol) == bar_id:
                continue
            rvol = relative_volume(df).iloc[-1]
            a = atr(df).iloc[-1]
            bar_range = bar["high"] - bar["low"]
            if rvol >= LARGE_MOVE_RVOL and bar_range >= LARGE_MOVE_ATR_MULT * a:
                self._last_large_move[symbol] = bar_id
                direction = "up" if bar["close"] >= bar["open"] else "down"
                self.notifier.notify(
                    "large_move",
                    f"{symbol}: large move {direction}",
                    f"{self._entry_tf} bar range {bar_range:.2f} "
                    f"({bar_range / a:.1f}x ATR) on {rvol:.1f}x volume",
                )

    def _maybe_send_summaries(self, now: datetime) -> None:
        et = now.astimezone(ET)
        if et.weekday() >= 5 or et.time() < time(16, 5):
            return
        if self._last_daily_summary != et.date():
            self._last_daily_summary = et.date()
            self.notifier.notify("daily_summary", *self._summary_text(et, "day"))
        week = et.isocalendar()[:2]
        if et.weekday() == 4 and self._last_weekly_summary != week:
            self._last_weekly_summary = week
            self.notifier.notify("weekly_summary", *self._summary_text(et, "week"))

    def _summary_text(self, et: datetime, period: str) -> tuple[str, str]:
        if period == "day":
            start = datetime.combine(et.date(), time(0), tzinfo=ET)
        else:
            start = datetime.combine(et.date() - timedelta(days=et.weekday()),
                                     time(0), tzinfo=ET)
        trades = self.journal.query(start=start)
        pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.is_win)
        acct = self.broker.get_account()
        title = f"{period.title()} summary: {len(trades)} trades, {pnl:+.2f}"
        body = (f"wins {wins}/{len(trades)}\n"
                f"equity {acct.equity:,.2f} (realized total {acct.realized_pnl:+,.2f})\n"
                f"open positions: {len(self.broker.get_positions())}\n"
                f"risk: {self.risk.status()}")
        return title, body
