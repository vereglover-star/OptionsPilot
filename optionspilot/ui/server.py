"""UI backend: FastAPI app over one Orchestrator instance.

Threading model: the orchestrator is not thread-safe, so every touch — the
background cycle loop, manual scans, status reads that mark positions — goes
through one re-entrant lock. Backtests run on their own thread with their own
components (they never touch the live orchestrator) and are exposed as a
single job slot with polled status.

The frontend is one static HTML file (ui/static/index.html) — no build step,
no CDN, works offline and inside the PyInstaller bundle.
"""

from __future__ import annotations

import asyncio
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from optionspilot import __version__
from optionspilot.config.runtime import MAX_WATCHLIST, RuntimeSettings
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import utcnow
from optionspilot.data import symbols as symdir
from optionspilot.data.presets import PRESETS
from optionspilot.orchestrator import Orchestrator

log = get_logger("ui")

ET = ZoneInfo("America/New_York")
STATIC_DIR = Path(__file__).parent / "static"
MAX_EQUITY_POINTS = 2000


class UIServer:
    """Owns the orchestrator, the cycle loop, and the backtest job slot."""

    def __init__(self, config: AppConfig, orchestrator: Orchestrator | None = None,
                 runtime: RuntimeSettings | None = None,
                 data_dir: str | Path = "data"):
        self.cfg = config
        self.orch = orchestrator or Orchestrator(config)
        data_dir = Path(data_dir)
        # When constructed outside the CLI bootstrap, own a store (no overlay:
        # the caller's config is taken as-is; bootstrap applies overlays).
        self.runtime = runtime or RuntimeSettings(
            data_dir / "settings.json", baseline=config
        )
        self.lock = threading.RLock()
        self.last_summary: dict = {}
        self.equity_history: list[tuple[str, float]] = []
        self._loop_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._bt_lock = threading.Lock()
        self.backtest_job: dict = {"state": "idle"}
        self._meta_path = data_dir / "state" / "symbol_meta.json"
        self._symbol_meta: dict[str, dict] = self._load_meta()
        self._kick_meta_refresh(self.cfg.data.watchlist)

    # ── cycle loop ───────────────────────────────────────────────────────────

    def start_loop(self) -> None:
        if self._loop_thread is not None:
            return
        self._loop_thread = threading.Thread(
            target=self._loop, name="cycle-loop", daemon=True
        )
        self._loop_thread.start()

    def stop_loop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        log.info("cycle loop started (scan every %ds while market open)",
                 self.cfg.engine.scan_interval_seconds)
        while not self._stop.is_set():
            try:
                now = utcnow()
                if self.orch.market_open(now):
                    self.run_cycle_now()
                self.orch._maybe_send_summaries(now)
            except Exception as exc:  # noqa: BLE001 — loop must survive
                log.exception("ui cycle failed: %s", exc)
            self._stop.wait(
                self.cfg.engine.scan_interval_seconds
                if self.orch.market_open(utcnow()) else 60
            )

    def run_cycle_now(self) -> dict:
        with self.lock:
            summary = self.orch.run_cycle()
            self.last_summary = summary
            equity = self.orch.broker.get_account().equity
            self.equity_history.append((summary["ts"], equity))
            del self.equity_history[:-MAX_EQUITY_POINTS]
            return summary

    # ── payloads ─────────────────────────────────────────────────────────────

    def status_payload(self) -> dict:
        with self.lock:
            orch = self.orch
            acct = orch.broker.get_account()
            marks = (orch.broker.current_marks()
                     if hasattr(orch.broker, "current_marks") else {})
            positions = []
            for p in orch.broker.get_positions():
                mark = marks.get(p.contract.symbol, p.avg_price)
                positions.append({
                    "contract": p.contract.symbol,
                    "underlying": p.contract.underlying,
                    "expiration": p.contract.expiration.isoformat(),
                    "strike": p.contract.strike,
                    "right": p.contract.right.value,
                    "managed_by": p.managed_by,
                    "direction": p.direction.value,
                    "quantity": p.quantity,
                    "avg_price": round(p.avg_price, 2),
                    "mark": round(mark, 2),
                    "unrealized": round(p.unrealized_pnl(mark), 2),
                    "stop": p.stop_current,
                    "target": p.target,
                    "opened_at": p.opened_at.isoformat(),
                })
            now_et = utcnow().astimezone(ET)
            return {
                "version": __version__,
                "ts": utcnow().isoformat(),
                "market_open": orch.market_open(utcnow()),
                "paper": True,
                "account": {
                    "cash": acct.cash,
                    "equity": acct.equity,
                    "realized_pnl": acct.realized_pnl,
                    "starting_balance": self.cfg.risk.starting_balance,
                },
                "pnl": self._pnl_windows(now_et),
                "risk": orch.risk.status(),
                "positions": positions,
                "signals": self.last_summary.get("signals", {}),
                "skipped": self.last_summary.get("skipped", {}),
                "last_cycle_ts": self.last_summary.get("ts"),
                "watchlist": self.cfg.data.watchlist,
                "min_confidence": self.cfg.engine.min_confidence,
                "operating_mode": self.cfg.engine.operating_mode,
                "trading_mode": self.cfg.engine.trading_mode,
                "high_risk_floor": self.cfg.engine.high_risk_floor,
                "high_risk_min_rr_stretch": self.cfg.engine.high_risk_min_rr_stretch,
                "custom_settings": self.runtime.custom_settings(),
                "risk_settings": {
                    "risk_per_trade_pct": self.cfg.risk.risk_per_trade_pct,
                    "daily_trade_limit": self.cfg.risk.daily_trade_limit,
                    "max_contracts": self.cfg.risk.max_contracts,
                    "min_risk_reward": self.cfg.risk.min_risk_reward,
                    "max_daily_loss_pct": self.cfg.risk.max_daily_loss_pct,
                },
                "pinned": self.runtime.pinned(),
                "quotes": self.last_summary.get("quotes", {}),
                "setup_history": self._setup_history(),
                "equity_history": self.equity_history[-300:],
                "notifications": [
                    {"kind": e.kind, "title": e.title, "body": e.body,
                     "ts": e.ts.isoformat()}
                    for e in orch.notifier.history[-15:]
                ][::-1],
            }

    # ── manual trading (Human Mode order flow) ───────────────────────────────

    def chain_payload(self, symbol: str, expiration: str = "") -> dict:
        from optionspilot.analysis.options_metrics import enrich_greeks, liquidity_score

        symbol = symbol.upper()
        with self.lock:
            provider = self.orch.provider
            expirations = [e.isoformat() for e in provider.get_expirations(symbol)]
            if not expirations:
                return {"symbol": symbol, "expirations": [], "chain": []}
            exp = expiration or expirations[0]
            spot = provider.get_quote(symbol).last
            today = utcnow().date()
            chain = provider.get_option_chain(symbol, date.fromisoformat(exp))
            rows = []
            for c in chain:
                if c.delta == 0.0:
                    c = enrich_greeks(c, spot, today)
                rows.append({
                    "strike": c.strike, "right": c.right.value,
                    "bid": c.bid, "ask": c.ask, "mid": round(c.mid, 2),
                    "delta": round(c.delta, 3), "iv": round(c.implied_volatility, 4),
                    "volume": c.volume, "open_interest": c.open_interest,
                    "liquidity": liquidity_score(c),
                    "dte": c.dte(today),
                })
            return {"symbol": symbol, "spot": spot, "expiration": exp,
                    "expirations": expirations, "chain": rows}

    def place_order(self, payload: dict) -> dict:
        from optionspilot.broker.orders import OrderKind, TIF
        from optionspilot.core.models import OptionRight

        kind = OrderKind(str(payload.get("kind", "market")))
        tif = TIF(str(payload.get("tif", "day")))
        side = str(payload.get("side", "buy_to_open"))
        symbol = str(payload.get("symbol", "")).upper()
        expiration = date.fromisoformat(str(payload.get("expiration")))
        strike = float(payload.get("strike"))
        right = OptionRight(str(payload.get("right")))
        quantity = int(payload.get("quantity", 1))

        with self.lock:
            provider = self.orch.provider
            chain = provider.get_option_chain(symbol, expiration)
            contract = next(
                (c for c in chain
                 if c.strike == strike and c.right is right), None)
            if contract is None:
                raise ValueError(
                    f"no {right.value} @ {strike} for {symbol} {expiration}")
            try:
                spot = provider.get_quote(symbol).last
            except Exception:  # noqa: BLE001 — spot is advisory for buys
                spot = 0.0
            order, event = self.orch.orders.place(
                kind=kind, side=side, contract=contract, quantity=quantity,
                ts=utcnow(), tif=tif,
                limit_price=float(payload.get("limit_price") or 0),
                stop_level=float(payload.get("stop_level") or 0),
                trail=float(payload.get("trail") or 0),
                trail_pct=float(payload.get("trail_pct") or 0),
                spot=spot,
            )
            if (event and event["event"] == "filled"
                    and side == "buy_to_open"):
                # track immediately so fast round trips still get coached
                self.orch.register_manual_entry(contract.symbol)
        return {"order": order.to_dict(),
                "event": event["event"] if event else "working"}

    def account_metrics(self) -> dict:
        with self.lock:
            broker = self.orch.broker
            acct = broker.get_account()
            trades = self.orch.journal.all()
            marks = (broker.current_marks()
                     if hasattr(broker, "current_marks") else {})
            unrealized = sum(
                p.unrealized_pnl(marks.get(p.contract.symbol, p.avg_price))
                for p in broker.get_positions()
            )
            history = (broker.equity_history()
                       if hasattr(broker, "equity_history") else [])
            now_et = utcnow().astimezone(ET)
            day_start = datetime.combine(now_et.date(), time(0), tzinfo=ET)
            daily = sum(t.pnl for t in trades
                        if t.exit_ts.astimezone(ET) >= day_start)
        start = self.cfg.risk.starting_balance
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        max_dd = 0.0
        peak = start
        for _, equity in history:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        return {
            "cash": acct.cash,
            "buying_power": acct.cash,      # options buying power = cash (no margin)
            "portfolio_value": acct.equity,
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": acct.realized_pnl,
            "daily_pnl": round(daily, 2),
            "total_return_pct": round((acct.equity / start - 1) * 100, 2),
            "trades": len(trades),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": (round(gross_win / gross_loss, 2)
                              if gross_loss else None),
            "max_drawdown_pct": round(max_dd, 2),
            "equity_history": history[-500:],
        }

    # ── watchlist management ─────────────────────────────────────────────────

    def watchlist_add(self, text: str) -> dict:
        """Parse free-form input (single ticker, comma/space/newline lists,
        pasted from anywhere), validate each symbol, add the valid ones."""
        requested = symdir.parse_symbols(text)
        result = {"added": [], "invalid": [], "duplicates": [], "over_cap": [],
                  "names": {}}
        if not requested:
            result["error"] = "no ticker symbols found in the input"
            return result
        with self.lock:
            current = list(self.cfg.data.watchlist)
            for symbol in requested:
                if symbol in current:
                    result["duplicates"].append(symbol)
                elif len(current) >= MAX_WATCHLIST:
                    result["over_cap"].append(symbol)
                elif symdir.is_known(symbol) or self._live_symbol_check(symbol):
                    current.append(symbol)
                    result["added"].append(symbol)
                    result["names"][symbol] = symdir.company_name(symbol)
                else:
                    result["invalid"].append(symbol)
            if result["added"]:
                self.runtime.set_watchlist(self.cfg, current)
        if result["over_cap"]:
            result["error"] = (f"watchlist is capped at {MAX_WATCHLIST} symbols "
                               f"(scan time grows with each one)")
        if result["added"]:
            self._kick_meta_refresh(result["added"])
            log.info("watchlist add: +%s (invalid: %s, dupes: %s)",
                     result["added"], result["invalid"], result["duplicates"])
        return result

    def watchlist_remove(self, symbol: str) -> dict:
        symbol = symbol.upper()
        with self.lock:
            current = [s for s in self.cfg.data.watchlist if s != symbol]
            if len(current) == len(self.cfg.data.watchlist):
                return {"error": f"{symbol} is not on the watchlist"}
            self.runtime.set_watchlist(self.cfg, current)   # raises if empty
        log.info("watchlist remove: %s", symbol)
        return {"removed": symbol, "watchlist": current}

    def watchlist_reorder(self, symbols: list[str]) -> dict:
        symbols = [s.upper() for s in symbols]
        with self.lock:
            if sorted(symbols) != sorted(self.cfg.data.watchlist):
                return {"error": "reorder must contain exactly the current symbols"}
            self.runtime.set_watchlist(self.cfg, symbols)
        return {"watchlist": symbols}

    def watchlist_payload(self) -> dict:
        with self.lock:
            return {
                "watchlist": list(self.cfg.data.watchlist),
                "pinned": self.runtime.pinned(),
                "favorites": self.runtime.favorites(),
                "max": MAX_WATCHLIST,
                "meta": dict(self._symbol_meta),
                "quotes": self.last_summary.get("quotes", {}),
                "signals": self.last_summary.get("signals", {}),
            }

    def _live_symbol_check(self, symbol: str) -> bool:
        """Fallback for tickers missing from the bundled directory: a real
        quote proves the symbol exists."""
        try:
            return self.orch.provider.get_quote(symbol).last > 0
        except Exception:  # noqa: BLE001 — any failure means 'not validated'
            return False

    # ── symbol metadata (names + market caps, for display and sorting) ──────

    def _load_meta(self) -> dict:
        try:
            if self._meta_path.exists():
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _kick_meta_refresh(self, symbols: list[str]) -> None:
        missing = [s for s in symbols if s not in self._symbol_meta]
        if not missing:
            return
        threading.Thread(target=self._refresh_meta, args=(missing,),
                         daemon=True, name="symbol-meta").start()

    def _refresh_meta(self, symbols: list[str]) -> None:
        get_cap = getattr(self.orch.provider, "get_market_cap", None)
        for symbol in symbols:
            meta = {"name": symdir.company_name(symbol),
                    "market_cap": get_cap(symbol) if get_cap else None}
            with self.lock:
                self._symbol_meta[symbol] = meta
        with self.lock:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            self._meta_path.write_text(
                json.dumps(self._symbol_meta, indent=1), encoding="utf-8")

    def _setup_history(self) -> dict:
        """Measured win rate per setup quality from the journal — the honest
        'estimated probability of success' (n/a until enough history exists)."""
        buckets: dict[str, list[bool]] = {}
        for t in self.orch.journal.all():
            quality = t.market_conditions.get("setup_quality")
            if quality:
                buckets.setdefault(quality, []).append(t.is_win)
        return {
            q: {"trades": len(v), "win_rate": round(sum(v) / len(v), 3)}
            for q, v in buckets.items()
        }

    def _pnl_windows(self, now_et: datetime) -> dict:
        day_start = datetime.combine(now_et.date(), time(0), tzinfo=ET)
        week_start = day_start - timedelta(days=now_et.weekday())
        month_start = day_start.replace(day=1)
        with self.lock:
            def pnl_since(start):
                return round(sum(
                    t.pnl for t in self.orch.journal.query(start=start)), 2)
            return {
                "today": pnl_since(day_start),
                "week": pnl_since(week_start),
                "month": pnl_since(month_start),
            }

    # ── backtest job ─────────────────────────────────────────────────────────

    def start_backtest(self, symbol: str, days: int, min_confidence: float | None
                       ) -> dict:
        with self._bt_lock:
            if self.backtest_job.get("state") == "running":
                return self.backtest_job
            self.backtest_job = {"state": "running", "symbol": symbol.upper(),
                                 "started": utcnow().isoformat()}
        threading.Thread(
            target=self._run_backtest, args=(symbol.upper(), days, min_confidence),
            daemon=True, name="backtest",
        ).start()
        return self.backtest_job

    def _run_backtest(self, symbol: str, days: int,
                      min_confidence: float | None) -> None:
        try:
            from optionspilot.backtest import Backtester
            from optionspilot.core.models import Timeframe

            cfg = self.cfg.model_copy(deep=True)
            if min_confidence is not None:
                cfg.engine.min_confidence = min_confidence
            end = utcnow()
            windows = {1: 5, 5: 10, 15: min(days, 55), 60: 60, 240: 100, 1440: 300}
            candles = {}
            for s in {*cfg.engine.entry_timeframes, *cfg.engine.htf_trend_timeframes}:
                tf = Timeframe.from_string(s)
                candles[tf] = self.orch.provider.get_candles(
                    symbol, tf, end - timedelta(days=windows[tf.minutes]), end)
            report = Backtester(cfg).run(symbol, candles)
            report.save_json(Path("data") / "reports" / f"{symbol.lower()}.json")
            report.save_html(Path("data") / "reports" / f"{symbol.lower()}.html")
            with self._bt_lock:
                self.backtest_job = {"state": "done", "symbol": symbol,
                                     "report": report.to_dict()}
        except Exception as exc:  # noqa: BLE001
            log.exception("backtest failed: %s", exc)
            with self._bt_lock:
                self.backtest_job = {"state": "error", "symbol": symbol,
                                     "error": str(exc)}


def create_app(config: AppConfig, orchestrator: Orchestrator | None = None,
               run_loop: bool = False,
               runtime: RuntimeSettings | None = None,
               data_dir: str | Path = "data") -> FastAPI:
    server = UIServer(config, orchestrator, runtime, data_dir)
    app = FastAPI(title="OptionsPilot", version=__version__)
    app.state.server = server
    if run_loop:
        server.start_loop()

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status():
        return server.status_payload()

    @app.post("/api/scan")
    def scan():
        return server.run_cycle_now()

    @app.get("/api/journal")
    def journal(last: int = 50):
        with server.lock:
            trades = server.orch.journal.all()[-last:]
            stats = server.orch.journal.stats()
        return {
            "stats": stats,
            "trades": [{
                "id": t.id, "symbol": t.symbol, "contract": t.contract_symbol,
                "direction": t.direction.value, "quantity": t.quantity,
                "entry_ts": t.entry_ts.isoformat(), "entry_price": t.entry_price,
                "exit_ts": t.exit_ts.isoformat(), "exit_price": round(t.exit_price, 2),
                "pnl": round(t.pnl, 2), "confidence": t.confidence,
                "entry_reasons": t.entry_reasons, "exit_reason": t.exit_reason,
                "conditions": t.market_conditions,
                "mistakes": t.mistakes, "lessons": t.lessons,
            } for t in reversed(trades)],
        }

    @app.get("/api/learning")
    def learning():
        from optionspilot.engine.scorer import DEFAULT_WEIGHTS
        from optionspilot.learning import LearningEngine, WeightStore

        with server.lock:
            engine = LearningEngine(server.orch.journal)
            store = WeightStore(Path("data") / "learning" / "weights.json")

            def rows(slices):
                return [{"label": s.label, "trades": s.trades,
                         "win_rate": s.win_rate, "expectancy": s.expectancy,
                         "profit_factor": (None if s.profit_factor == float("inf")
                                           else s.profit_factor)}
                        for s in slices]
            return {
                "weights_version": store.version(),
                "weights": {k: {"default": DEFAULT_WEIGHTS[k],
                                "learned": store.current().get(k),
                                "effective": server.orch.engine.scorer.weights[k]}
                            for k in DEFAULT_WEIGHTS},
                "by_evidence": rows(engine.by_evidence()),
                "by_hour": rows(engine.by_hour_et()),
                "by_confidence": rows(engine.by_confidence()),
                "by_exit_reason": rows(engine.by_exit_reason()),
            }

    @app.get("/api/config")
    def config_view():
        return JSONResponse(config.model_dump(mode="json"))

    @app.get("/api/chain")
    def chain_view(symbol: str, expiration: str = ""):
        try:
            return server.chain_payload(symbol, expiration)
        except Exception as exc:  # noqa: BLE001 — surface as a clean 502
            log.error("chain fetch failed: %s", exc)
            return JSONResponse({"error": f"chain unavailable: {exc}"},
                                status_code=502)

    @app.get("/api/orders")
    def orders_view():
        with server.lock:
            return {
                "working": [o.to_dict() for o in server.orch.orders.working()],
                "history": server.orch.orders.history(50),
            }

    @app.post("/api/orders")
    def orders_place(payload: dict):
        from optionspilot.broker.base import BrokerError

        try:
            return server.place_order(payload)
        except (ValueError, KeyError, TypeError, BrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/orders/cancel")
    def orders_cancel(payload: dict):
        from optionspilot.broker.base import BrokerError

        try:
            with server.lock:
                order = server.orch.orders.cancel(str(payload.get("id", "")))
            return order.to_dict()
        except BrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.get("/api/account/metrics")
    def account_metrics():
        return server.account_metrics()

    @app.get("/api/watchlist")
    def watchlist_view():
        return server.watchlist_payload()

    @app.post("/api/watchlist/add")
    def watchlist_add(payload: dict):
        return server.watchlist_add(str(payload.get("text", "")))

    @app.post("/api/watchlist/remove")
    def watchlist_remove(payload: dict):
        out = server.watchlist_remove(str(payload.get("symbol", "")))
        return JSONResponse(out, status_code=422 if "error" in out else 200)

    @app.post("/api/watchlist/reorder")
    def watchlist_reorder(payload: dict):
        out = server.watchlist_reorder(list(payload.get("symbols", [])))
        return JSONResponse(out, status_code=422 if "error" in out else 200)

    @app.post("/api/watchlist/pin")
    def watchlist_pin(payload: dict):
        with server.lock:
            pinned = server.runtime.set_pinned(
                str(payload.get("symbol", "")), bool(payload.get("pinned", True))
            )
        return {"pinned": pinned}

    @app.post("/api/watchlist/favorites")
    def watchlist_favorites(payload: dict):
        with server.lock:
            symbols = payload.get("symbols") or list(server.cfg.data.watchlist)
            server.runtime.save_favorites([str(s) for s in symbols])
        return {"favorites": server.runtime.favorites()}

    @app.get("/api/watchlist/presets")
    def watchlist_presets():
        return {**PRESETS, "My Favorites": server.runtime.favorites()}

    @app.get("/api/symbols/search")
    def symbols_search(q: str = ""):
        return {"results": symdir.search(q)}

    @app.post("/api/mode")
    def set_mode(payload: dict):
        mode = str(payload.get("mode", ""))
        custom = payload.get("custom")
        try:
            with server.lock:
                server.runtime.set_mode(server.cfg, mode, custom)
        except (ValueError, ValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {
            "trading_mode": server.cfg.engine.trading_mode,
            "min_confidence": server.cfg.engine.min_confidence,
            "custom_settings": server.runtime.custom_settings(),
        }

    @app.post("/api/operating_mode")
    def set_operating_mode(payload: dict):
        try:
            with server.lock:
                server.runtime.set_operating_mode(
                    server.cfg, str(payload.get("mode", "")))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"operating_mode": server.cfg.engine.operating_mode}

    @app.get("/api/coach")
    def coach_view():
        from optionspilot.coach import CoachProfile

        with server.lock:
            reviews = server.orch.coach.load_all()
        reviews.sort(key=lambda r: r.get("trade_id", ""), reverse=True)
        return {
            "profile": CoachProfile(reviews).build(),
            "reviews": reviews[:50],
        }

    @app.post("/api/risk/reset_halt")
    def reset_halt():
        with server.lock:
            server.orch.risk.reset_halt()
            return server.orch.risk.status()

    @app.post("/api/backtest")
    def backtest(payload: dict):
        return server.start_backtest(
            str(payload.get("symbol", "SPY")),
            int(payload.get("days", 25)),
            payload.get("min_confidence"),
        )

    @app.get("/api/backtest")
    def backtest_status():
        with server._bt_lock:
            return server.backtest_job

    @app.post("/webhook/tradingview")
    def tradingview(payload: dict):
        from optionspilot.integrations import parse_alert

        icfg = config.integrations
        if not icfg.tradingview_webhook:
            return JSONResponse(
                {"error": "tradingview webhook disabled in config"},
                status_code=403,
            )
        try:
            alert = parse_alert(payload, icfg.tradingview_secret)
        except ValueError as exc:
            log.warning("rejected tradingview webhook: %s", exc)
            code = 403 if "secret" in str(exc) else 422
            return JSONResponse({"error": str(exc)}, status_code=code)
        log.info("tradingview alert: scan %s (%s)", alert.symbol,
                 alert.note or "no note")
        with server.lock:
            summary = server.orch.scan_single(alert.symbol)
        return {"source": "tradingview", "symbol": alert.symbol,
                "note": alert.note, **summary}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        try:
            while True:
                await socket.send_json(server.status_payload())
                await asyncio.sleep(2.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8787,
          run_loop: bool = True,
          runtime: RuntimeSettings | None = None) -> None:  # pragma: no cover - blocking server
    import uvicorn

    app = create_app(config, run_loop=run_loop, runtime=runtime)
    print(f"OptionsPilot dashboard: http://{host}:{port}  (paper trading only)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
