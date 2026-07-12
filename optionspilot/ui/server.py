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
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from optionspilot import __version__
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import utcnow
from optionspilot.orchestrator import Orchestrator

log = get_logger("ui")

ET = ZoneInfo("America/New_York")
STATIC_DIR = Path(__file__).parent / "static"
MAX_EQUITY_POINTS = 2000


class UIServer:
    """Owns the orchestrator, the cycle loop, and the backtest job slot."""

    def __init__(self, config: AppConfig, orchestrator: Orchestrator | None = None):
        self.cfg = config
        self.orch = orchestrator or Orchestrator(config)
        self.lock = threading.RLock()
        self.last_summary: dict = {}
        self.equity_history: list[tuple[str, float]] = []
        self._loop_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._bt_lock = threading.Lock()
        self.backtest_job: dict = {"state": "idle"}

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
                "equity_history": self.equity_history[-300:],
                "notifications": [
                    {"kind": e.kind, "title": e.title, "body": e.body,
                     "ts": e.ts.isoformat()}
                    for e in orch.notifier.history[-15:]
                ][::-1],
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
               run_loop: bool = False) -> FastAPI:
    server = UIServer(config, orchestrator)
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
          run_loop: bool = True) -> None:  # pragma: no cover - blocking server
    import uvicorn

    app = create_app(config, run_loop=run_loop)
    print(f"OptionsPilot dashboard: http://{host}:{port}  (paper trading only)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
