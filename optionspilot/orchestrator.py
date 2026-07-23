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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from optionspilot.analysis.options_metrics import bs_greeks
from optionspilot.analysis.structure import detect_events, find_swings
from optionspilot.broker import OrderManager, PositionManager, create_broker
from optionspilot.broker.base import Broker
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import (
    Direction, Fill, Position, Timeframe, TradePlan, TradeRecord, utcnow,
)
from optionspilot.data import CachedProvider, MarketDataProvider, YFinanceProvider
from optionspilot.engine import DecisionEngine
from optionspilot.experience import ExperienceEngine, build_snapshot
from optionspilot.journal import TradeJournal
from optionspilot.learning import WeightStore
from optionspilot.notify import NotificationCenter, build_notification_center
from optionspilot.risk import RiskManager

log = get_logger("engine")

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# History window per timeframe, bounded by what yfinance actually serves
# (1m ≤ ~7 days back; 2m/5m/15m/30m ≤ 60 days; 1h ≤ 730 days; 1d+ unlimited).
# 3m resamples from 1m and 10m from 5m, so they inherit those source limits.
# Public: also read by the /api/candles history window (ui/server.py) and the
# CLI backtest window (__main__.py). Kept here, its natural owner.
WINDOW_DAYS = {
    Timeframe.M1: 5, Timeframe.M2: 10, Timeframe.M3: 5,
    Timeframe.M5: 10, Timeframe.M10: 25, Timeframe.M15: 25,
    Timeframe.M30: 40, Timeframe.H1: 60, Timeframe.H2: 90,
    Timeframe.H4: 100, Timeframe.D1: 300, Timeframe.W1: 1800,
    Timeframe.MN1: 5400,
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
    # Full centralized decision snapshot at entry (experience/snapshot.py), fed
    # to the Experience Engine at close for feature-symmetric AI experiences.
    entry_context: dict = field(default_factory=dict)

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


class _JsonStore:
    """Tiny persisted dict for manual-trade context snapshots."""

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self, doc: dict) -> None:
        self._path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


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
        self.provider = provider or CachedProvider(
            YFinanceProvider(), data_dir / "cache.db"
        )
        self.broker = broker or create_broker(
            config, data_dir / "paper.db", config.risk.starting_balance
        )
        self.journal = journal or TradeJournal(data_dir / "journal.db")
        self.notifier = notifier or build_notification_center(config.notify)
        self.experience = ExperienceEngine(data_dir / "experience.db")
        if learned_weights is None:
            learned_weights = WeightStore(data_dir / "learning" / "weights.json").current()
        self.engine = DecisionEngine(config, learned_weights)
        self.risk = RiskManager(config.risk)
        self.pm = PositionManager()
        self.orders = OrderManager(self.broker, data_dir / "orders.db")
        self._meta_store = _MetaStore(data_dir / "state" / "open_trades.json")
        self._metas = self._meta_store.load()
        from optionspilot.coach import TradeCoach
        self.coach = TradeCoach(data_dir / "coach")
        self._manual_store = _JsonStore(data_dir / "state" / "manual_trades.json")
        self._manual: dict[str, dict] = self._manual_store.load()
        self._last_advice: dict[str, str] = {}
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

    def run_cycle(self, now: datetime | None = None,
                  candles: dict[str, dict[Timeframe, pd.DataFrame]] | None = None,
                  ) -> dict:
        now = now or utcnow()
        summary: dict = {"ts": now.isoformat(), "opened": [], "closed": [],
                         "signals": {}, "skipped": {}}
        if candles is None:
            candles = self.fetch_watchlist_candles(self.cfg.data.watchlist)
        else:  # prefetched frames may omit symbols added mid-flight
            candles = {sym: candles.get(sym) or self._fetch_candles(sym)
                       for sym in self.cfg.data.watchlist}
        summary["quotes"] = {
            sym: self._quote_snapshot(tfs) for sym, tfs in candles.items()
        }

        self._manage_positions(now, candles, summary)
        self._evaluate_orders(now, summary)
        self._reconcile_manual(now, candles, summary)
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

    @staticmethod
    def _quote_snapshot(tfs: dict[Timeframe, pd.DataFrame]) -> dict:
        """Price / daily change / volume for watchlist display and sorting,
        derived from candles already fetched this cycle — no extra requests."""
        daily = tfs.get(Timeframe.D1)
        if daily is None or len(daily) < 2:
            return {}
        last = float(daily["close"].iloc[-1])
        prev = float(daily["close"].iloc[-2])
        return {
            "price": round(last, 2),
            "change_pct": round((last / prev - 1) * 100, 2),
            "volume": int(daily["volume"].iloc[-1]),
        }

    # ── data ─────────────────────────────────────────────────────────────────

    def fetch_watchlist_candles(
        self,
        symbols: list[str],
        on_symbol=None,
        max_workers: int = 8,
    ) -> dict[str, dict[Timeframe, pd.DataFrame]]:
        """Fetch candles for every (symbol, timeframe) pair in parallel.

        Pure data acquisition — touches only the provider (which must be
        thread-safe), never broker/risk/journal state, so callers may run it
        WITHOUT holding the orchestrator lock and keep the UI responsive.
        `on_symbol(symbol, frames)` fires as each symbol completes, enabling
        progressive display while the rest are still downloading.
        """
        out: dict[str, dict[Timeframe, pd.DataFrame]] = {s: {} for s in symbols}
        jobs = [(sym, tf) for sym in symbols for tf in self._timeframes]
        if not jobs:
            return out
        remaining = {sym: len(self._timeframes) for sym in symbols}
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(jobs)), thread_name_prefix="fetch"
        ) as pool:
            futures = {pool.submit(self._fetch_one, sym, tf): (sym, tf)
                       for sym, tf in jobs}
            for future in as_completed(futures):
                sym, tf = futures[future]
                out[sym][tf] = future.result()   # _fetch_one never raises
                remaining[sym] -= 1
                if remaining[sym] == 0 and on_symbol is not None:
                    try:
                        on_symbol(sym, out[sym])
                    except Exception as exc:  # noqa: BLE001 — progress is advisory
                        log.error("scan progress callback failed: %s", exc)
        return out

    def _fetch_one(self, symbol: str, tf: Timeframe) -> pd.DataFrame:
        end = utcnow()
        try:
            return self.provider.get_candles(
                symbol, tf, end - timedelta(days=WINDOW_DAYS[tf]), end
            )
        except Exception as exc:  # noqa: BLE001
            log.error("candle fetch failed %s %s: %s", symbol, tf, exc)
            return pd.DataFrame()

    def _fetch_candles(self, symbol: str) -> dict[Timeframe, pd.DataFrame]:
        return {tf: self._fetch_one(symbol, tf) for tf in self._timeframes}

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
            # Record the richer experience alongside the journal (best-effort;
            # ExperienceEngine.record_trade never raises). The AI entry snapshot
            # gives the experience feature parity with a coached manual trade.
            self.experience.record_trade(
                record, entry_context=meta.entry_context or None)
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

    # ── working orders (manual trading) ──────────────────────────────────────

    def _evaluate_orders(self, now: datetime, summary: dict) -> None:
        if not self.orders.working():
            return
        spot_cache: dict[str, float | None] = {}
        chain_cache: dict[tuple, list] = {}

        def get_spot(underlying: str) -> float | None:
            if underlying not in spot_cache:
                try:
                    spot_cache[underlying] = self.provider.get_quote(underlying).last
                except Exception as exc:  # noqa: BLE001 — order stays working
                    log.error("order eval: quote failed for %s: %s", underlying, exc)
                    spot_cache[underlying] = None
            return spot_cache[underlying]

        def get_option_quote(contract) -> tuple[float, float]:
            key = (contract.underlying, contract.expiration)
            if key not in chain_cache:
                try:
                    chain_cache[key] = self.provider.get_option_chain(*key)
                except Exception as exc:  # noqa: BLE001
                    log.error("order eval: chain failed for %s: %s", key, exc)
                    chain_cache[key] = []
            live = next((c for c in chain_cache[key]
                         if c.symbol == contract.symbol), None)
            return (live.bid, live.ask) if live is not None else (0.0, 0.0)

        def approve_entry(order, ask: float) -> str | None:
            decision = self.approve_manual_entry(
                order.contract, order.quantity, now, premium=ask,
            )
            return decision.veto if not decision.approved else None

        for event in self.orders.evaluate(
            now, get_spot, get_option_quote, approve_entry=approve_entry,
        ):
            summary.setdefault("orders", []).append(event)
            order = event["order"]
            if event["event"] == "filled":
                if order["side"] == "buy_to_open":
                    self.register_manual_entry(order["contract"], entry_ts=now)
                self.notifier.notify(
                    "trade_opened" if order["side"] == "buy_to_open"
                    else "trade_closed",
                    f"Order filled: {order['kind']} {order['contract']} "
                    f"x{order['quantity']}",
                    order["result"],
                )
            elif event["event"] in ("expired", "rejected"):
                self.notifier.notify(
                    "risk_limit", f"Order {event['event']}: {order['contract']}",
                    order["result"],
                )

    # ── Human Mode: manual-trade reconciliation + coaching ───────────────────

    def approve_manual_entry(self, contract, quantity: int, now: datetime,
                             premium: float | None = None):
        """Risk preflight for every manual buy, including delayed limit fills."""
        existing = next(
            (p for p in self.broker.get_positions()
             if p.contract.symbol == contract.symbol),
            None,
        )
        return self.risk.approve_manual_entry(
            quantity=quantity,
            premium=premium if premium is not None and premium > 0 else contract.ask,
            open_positions=len(self.broker.get_positions()),
            now=now,
            is_new_position=existing is None,
            existing_quantity=existing.quantity if existing is not None else 0,
        )

    def register_manual_entry(self, contract_symbol: str,
                              entry_ts: datetime | None = None) -> None:
        """Called right after an immediate (market) manual fill so the round
        trip is tracked even if it opens and closes between scan cycles.
        Context is captured on the next cycle while the position is open."""
        if entry_ts is not None:
            self.risk.record_entry(entry_ts)
        position = next(
            (p for p in self.broker.get_positions()
             if p.contract.symbol == contract_symbol and p.managed_by == "manual"),
            None,
        )
        if position is None or contract_symbol in self._manual:
            return
        self._manual[contract_symbol] = {
            "direction": position.direction.value,
            "entry_ts": position.opened_at.isoformat(),
            "entry_price": position.avg_price,
            "quantity": position.quantity,
            "equity_at_entry": self.broker.get_account().equity,
            "entry_context": None,
        }
        self._manual_store.save(self._manual)

    def _reconcile_manual(self, now, candles, summary) -> None:
        open_manual = {p.contract.symbol: p
                       for p in self.broker.get_positions()
                       if p.managed_by == "manual"}
        # track new entries / backfill missing context while still open
        for symbol, position in open_manual.items():
            if symbol not in self._manual:
                self.register_manual_entry(symbol)
            meta = self._manual.get(symbol)
            if meta is not None and meta.get("entry_context") is None:
                meta["entry_context"] = self._capture_context(position, candles)
                self._manual_store.save(self._manual)
        # closed round trips -> journal + coach review
        for symbol in [s for s in self._manual if s not in open_manual]:
            meta = self._manual.pop(symbol)
            self._manual_store.save(self._manual)
            try:
                self._finalize_manual(symbol, meta, now, candles, summary)
            except Exception as exc:  # noqa: BLE001 — never break the cycle
                log.exception("manual trade finalize failed for %s: %s",
                              symbol, exc)

    def _capture_context(self, position: Position, candles) -> dict | None:
        """Analysis snapshot for the coach AND the Experience Engine, built by
        the one centralized snapshot builder so manual trades gain the same
        rich feature set as AI trades (feature symmetry). Best-effort."""
        try:
            underlying = position.contract.underlying
            symbol_candles = candles.get(underlying) or self._fetch_candles(underlying)
            decision = self.engine.evaluate(underlying, symbol_candles)
            spot = self.provider.get_quote(underlying).last
            live = self._lookup_contract(position)
            contract = live or position.contract
            if contract.delta == 0.0 and spot > 0:
                from optionspilot.analysis.options_metrics import enrich_greeks
                contract = enrich_greeks(contract, spot, utcnow().date())
            return build_snapshot(
                decision, spot=spot, contract=contract,
                operating_mode=self.cfg.engine.operating_mode,
                trading_mode=self.cfg.engine.trading_mode,
            )
        except Exception as exc:  # noqa: BLE001 — context is best-effort
            log.error("context capture failed for %s: %s",
                      position.contract.symbol, exc)
            return None

    def _finalize_manual(self, symbol, meta, now, candles, summary) -> None:
        fills = self.broker.fills_for(symbol)
        sells = [f for f in fills if f["side"] == "sell_to_close"]
        if not sells:
            log.warning("manual %s closed but no sell fills found — skipping",
                        symbol)
            return
        sold = sum(f["quantity"] for f in sells)
        exit_price = sum(f["price"] * f["quantity"] for f in sells) / sold
        commissions = sum(f["commission"] for f in fills)
        entry_ts = datetime.fromisoformat(meta["entry_ts"])
        exit_ts = datetime.fromisoformat(sells[-1]["ts"])
        underlying = symbol[:-15] if len(symbol) > 15 else symbol

        entry_context = meta.get("entry_context")
        trade = TradeRecord(
            id=f"{underlying}-M-{entry_ts:%Y%m%d-%H%M%S}",
            symbol=underlying, contract_symbol=symbol,
            direction=Direction(meta["direction"]), strategy="manual",
            quantity=meta["quantity"],
            entry_ts=entry_ts, entry_price=meta["entry_price"],
            exit_ts=exit_ts, exit_price=exit_price,
            commissions=commissions,
            confidence=(entry_context or {}).get("confidence", 0.0),
            entry_reasons=["manual trade (Human Mode)"],
            exit_reason=sells[-1]["reason"] or "manual close",
            market_conditions={
                "mode": "manual",
                "hour_et": str(entry_ts.astimezone(ET).hour),
            },
        )

        last_loss = next(
            (t for t in reversed(self.journal.all())
             if t.pnl < 0 and t.exit_ts < entry_ts), None)
        loss_minutes = ((entry_ts - last_loss.exit_ts).total_seconds() / 60
                        if last_loss is not None else None)

        exit_context = self._capture_context_for_symbol(underlying, candles)
        review = self.coach.review(
            trade,
            entry_context=entry_context,
            exit_context=exit_context,
            orders=self.orders.orders_for(symbol),
            recent_loss_minutes_before_entry=loss_minutes,
            equity_at_entry=meta.get("equity_at_entry", 0.0),
        )
        trade.mistakes = list(review.mistakes)
        trade.lessons = list(review.improvements)
        trade.market_conditions["coach_score"] = str(review.score)
        trade.market_conditions["setup_quality"] = review.setup_quality
        self.journal.record(trade)
        # Manual trades carry the richer entry/exit analysis context — feed it
        # to the experience store for higher-fidelity similarity features.
        self.experience.record_trade(
            trade, entry_context=entry_context, exit_context=exit_context,
        )
        self.risk.record_closed_trade(exit_ts, trade.pnl)
        summary["closed"].append({"symbol": symbol, "pnl": trade.pnl,
                                  "coach_score": review.score})
        self.notifier.notify(
            "trade_closed",
            f"Coach review: {review.score}/100 — "
            f"{trade.symbol} {review.verdict} {trade.pnl:+.2f}",
            review.summary,
        )

    def _capture_context_for_symbol(self, underlying: str, candles) -> dict | None:
        """Exit-time context: reuse the position-context capture on a synthetic
        wrapper (only underlying analysis matters at exit)."""
        try:
            symbol_candles = candles.get(underlying) or self._fetch_candles(underlying)
            decision = self.engine.evaluate(underlying, symbol_candles)
            spot = self.provider.get_quote(underlying).last
            return {
                "captured_ts": utcnow().isoformat(),
                "spot": spot,
                "confidence": decision.signal.confidence if decision.signal else 0.0,
                "direction": (decision.signal.direction.value
                              if decision.signal else "unknown"),
            }
        except Exception as exc:  # noqa: BLE001
            log.error("exit context capture failed for %s: %s", underlying, exc)
            return None

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
        if hasattr(self.broker, "record_equity_snapshot"):
            self.broker.record_equity_snapshot(now)

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
            **(decision.gate.to_dict() if decision.gate else {}),
        }
        if self.cfg.engine.operating_mode == "human":
            if decision.tradeable:
                summary["skipped"][symbol] = (
                    "Human Mode: the AI would take this trade — it's yours "
                    "if you want it (Trade tab)")
                self._advise_human(symbol, decision, summary)
            return
        if not decision.tradeable:
            if decision.gate is not None:
                summary["skipped"][symbol] = decision.gate.reason
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
        snapshot = self._ai_snapshot(decision, spot=spot, plan=plan)
        fill = self.broker.open_position(plan, approval.quantity, now)
        self.risk.record_entry(now)
        self._register_meta(plan, approval.quantity, fill, decision.gate, snapshot)
        # Advisory historical context — surfaced for explanation, never used in
        # the decision above (which has already been made deterministically).
        self._attach_historical(symbol, snapshot, decision, summary)
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

    def _advise_human(self, symbol: str, decision, summary=None) -> None:
        """In Human Mode a tradeable signal becomes advice, never an order.
        Notify once per symbol per bar so it doesn't spam every cycle."""
        bar_id = decision.entry_view.ts.isoformat() if decision.entry_view else ""
        if self._last_advice.get(symbol) == bar_id:
            return
        self._last_advice[symbol] = bar_id
        snapshot = self._ai_snapshot(decision)
        result = self._attach_historical(symbol, snapshot, decision, summary)
        hist_line = ""
        if result is not None and result.has_evidence:
            hist_line = "\n" + result.explain(decision.signal.confidence)
        self.notifier.notify(
            "trade_opened",
            f"AI signal (advice only): {symbol} "
            f"{decision.signal.direction.value} "
            f"{decision.signal.confidence:.0f}%",
            "Human Mode is on — the AI will not trade this.\n"
            + "\n".join(decision.signal.reasons[:5]) + hist_line,
        )

    # ── advisory historical context (Experience Engine — never affects trades) ─

    def _ai_snapshot(self, decision, spot=None, plan=None) -> dict:
        """Build the centralized AI decision snapshot (best-effort)."""
        try:
            return build_snapshot(
                decision, spot=spot, plan=plan,
                operating_mode=self.cfg.engine.operating_mode,
                trading_mode=self.cfg.engine.trading_mode,
            )
        except Exception as exc:  # noqa: BLE001 — advisory, must never break scan
            log.error("snapshot build failed for %s: %s",
                      getattr(getattr(decision, "signal", None), "symbol", "?"), exc)
            return {}

    def _attach_historical(self, symbol, snapshot, decision, summary):
        """Attach advisory historical-similarity evidence to a tradeable signal.
        Purely advisory: it is computed AFTER the deterministic decision and
        never feeds back into it. Best-effort — a failure never breaks the scan."""
        try:
            result = self.experience.explain_setup(snapshot)
        except Exception as exc:  # noqa: BLE001
            log.error("historical context failed for %s: %s", symbol, exc)
            return None
        if summary is not None:
            conf = decision.signal.confidence if decision.signal else 0.0
            summary.setdefault("signals", {}).setdefault(symbol, {})["historical"] = {
                "n_similar": result.n_similar,
                "win_rate": result.win_rate,
                "avg_return_pct": result.avg_return_pct,
                "avg_hold_minutes": result.avg_hold_minutes,
                "calibrated_confidence": result.calibrated_confidence,
                "common_successes": result.common_successes,
                "common_failures": result.common_failures,
                "explanation": result.explain(conf),
            }
        return result

    def experience_for_symbol(self, symbol: str, *, k: int = 20,
                              min_similarity: float = 0.3) -> dict:
        """Advisory historical context for a symbol's CURRENT setup — the Similar
        Trade Viewer's backing data. Evaluates the symbol deterministically and
        looks up comparable historical experiences. Purely advisory: it opens no
        position and changes no decision. The UI server calls this under its lock."""
        candles = self._fetch_candles(symbol)
        decision = self.engine.evaluate(symbol, candles)
        snapshot = self._ai_snapshot(decision)
        result = self.experience.explain_setup(
            snapshot, k=k, min_similarity=min_similarity)
        similar = self.experience.similar_to_snapshot(
            snapshot, k=k, min_similarity=min_similarity)
        signal = decision.signal
        conf = signal.confidence if signal else 0.0
        return {
            "symbol": symbol,
            "has_signal": signal is not None,
            "direction": signal.direction.value if signal else None,
            "deterministic_score": conf if signal else None,
            "tradeable": decision.tradeable,
            "reasoning": snapshot.get("reasoning", ""),
            "historical": {
                "n_similar": result.n_similar,
                "win_rate": result.win_rate,
                "avg_return_pct": result.avg_return_pct,
                "avg_hold_minutes": result.avg_hold_minutes,
                "avg_pnl": result.avg_pnl,
                "calibrated_confidence": result.calibrated_confidence,
                "most_common_exit": result.most_common_exit,
                "common_successes": result.common_successes,
                "common_failures": result.common_failures,
                "explanation": result.explain(conf),
            },
            "similar_trades": [t.to_dict() for t in similar],
        }

    def _register_meta(self, plan: TradePlan, quantity: int, fill: Fill,
                       gate=None, entry_context: dict | None = None) -> None:
        signal = plan.signal
        gate_conditions = {}
        if gate is not None:
            gate_conditions = {
                "mode": gate.mode,
                "setup_quality": gate.setup_quality,
                "min_confidence_used": f"{gate.min_confidence_required:.0f}",
            }
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
                **gate_conditions,
            },
            entry_context=entry_context or {},
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
