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
import sys
import threading
import tracemalloc
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from optionspilot import __version__
from optionspilot.analysis import indicators as ind
from optionspilot.analysis.options_metrics import enrich_greeks, liquidity_score
from optionspilot.backtest import Backtester
from optionspilot.broker.base import BrokerError
from optionspilot.broker.orders import OrderKind, TIF
from optionspilot.coach import CoachProfile, build_dashboard
from optionspilot.config.runtime import RuntimeSettings
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import OptionRight, Timeframe, utcnow
from optionspilot.core.paths import AppPaths
from optionspilot.data import replay as mdreplay
from optionspilot.data import report as mdreport
from optionspilot.data import sessions
from optionspilot.data import symbols as symdir
from optionspilot.data.base import validate_candles
from optionspilot.data.presets import PRESETS
from optionspilot.engine.scorer import DEFAULT_WEIGHTS
from optionspilot.intelligence import (
    Goal, IntelligenceSnapshot, build_evidence_index,
)
from optionspilot.intelligence.goals import TEMPLATES as GOAL_TEMPLATES
from optionspilot.intelligence.performance import METRIC_SPECS
from optionspilot.integrations import parse_alert
from optionspilot.learning import LearningEngine, WeightStore
from optionspilot.orchestrator import WINDOW_DAYS, Orchestrator
from optionspilot.services import IdempotencyStore, ServiceRegistry
from optionspilot.services.runtime import BackgroundRuntime, RuntimeSnapshot, TaskSpec
from optionspilot.host import current_host
from optionspilot.services import intelligence as intel_view
from optionspilot.services import sync as sync_boundaries
from optionspilot.ui import guide
from optionspilot.ui.api_v1 import register_v1_routes
from optionspilot.update.models import UpdateError
from optionspilot.update.service import UpdateService

log = get_logger("ui")


def _experience_dict(rec) -> dict:
    """Flatten an ExperienceRecord to the fields the Experience/Recent view
    needs (a stable, JSON-friendly subset — the full record stays in the store)."""
    return {
        "trade_id": rec.trade_id,
        "date": rec.entry_ts.date().isoformat(),
        "symbol": rec.symbol,
        "timeframe": rec.timeframe,
        "direction": rec.direction,
        "managed_by": rec.managed_by,
        "outcome": "win" if rec.is_win else "loss",
        "pnl": rec.pnl,
        "return_pct": rec.return_pct,
        "confidence": rec.confidence_entry,
        "setup_quality": rec.setup_quality,
        "market_regime": rec.market_regime,
        "exit_reason": rec.exit_reason,
    }

ET = ZoneInfo("America/New_York")
STATIC_DIR = Path(__file__).parent / "static"
MAX_EQUITY_POINTS = 2000

# V0.7.0: the intelligence projections moved to `services/intelligence.py`.
# They decide which twelve of thirty-eight metrics are a headline and how much
# of a five-year series a client receives — presentation decisions, reachable
# now without importing a web framework. Re-exported under their historical
# names so anything importing them from here keeps working.
PERIOD_LIMITS = intel_view.PERIOD_LIMITS
SUMMARY_METRICS = intel_view.SUMMARY_METRICS
_intelligence_payload = intel_view.payload


def _intelligence_summary(snapshot: IntelligenceSnapshot) -> dict:
    return intel_view.summary(snapshot)


class UIServer:
    """Owns the orchestrator, the cycle loop, and the backtest job slot."""

    def __init__(self, config: AppConfig, orchestrator: Orchestrator | None = None,
                 runtime: RuntimeSettings | None = None,
                 data_dir: str | Path | None = None):
        self.cfg = config
        # Default to the per-user storage root (AppPaths) so the UI's own state
        # (settings, symbol metadata, backtest reports) lives with all other
        # user data, never beside the executable.
        data_dir = Path(data_dir) if data_dir is not None else AppPaths().get_data_dir()
        self.orch = orchestrator or Orchestrator(config, data_dir=data_dir)
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
        self._close_lock = threading.Lock()
        self._closed = False
        self.background = BackgroundRuntime(health_check=self._health_check)
        self._runtime_health: dict = {"state": "healthy", "last_check": None,
                                      "issues": [], "repairs": [], "memory": {}}
        # Tracing every allocation is intentionally a live-runtime diagnostic,
        # not a side effect of constructing an app for an HTTP request or a
        # test.  The server that starts it also releases it during shutdown.
        self._owns_memory_tracing = False
        self._health_memory_baseline = 0
        self._bt_lock = threading.Lock()
        self.backtest_job: dict = {"state": "idle"}
        # Scan pipeline: candle fetching runs OUTSIDE self.lock (it only
        # touches the thread-safe provider), so status reads and the UI stay
        # responsive during a scan. _cycle_lock serializes whole cycles so the
        # background loop and a manual scan can never interleave.
        self._cycle_lock = threading.Lock()
        self.scan_state: dict = {"running": False, "done": 0, "total": 0}
        self._journal_cache: tuple[int, list] | None = None
        self._coach_cache: tuple[int, dict] | None = None
        self._meta_path = data_dir / "state" / "symbol_meta.json"
        self._reports_dir = data_dir / "reports"
        self._symbol_meta: dict[str, dict] = self._load_meta()
        # Metadata enrichment is background work, not construction work.  In
        # particular, creating an application for a request/test must not
        # silently create a network-capable worker with no lifecycle owner.
        # The runtime drains this queue after it has started.
        self._meta_pending: set[str] = set()
        self._queue_meta_refresh(self.cfg.data.watchlist)
        # Self-updater: reads GitHub Releases, downloads to a temp dir, and (on
        # explicit user action) launches the installer. Preferences persist via
        # RuntimeSettings. Constructing it touches no network — a launch-time
        # check is kicked separately (see create_app), only for the real app.
        self.updater = UpdateService(__version__, self.runtime)
        # V0.7.0: the platform-independent application layer. Everything that
        # decides WHAT a client is shown — portfolio statistics, watchlist
        # classification, intelligence projections, workspace state,
        # notification routing — lives behind this and is reachable without
        # importing FastAPI. `UIServer` keeps its method names so nothing that
        # calls them had to change; the bodies now delegate.
        self._data_dir = data_dir
        self.idempotency = IdempotencyStore(data_dir / "state" / "idempotency.db")
        self.services = ServiceRegistry(
            orchestrator=self.orch,
            runtime=self.runtime,
            config=self.cfg,
            lock=self.lock,
            directory=symdir,
            # Bound LATE, through the attribute, rather than by handing over the
            # bound method here. These three are overridable seams — a test
            # replaces `_live_symbol_check` to keep validation offline, and a
            # future host could replace any of them — and a bound method
            # captured at construction silently ignores every later
            # reassignment. Caught by an existing test rather than in
            # production, which is the only reason it is a footnote.
            trades=lambda: self._all_trades(),
            verify_symbol=lambda symbol: self._live_symbol_check(symbol),
            on_symbols_added=self._queue_meta_refresh,
            log=log,
        )

    # ── cycle loop ───────────────────────────────────────────────────────────

    def start_loop(self) -> None:
        if self._closed:
            raise RuntimeError("UI server is closed")
        if self.background.snapshot().running:
            return
        self._start_memory_monitoring()
        prefs = self.runtime.runtime_prefs()
        self.background.set_profile(prefs["hidden_profile"])
        if not prefs["auto_resume_monitoring"]:
            self.background.pause()
        task_names = {task["name"] for task in self.background.snapshot().tasks}
        if "market_monitor" not in task_names:
            self.background.register(TaskSpec(
                "market_monitor", self.cfg.engine.scan_interval_seconds,
                self._background_cycle, policy="monitoring"))
        if "symbol_metadata" not in task_names:
            self.background.register(TaskSpec(
                "symbol_metadata", 60.0, self._refresh_pending_meta,
                policy="normal"))
        self.background.start()

    def stop_loop(self) -> None:
        self._stop.set()
        self.background.stop()

    def close(self) -> None:
        """Stop the one scheduler before releasing owned application resources."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.stop_loop()
        self.orch.close()
        self.idempotency.close()
        self._stop_memory_monitoring()

    def set_background_visibility(self, visible: bool) -> None:
        self.background.set_visibility(visible)

    def runtime_payload(self) -> dict:
        snap = self.background.snapshot()
        return {
            "settings": self.runtime.runtime_prefs(),
            "background": snap.to_dict(),
            "health": dict(self._runtime_health),
        }

    def update_runtime(self, patch: dict | None) -> dict:
        prefs = self.runtime.set_runtime_prefs(**(patch or {}))
        self.background.set_profile(prefs["hidden_profile"])
        if prefs["auto_resume_monitoring"] and self.background.snapshot().paused:
            self.background.resume()
        current_host().set_startup(
            prefs["start_with_windows"], "\"%s\" ui" % sys.executable)
        return self.runtime_payload()

    def pause_background(self) -> dict:
        self.background.pause()
        return self.runtime_payload()

    def resume_background(self) -> dict:
        self.background.resume()
        return self.runtime_payload()

    def _background_cycle(self) -> None:
        now = utcnow()
        if self.orch.market_open(now):
            self.run_cycle_now()
        self.orch._maybe_send_summaries(now)

    def _health_check(self, snapshot: RuntimeSnapshot) -> None:
        issues = []
        repairs = []
        for task in snapshot.tasks:
            if task["recovery_pending"]:
                repairs.append(f"{task['name']}: retry scheduled")
            elif task["failures"] and task["last_error"]:
                issues.append(f"{task['name']}: {task['last_error']}")
        current, peak = tracemalloc.get_traced_memory()
        memory = {"current_bytes": current, "peak_bytes": peak,
                  "baseline_bytes": self._health_memory_baseline}
        if (self._health_memory_baseline > 1_000_000 and
                current > self._health_memory_baseline * 3):
            issues.append("traced memory grew beyond the health threshold")
        self._runtime_health = {
            "state": "degraded" if issues else "healthy",
            "last_check": utcnow().isoformat(),
            "issues": issues,
            "repairs": repairs,
            "memory": memory,
        }
        if issues:
            self.orch.notifier.notify(
                "error", "Background health check needs attention", issues[0])

    def _start_memory_monitoring(self) -> None:
        if not tracemalloc.is_tracing():
            # ONE frame, not ten. The health check reads `get_traced_memory()`,
            # which reports totals and never looks at a traceback; nothing in
            # the application calls `take_snapshot()`. Ten frames per live
            # allocation was pure overhead — paid on every allocation, for the
            # whole life of a desktop session, to produce identical numbers.
            tracemalloc.start(1)
            self._owns_memory_tracing = True
        self._health_memory_baseline = tracemalloc.get_traced_memory()[0]

    def _stop_memory_monitoring(self) -> None:
        if self._owns_memory_tracing:
            tracemalloc.stop()
            self._owns_memory_tracing = False
        self._health_memory_baseline = 0

    def _loop(self) -> None:
        """Legacy loop body retained for embedders; new starts use BackgroundRuntime."""
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
        """One full cycle: parallel candle prefetch (no orchestrator lock, with
        live progress for the UI), then the stateful cycle under the lock."""
        with self._cycle_lock:
            symbols = list(self.cfg.data.watchlist)
            self.scan_state = {"running": True, "done": 0, "total": len(symbols)}
            try:
                candles = self.orch.fetch_watchlist_candles(
                    symbols, on_symbol=self._on_symbol_fetched)
                with self.lock:
                    summary = self.orch.run_cycle(candles=candles)
                    self.last_summary = summary
                    equity = self.orch.broker.get_account().equity
                    self.equity_history.append((summary["ts"], equity))
                    del self.equity_history[:-MAX_EQUITY_POINTS]
                    return summary
            finally:
                self.scan_state = {"running": False,
                                   "done": len(symbols), "total": len(symbols)}

    def _on_symbol_fetched(self, symbol: str, frames: dict) -> None:
        """Progressive scan feedback: as each symbol's candles land, publish
        its fresh quote so watchlist prices tick in while the scan runs."""
        quote = self.orch._quote_snapshot(frames)
        with self.lock:
            state = dict(self.scan_state)
            state["done"] = state.get("done", 0) + 1
            self.scan_state = state
            if quote:
                self.last_summary.setdefault("quotes", {})[symbol] = quote

    def request_scan(self) -> dict:
        """Non-blocking manual scan: start a cycle in the background (unless
        one is already running) and return immediately. Progress is surfaced
        in every status payload / WS push as `scan`."""
        if not self.scan_state.get("running") and not self._cycle_lock.locked():
            threading.Thread(target=self._background_scan,
                             daemon=True, name="manual-scan").start()
        return {"state": "started", "scan": self.scan_state}

    def _background_scan(self) -> None:
        try:
            self.run_cycle_now()
        except Exception as exc:  # noqa: BLE001 — surfaced via logs/status
            log.exception("manual scan failed: %s", exc)

    # ── payloads ─────────────────────────────────────────────────────────────

    def _coach_dashboard(self, reviews: list[dict]) -> dict:
        """Cached Coach 2.0 dashboard. Recomputed only when the review count
        changes — reviews are write-once per trade, so count is a valid key."""
        key = len(reviews)
        if self._coach_cache is None or self._coach_cache[0] != key:
            self._coach_cache = (key, build_dashboard(reviews))
        return self._coach_cache[1]

    def status_payload(self) -> dict:
        with self.lock:
            orch = self.orch
            positions = [p.to_dict() for p in self.services.portfolio.positions()]
            now_et = utcnow().astimezone(ET)
            return {
                "version": __version__,
                "ts": utcnow().isoformat(),
                "scan": dict(self.scan_state),
                "market_open": orch.market_open(utcnow()),
                "paper": True,
                "account": self.services.portfolio.account().to_dict(),
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
                # Newest first, and the ordering is the service's decision
                # rather than each client's — see NotificationService.recent.
                "notifications": [n.to_dict()
                                  for n in self.services.notifications.recent(15)],
                "runtime": self.runtime_payload(),
            }

    # ── chart workspace data ─────────────────────────────────────────────────

    def candles_payload(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        extended_hours: bool = False,
    ) -> dict:
        """OHLCV + indicator series for the Charts tab, computed by the SAME
        analysis library the engine trades with (guaranteed visual parity).
        Provider-only — no orchestrator state, so no lock is taken and chart
        loads never contend with a running scan."""
        symbol = symbol.upper()
        tf = Timeframe.from_string(timeframe)
        end = end or utcnow()
        start = start or (end - timedelta(days=WINDOW_DAYS[tf]))
        if end < start:
            start, end = end, start
        # display surface: prefer clearly-flagged stale bars over a blank
        # chart when the live fetch fails (feature-detected so tests that
        # inject bare fake providers keep working)
        # Extended hours only exists for intraday intervals; daily+ bars are RTH
        # aggregates so the flag is forced off there (keeps the cache key and the
        # session tagging honest).
        ext = extended_hours and tf.minutes < Timeframe.D1.minutes
        # `get_history` returns the full result — which tier answered, which
        # provider, whether the window is older than anything can serve, the
        # validation report and the diagnostics trace id — so the frontend can
        # be EXPLICIT about its state instead of inferring one from an empty
        # array. Feature-detected so tests injecting a bare fake provider (and
        # any legacy adapter) keep working on the older two-method contract.
        get_history = getattr(self.orch.provider, "get_history", None)
        meta: dict = {}
        if get_history is not None:
            result = (get_history(symbol, tf, start, end, extended_hours=True)
                      if ext else get_history(symbol, tf, start, end))
            df, stale = result.frame, result.stale
            meta = result.as_meta()
        else:
            stale_ok = getattr(self.orch.provider, "get_candles_stale_ok", None)
            # Only thread the kwarg when actually requesting extended hours, so
            # plain 4-arg providers are unaffected.
            if stale_ok is not None:
                df, stale = (stale_ok(symbol, tf, start, end, extended_hours=True)
                             if ext else stale_ok(symbol, tf, start, end))
            else:
                df = (self.orch.provider.get_candles(symbol, tf, start, end,
                                                     extended_hours=True)
                      if ext else self.orch.provider.get_candles(symbol, tf, start, end))
                stale = False
        # One sanitization choke point for everything derived below: candles
        # AND indicator series. Providers validate their own output, but this
        # endpoint must stay robust to any that don't — a single non-finite
        # bar otherwise poisons computed indicators (inf VWAP from one inf
        # high) and 500s the response during JSON serialization.
        df = validate_candles(df, context=f"/api/candles {symbol} {timeframe}")
        # Whether the US market is open right now decides how the frontend reads
        # a stale (disk-fallback) payload: while the market is CLOSED the newest
        # cached bar already IS the freshest bar the market ever produced, so a
        # "live data unavailable" banner would be a category error — the chart
        # is simply showing the last session. Only an OPEN-market stale payload
        # means the display has genuinely fallen behind live prices.
        market_open = self.orch.market_open(utcnow())
        if df.empty:
            # An empty payload is NOT one condition. `meta["outcome"]` says
            # which of them it is — `exhausted` (the window predates every
            # provider: the true start of history, and the frontend must stop
            # asking), `empty` (a holiday or pre-listing window: legitimate),
            # or `failed` (nothing could answer: the only case that deserves an
            # error state). Conflating the three is what made a scroll into old
            # intraday history retry forever.
            return {"symbol": symbol, "timeframe": timeframe, "candles": [],
                    "indicators": {}, "stale": False, "market_open": market_open,
                    **meta}

        import math
        icfg = self.cfg.indicators
        close = df["close"]
        series: dict[str, list] = {}

        def col(name: str, s) -> None:
            series[name] = [round(float(v), 4) if math.isfinite(v) else None
                            for v in s]

        if icfg.ema:
            for period in icfg.ema_periods[:3]:
                col(f"ema{period}", ind.ema(close, period))
        if icfg.vwap and tf.minutes < Timeframe.D1.minutes:
            col("vwap", ind.vwap(df))
        if icfg.bollinger:
            bb = ind.bollinger(close)
            col("bb_upper", bb["bb_upper"])
            col("bb_lower", bb["bb_lower"])
            col("bb_mid", bb["bb_mid"])
        if icfg.rsi:
            col("rsi", ind.rsi(close, icfg.rsi_period))
        if icfg.macd:
            m = ind.macd(close)
            col("macd", m["macd"])
            col("macd_signal", m["macd_signal"])
            col("macd_hist", m["macd_hist"])

        times = [int(ts.timestamp()) for ts in df.index]
        # Per-bar session labels only when extended hours are shown (in RTH-only
        # mode every bar is regular, so the field is omitted to keep the payload
        # lean; the frontend defaults a missing session to "rth").
        sess = sessions.labels(df.index) if ext else None
        # validate_candles() above already dropped non-finite bars; these
        # guards are the last line of defense — one rogue float would 500 the
        # whole endpoint during JSON serialization (allow_nan=False).
        candles = []
        for i, (t, r) in enumerate(zip(times, df.itertuples(index=False))):
            if not all(math.isfinite(v) for v in (r.open, r.high, r.low, r.close)):
                continue
            bar = {"time": t, "open": round(r.open, 4), "high": round(r.high, 4),
                   "low": round(r.low, 4), "close": round(r.close, 4),
                   "volume": int(r.volume) if math.isfinite(r.volume) else 0}
            if sess is not None:
                bar["session"] = sess[i]
            candles.append(bar)
        log.debug("candles %s %s: %d bars%s%s", symbol, timeframe, len(candles),
                  " (stale)" if stale else "", " ext" if ext else "")
        return {"symbol": symbol, "timeframe": timeframe,
                "candles": candles, "indicators": series, "stale": stale,
                "as_of": times[-1] if stale else None,
                "market_open": market_open, "extended_hours": ext, **meta}

    def marketdata_diagnostics(self, traces: int = 25) -> dict:
        """Provider health + cache stats + recent request traces.

        Provider-only, like `candles_payload` — no orchestrator state, so no
        lock is taken and asking for diagnostics can never contend with (or be
        blocked by) a running scan. Returns `{"available": False}` rather than
        erroring when the injected provider predates this architecture, so the
        endpoint is safe to call against any build."""
        health = getattr(self.orch.provider, "health", None)
        diagnostics = getattr(self.orch.provider, "diagnostics", None)
        if health is None or diagnostics is None:
            return {"available": False,
                    "reason": "this provider does not expose diagnostics"}
        payload = health()
        payload["available"] = True
        payload["traces"] = diagnostics.recent(max(0, min(traces, 200)))
        payload["version"] = __version__
        return payload

    def marketdata_report(self, traces: int = 25) -> str:
        """The same diagnostics rendered as plain text for a bug report.

        Rendering happens in `data/report.py` over the *same* payload the JSON
        export and the dashboard use, so the three can never disagree about a
        number."""
        return mdreport.render(self.marketdata_diagnostics(traces),
                               traces=traces,
                               title=f"OptionsPilot v{__version__} — market "
                                     f"data diagnostics")

    def marketdata_replay(self, trace_id: int) -> dict:
        """Re-run a recorded request and poll every provider directly.

        This spends real upstream requests, so it is a POST triggered by an
        explicit click on the diagnostics page — never anything the chart or a
        background timer can reach.
        """
        service = getattr(self.orch.provider, "service", None)
        diagnostics = getattr(self.orch.provider, "diagnostics", None)
        if service is None or diagnostics is None:
            return {"error": "this provider does not support replay"}
        trace = diagnostics.find(trace_id)
        if trace is None:
            return {"error": f"no trace {trace_id} in the ring "
                             f"(it holds the most recent requests only)"}
        return mdreplay.replay(service, trace).as_dict()

    # ── market data control centre (Settings ▸ Market Data) ──────────────────
    #
    # Every method here delegates to `data/control.py`. That is the whole
    # design: the UI layer decides HTTP status codes and nothing else, so the
    # control-centre logic is testable without a web server and cannot acquire
    # a second implementation inside a route handler.
    #
    # None of these take `self.lock`. They touch the provider stack, which is
    # thread-safe and independent of the orchestrator's mutable state — the
    # same reasoning as `candles_payload` and `marketdata_diagnostics`. Taking
    # the lock would mean a running scan could block the settings page, which
    # is precisely when a user is most likely to be looking at it.

    @property
    def marketdata(self):
        """The control plane, or None when the provider is not the real stack.

        A test (or an embedding) that injects a `MarketDataProvider` double has
        no registry to administer. Returning None rather than raising lets the
        endpoints answer "not available for this provider" the same way the
        diagnostics endpoint already does, instead of 500ing.
        """
        return getattr(self.orch, "marketdata", None)

    def _no_control(self) -> dict:
        return {"available": False,
                "reason": "this provider does not expose market-data controls"}

    def marketdata_dashboard(self) -> dict:
        control = self.marketdata
        return control.dashboard() if control else self._no_control()

    def marketdata_set_key(self, name: str, api_key: str) -> dict:
        control = self.marketdata
        return (control.set_api_key(name, api_key) if control
                else self._no_control())

    def marketdata_remove_key(self, name: str) -> dict:
        control = self.marketdata
        return control.remove_api_key(name) if control else self._no_control()

    def marketdata_set_enabled(self, name: str, enabled: bool) -> dict:
        control = self.marketdata
        return (control.set_enabled(name, enabled) if control
                else self._no_control())

    def marketdata_move(self, name: str, direction: str) -> dict:
        control = self.marketdata
        return control.move(name, direction) if control else self._no_control()

    def marketdata_set_order(self, order: list[str]) -> dict:
        control = self.marketdata
        return control.set_order(order) if control else self._no_control()

    def marketdata_reset_order(self) -> dict:
        control = self.marketdata
        return control.reset_order() if control else self._no_control()

    def marketdata_set_ordering_mode(self, mode: str) -> dict:
        control = self.marketdata
        return (control.set_ordering_mode(mode) if control
                else self._no_control())

    def marketdata_test(self, name: str) -> dict:
        control = self.marketdata
        return control.test_connection(name) if control else self._no_control()

    def marketdata_maintenance(self, action: str) -> dict:
        control = self.marketdata
        return control.start_maintenance(action) if control else self._no_control()

    def marketdata_maintenance_status(self) -> dict:
        control = self.marketdata
        return control.maintenance_status() if control else self._no_control()

    def marketdata_maintenance_cancel(self) -> dict:
        control = self.marketdata
        return control.cancel_maintenance() if control else self._no_control()

    # ── manual trading (Human Mode order flow) ───────────────────────────────

    def chain_payload(self, symbol: str, expiration: str = "") -> dict:

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
            if side == "buy_to_open" and kind is OrderKind.MARKET:
                # immediate fills never reach OrderManager.evaluate()'s
                # fill-time risk callback — preflight them here so manual
                # entries can't bypass the circuit breaker / entry limits
                decision = self.orch.approve_manual_entry(
                    contract, quantity, utcnow(), premium=contract.ask)
                if not decision.approved:
                    raise BrokerError(decision.veto)
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
                # track immediately so fast round trips still get coached,
                # and count the entry against the daily trade limit
                self.orch.register_manual_entry(contract.symbol,
                                                entry_ts=utcnow())
        return {"order": order.to_dict(),
                "event": event["event"] if event else "working"}

    def account_metrics(self) -> dict:
        return self.services.portfolio.performance(
            utcnow().astimezone(ET)).to_dict()

    # ── watchlist management ─────────────────────────────────────────────────

    def watchlist_add(self, text: str) -> dict:
        return self.services.watchlist.add(text).to_dict()

    def watchlist_remove(self, symbol: str) -> dict:
        return self.services.watchlist.remove(symbol)

    def watchlist_reorder(self, symbols: list[str]) -> dict:
        return self.services.watchlist.reorder(symbols)

    def watchlist_payload(self) -> dict:
        with self.lock:
            return self.services.watchlist.view(
                quotes=self.last_summary.get("quotes", {}),
                signals=self.last_summary.get("signals", {}),
                meta=self._symbol_meta,
            ).to_dict()

    # ── workspace (V0.7.0) ───────────────────────────────────────────────────

    def workspace_payload(self) -> dict:
        with self.lock:
            return self.services.workspace.get().to_dict()

    def update_workspace(self, patch: dict | None) -> dict:
        with self.lock:
            return self.services.workspace.update(patch).to_dict()

    def reset_workspace(self) -> dict:
        with self.lock:
            return self.services.workspace.reset().to_dict()

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

    def _queue_meta_refresh(self, symbols: list[str]) -> None:
        """Queue unresolved metadata for the lifecycle-owned runtime."""
        with self.lock:
            self._meta_pending.update(
                symbol for symbol in symbols if symbol not in self._symbol_meta)
        self.background.trigger("symbol_metadata")

    def _refresh_pending_meta(self) -> None:
        """Resolve one queued metadata batch under runtime ownership."""
        with self.lock:
            symbols = sorted(self._meta_pending)
            self._meta_pending.clear()
        if not symbols:
            return
        get_cap = getattr(self.orch.provider, "get_market_cap", None)
        resolved: dict[str, dict] = {}
        for symbol in symbols:
            try:
                cap = get_cap(symbol) if get_cap else None
            except Exception:  # provider metadata is optional enrichment
                cap = None
            resolved[symbol] = {
                "name": symdir.company_name(symbol),
                "market_cap": cap,
            }
        with self.lock:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            self._meta_path.write_text(
                json.dumps({**self._symbol_meta, **resolved}, indent=1),
                encoding="utf-8")
            self._symbol_meta.update(resolved)

    def _all_trades(self) -> list:
        """Journal rows cached by revision — the status payload is pushed every
        2s per client and must not rescan SQLite when nothing changed. Call
        under self.lock."""
        journal = self.orch.journal
        cache = self._journal_cache
        if cache is None or cache[0] != journal.revision:
            cache = (journal.revision, journal.all())
            self._journal_cache = cache
        return cache[1]

    # ── guided onboarding (V0.6.1) ───────────────────────────────────────────

    def _guide_facts(self) -> guide.GuideFacts:
        """Measure how the app has been used. Call under self.lock.

        Everything here is read from state that already exists — the journal,
        the order book, the broker, the watchlist. Nothing is recorded specially
        for the guide, and nothing here touches the network.
        """
        trades = self._all_trades()
        orders = list(self.orch.orders.history(200)) + \
            [o.to_dict() for o in self.orch.orders.working()]
        kinds = {str(o.get("kind")) for o in orders if o.get("kind")}
        try:
            reviews = len(self.orch.coach.load_all())
        except Exception:  # noqa: BLE001 — a missing/corrupt review dir must
            reviews = 0    # not break onboarding; it is only a count here
        return guide.GuideFacts(
            closed_trades=len(trades),
            manual_trades=sum(1 for t in trades if t.strategy == "manual"),
            coach_reviews=reviews,
            open_positions=len(self.orch.broker.get_positions()),
            orders_placed=len(orders),
            order_kinds_used=frozenset(kinds),
            watchlist_size=len(self.cfg.data.watchlist),
            single_data_source=self._single_data_source(),
        )

    def _single_data_source(self) -> bool | None:
        """Is exactly one INDEPENDENT market-data source usable right now?

        None when it cannot be determined — an injected provider double has no
        chain to inspect, and answering False there would be a claim the data
        does not support (and would silently suppress the recommendation that
        matters most on a keyless install).
        """
        control = self.marketdata
        if control is None:
            return None
        try:
            failover = control.dashboard().get("failover") or {}
        except Exception:  # noqa: BLE001 — diagnostics must never break a page
            return None
        spof = failover.get("single_point_of_failure")
        return bool(spof) if isinstance(spof, bool) else None

    def guide_payload(self) -> dict:
        with self.lock:
            state = self.runtime.guide_state()
            facts = self._guide_facts()
        return guide.payload(state, facts)

    def update_guide(self, patch: dict | None) -> dict:
        """Merge a client patch into the persisted guide state.

        Returns the same shape as `guide_payload` so a client never has to make
        a second request to find out what it should offer next.
        """
        with self.lock:
            merged = guide.merge_state(self.runtime.guide_state(), patch or {})
            self.runtime.set_guide_state(merged)
            facts = self._guide_facts()
        return guide.payload(merged, facts)

    def _setup_history(self) -> dict:
        return self.services.portfolio.setup_history()

    def _pnl_windows(self, now_et: datetime) -> dict:
        return self.services.portfolio.pnl_windows(now_et).to_dict()

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
            report.save_json(self._reports_dir / f"{symbol.lower()}.json")
            report.save_html(self._reports_dir / f"{symbol.lower()}.html")
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
               data_dir: str | Path | None = None) -> FastAPI:
    server = UIServer(config, orchestrator, runtime, data_dir)
    app = FastAPI(title="OptionsPilot", version=__version__)
    app.state.server = server
    @app.on_event("shutdown")
    def _shutdown():
        server.close()
    if run_loop:
        server.start_loop()
        # Quietly check for updates in the background on launch (respecting the
        # user's auto-check + frequency preferences). Gated on run_loop so the
        # test suite — which builds the app with run_loop=False — never touches
        # the network. Failures are swallowed inside the service; startup is
        # never blocked or slowed by this call.
        try:
            server.updater.maybe_check_on_launch()
        except Exception:  # noqa: BLE001 - a failed update check must never break launch
            log.debug("launch-time update check could not start", exc_info=True)

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/static/lightweight-charts.js")
    def chart_lib():
        # the one bundled JS asset (Apache-2.0, vendored — no CDN, offline-safe)
        return FileResponse(STATIC_DIR / "lightweight-charts.js",
                            media_type="application/javascript")

    @app.get("/favicon.ico")
    def favicon():
        # copied from assets/optionspilot.ico (generated by scripts/make_icon.py)
        # into ui/static so it's bundled the same way in a dev checkout, a
        # pip-installed wheel, and the PyInstaller exe — assets/ itself isn't
        # packaged, ui/static/* already is (see pyproject.toml package-data)
        return FileResponse(STATIC_DIR / "favicon.ico",
                            media_type="image/x-icon")

    @app.get("/api/candles")
    def candles_view(
        symbol: str,
        tf: str = "5m",
        start: datetime | None = None,
        end: datetime | None = None,
        ext: bool = False,
    ):
        try:
            return server.candles_payload(symbol, tf, start=start, end=end,
                                          extended_hours=ext)
        except Exception as exc:  # noqa: BLE001 — surface as a clean 502
            log.error("candles fetch failed: %s", exc)
            return JSONResponse({"error": f"candles unavailable: {exc}"},
                                status_code=502)

    @app.get("/api/diagnostics/marketdata")
    def marketdata_diagnostics(traces: int = 25):
        """Everything needed to diagnose a chart complaint without reproducing
        it: per-provider health and circuit-breaker state, cache size and
        schema, aggregate request outcomes, and the most recent request traces
        (which providers were tried, why each was skipped or failed, which tier
        answered, and what validation found)."""
        return server.marketdata_diagnostics(traces)

    @app.get("/api/diagnostics/marketdata/export")
    def marketdata_export(format: str = "json", traces: int = 50):
        """The diagnostics payload as a downloadable attachment.

        `format=text` renders the human-readable report a user can paste into
        an issue; `format=json` is the same data verbatim for tooling. Both are
        served as attachments with a dated filename so a bug report arrives
        with something identifiable rather than `download (3)`.
        """
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        if format == "text":
            return PlainTextResponse(
                server.marketdata_report(traces),
                headers={"Content-Disposition": "attachment; filename="
                         f"optionspilot-diagnostics-{stamp}.txt"})
        return JSONResponse(
            server.marketdata_diagnostics(traces),
            headers={"Content-Disposition": "attachment; filename="
                     f"optionspilot-diagnostics-{stamp}.json"})

    @app.post("/api/diagnostics/marketdata/replay")
    def marketdata_replay(payload: dict | None = None):
        """Re-run one recorded request and compare every provider's answer.

        POST because it is not free: it spends one upstream request per
        provider, deliberately bypassing the memo and the cache so it measures
        the real chain rather than the answer being investigated.
        """
        trace_id = int((payload or {}).get("trace_id", 0))
        if trace_id <= 0:
            return JSONResponse({"error": "trace_id is required"},
                                status_code=400)
        try:
            result = server.marketdata_replay(trace_id)
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not 500
            log.error("marketdata replay failed: %s", exc)
            return JSONResponse({"error": f"replay failed: {exc}"},
                                status_code=502)
        if "error" in result:
            return JSONResponse(result, status_code=404)
        return result

    # ── market data control centre ───────────────────────────────────────────
    #
    # Read is a GET and free; everything that CHANGES something or spends an
    # upstream request is a POST. That split is not ceremony here: the
    # dashboard is polled every few seconds while the settings tab is open, and
    # a GET that could spend a metered request would turn an idle settings page
    # into the thing that exhausts a 25-per-day key.

    def _md(result: dict, *, missing: int = 400):
        """One place that turns a control-plane dict into an HTTP response.

        The control plane reports failures as `{"error": ...}` rather than by
        raising, because most of them are user-input problems ("no such
        provider", "that mode does not exist") and an exception-driven API
        would make the caller's happy path the exceptional one. This maps them
        to status codes so the frontend can still tell success from failure
        without parsing prose.
        """
        if isinstance(result, dict) and result.get("available") is False:
            return JSONResponse(result, status_code=501)
        if isinstance(result, dict) and "error" in result:
            return JSONResponse(result, status_code=missing)
        return result

    @app.get("/api/marketdata")
    def marketdata_dashboard():
        """The whole control centre in one payload: every provider's health,
        credentials (masked), quota, capabilities and position in the order,
        plus the failover picture, live recommendations and maintenance state.

        Safe to poll — every number is a counter that already exists, and
        nothing here touches the network."""
        return server.marketdata_dashboard()

    @app.post("/api/marketdata/providers/{name}/key")
    def marketdata_set_key(name: str, payload: dict | None = None):
        """Store an API key and apply it to the live provider immediately.

        The key is written to `credentials.json` (owner-only, never exported)
        and never echoed back — the response carries a mask. An environment
        variable still takes precedence, and the response says so explicitly
        when one is shadowing what was just saved.
        """
        return _md(server.marketdata_set_key(
            name, str((payload or {}).get("api_key", ""))))

    @app.delete("/api/marketdata/providers/{name}/key")
    def marketdata_remove_key(name: str):
        return _md(server.marketdata_remove_key(name))

    @app.post("/api/marketdata/providers/{name}/enabled")
    def marketdata_set_enabled(name: str, payload: dict | None = None):
        """Switch a provider on or off without a restart."""
        return _md(server.marketdata_set_enabled(
            name, bool((payload or {}).get("enabled", True))))

    @app.post("/api/marketdata/providers/{name}/test")
    def marketdata_test(name: str):
        """Run a real request against one provider and report what happened.

        POST because it spends an upstream request — including a metered one.
        End to end on purpose: transport, authentication, parsing and semantic
        validation, so a provider whose response format has changed fails the
        test rather than passing it."""
        return _md(server.marketdata_test(name))

    @app.post("/api/marketdata/providers/{name}/move")
    def marketdata_move(name: str, payload: dict | None = None):
        direction = str((payload or {}).get("direction", "up")).lower()
        if direction not in ("up", "down"):
            return JSONResponse({"error": "direction must be 'up' or 'down'"},
                                status_code=400)
        return _md(server.marketdata_move(name, direction))

    @app.post("/api/marketdata/order")
    def marketdata_set_order(payload: dict | None = None):
        order = (payload or {}).get("order")
        if not isinstance(order, list):
            return JSONResponse({"error": "order must be a list of provider "
                                          "names, best first"}, status_code=400)
        return _md(server.marketdata_set_order([str(n) for n in order]))

    @app.post("/api/marketdata/order/reset")
    def marketdata_reset_order():
        return _md(server.marketdata_reset_order())

    @app.post("/api/marketdata/ordering_mode")
    def marketdata_ordering_mode(payload: dict | None = None):
        return _md(server.marketdata_set_ordering_mode(
            str((payload or {}).get("mode", ""))))

    @app.post("/api/marketdata/maintenance")
    def marketdata_maintenance(payload: dict | None = None):
        """Start one maintenance action on a background thread.

        Returns immediately with the job's initial state; progress is polled
        from the GET below. Several of these take tens of seconds and one takes
        minutes, so a synchronous endpoint would hold a request open past any
        sensible client timeout with no way to distinguish slow from dead."""
        return _md(server.marketdata_maintenance(
            str((payload or {}).get("action", ""))), missing=400)

    @app.get("/api/marketdata/maintenance")
    def marketdata_maintenance_status():
        return server.marketdata_maintenance_status()

    @app.delete("/api/marketdata/maintenance")
    def marketdata_maintenance_cancel():
        """Ask the running action to stop at its next checkpoint.

        Cooperative rather than forcible: the actions long enough to want
        cancelling are the ones spending upstream requests, and abandoning one
        mid-flight would leave a provider's counters inconsistent with what it
        actually served."""
        return _md(server.marketdata_maintenance_cancel())

    # ── developer QA (gated) ─────────────────────────────────────────────────
    #
    # `market_data.qa_mode` is false in every shipped build, and these return
    # 404 — not 403 — while it is. A 403 confirms the endpoint exists, which is
    # a small thing to hand an unattended local HTTP server; 404 says only that
    # this build has no such route, which is functionally true.

    def _qa_gate():
        control = server.marketdata
        if control is None or not control.config.qa_mode:
            return JSONResponse({"error": "not found"}, status_code=404)
        return None

    @app.get("/api/marketdata/qa")
    def marketdata_qa_state():
        return _qa_gate() or server.marketdata.qa_state()

    @app.post("/api/marketdata/qa/fault")
    def marketdata_qa_fault(payload: dict | None = None):
        """Arm a simulated failure against one provider.

        The fault fires inside `HistoryAdapter.fetch_history`, in the exact
        place a real transport failure occurs, so the health monitor, the
        breaker, the ranking, the tier ladder and the frontend state machine
        all behave identically to the genuine article."""
        blocked = _qa_gate()
        if blocked:
            return blocked
        data = payload or {}
        count = data.get("count")
        return _md(server.marketdata.qa_arm(
            str(data.get("provider", "")), str(data.get("kind", "")),
            count=int(count) if count else None,
            seconds=float(data.get("seconds", 2.0))))

    @app.delete("/api/marketdata/qa/fault")
    def marketdata_qa_clear(provider: str | None = None):
        return _qa_gate() or _md(server.marketdata.qa_clear(provider))

    @app.post("/api/marketdata/qa/breaker")
    def marketdata_qa_breaker(payload: dict | None = None):
        blocked = _qa_gate()
        if blocked:
            return blocked
        data = payload or {}
        return _md(server.marketdata.qa_trip_breaker(
            str(data.get("provider", "")), float(data.get("seconds", 30.0))))

    @app.post("/api/marketdata/qa/reset")
    def marketdata_qa_reset():
        return _qa_gate() or _md(server.marketdata.qa_reset_health())

    @app.post("/api/marketdata/qa/corrupt_cache")
    def marketdata_qa_corrupt_cache():
        """Prove the cache-corruption recovery path on a COPY of the real
        cache file. The user's own cache is never touched."""
        return _qa_gate() or _md(server.marketdata.qa_corrupt_cache())

    @app.get("/api/status")
    def status():
        return server.status_payload()

    @app.post("/api/scan")
    def scan(payload: dict | None = None):
        # Default: non-blocking — kick off a background cycle and let the UI
        # follow progress via /ws `scan` state. `{"wait": true}` runs the
        # cycle synchronously (tests, scripts, curl).
        if payload and payload.get("wait"):
            return server.run_cycle_now()
        return server.request_scan()

    @app.get("/api/journal")
    def journal(last: int = 50):
        with server.lock:
            trades = server._all_trades()[-last:]
            stats = server.orch.journal.stats()
            # Which intelligence findings each row is evidence for. Built from
            # the snapshot the rest of the app already has, so the journal list
            # can flag "this trade contributed to 2 findings" without a second
            # analysis or a per-row request.
            findings = build_evidence_index(server.orch.intelligence_snapshot())
        return {
            "stats": stats,
            "findings": {tid: labels for tid, labels in findings.items()
                         if any(t.id == tid for t in trades)},
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

        with server.lock:
            engine = LearningEngine(server.orch.journal)
            # The SAME file the orchestrator loads its learned weights from.
            # This read was `Path("data") / ...` — CWD-relative, one of the
            # hardcodes V0.4.4's storage split was meant to eliminate. On any
            # real install the CWD is not the storage root, so the file simply
            # did not exist and the Learning tab reported "no learned weights"
            # however much the engine had learned. `effective` was right (it is
            # read from the live scorer), which is what made it look plausible.
            store = WeightStore(server._data_dir / "learning" / "weights.json")

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

        try:
            return server.place_order(payload)
        except (ValueError, KeyError, TypeError, BrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/orders/cancel")
    def orders_cancel(payload: dict):

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
        with server.lock:
            reviews = server.orch.coach.load_all()
        # The dashboard is a pure function of the reviews; recompute only when
        # their count changes (a new trade was reviewed), mirroring the
        # journal-cache pattern. Reviews are write-once per trade, so count is a
        # sufficient cache key.
        dashboard = server._coach_dashboard(reviews)
        reviews.sort(key=lambda r: r.get("trade_id", ""), reverse=True)
        # V0.6.0: the Coach also serves the intelligence layer's view of the
        # same trader. The two are deliberately kept side by side rather than
        # merged — `dashboard` is the per-review scorecard aggregation the coach
        # has always produced, `intelligence` is the cross-trade analysis, and
        # they answer different questions over different windows. The Coach tab
        # renders both and labels which is which.
        with server.lock:
            snapshot = server.orch.intelligence_snapshot()
        return {
            "profile": CoachProfile(reviews).build(),
            "dashboard": dashboard,
            "reviews": reviews[:50],
            "intelligence": _intelligence_payload(snapshot, full=False),
        }

    # ── guided onboarding (V0.6.1) ───────────────────────────────────────
    # The tutorials live in index.html; these two routes own the part the
    # frontend cannot: durable progress, and which tutorial to offer next
    # based on what the user has actually used.

    @app.get("/api/guide")
    def guide_view():
        return server.guide_payload()

    @app.post("/api/guide/state")
    def guide_update(payload: dict | None = None):
        """Merge a patch and return the full state plus fresh recommendations.

        Deliberately forgiving: an unknown tutorial id, an unusable feature key
        or a garbage body is ignored rather than rejected. This endpoint records
        that someone finished a tour — failing it would be a 4xx in the middle
        of a celebration, and nothing downstream depends on the write.
        """
        return server.update_guide(payload)

    # ── workspace (V0.7.0) ───────────────────────────────────────────────
    # Where the user was looking, owned by the server rather than by one
    # browser's localStorage. See services/workspace.py for why.

    @app.get("/api/workspace")
    def workspace_view():
        return server.workspace_payload()

    @app.post("/api/workspace")
    def workspace_update(payload: dict | None = None):
        """Merge a PARTIAL patch. Deliberately forgiving, and deliberately
        partial: a client that only knows about `symbol` must be able to say so
        without overwriting panel layout it has never heard of. An unusable
        value is replaced by its default rather than rejected — this records
        where someone was looking, and 4xx-ing it would interrupt a chart."""
        return server.update_workspace(payload)

    @app.delete("/api/workspace")
    def workspace_reset():
        return server.reset_workspace()

    # ── platform readiness (V0.7.0) ──────────────────────────────────────

    @app.get("/api/host")
    def host_view():
        """What this build's host can do.

        A client reads this to decide which surfaces to offer at all rather
        than to guess from a user-agent string. Contains no user data and no
        secret, so it is safe in a public bug report."""
        return server.services.host_view().to_dict()

    @app.get("/api/diagnostics/sync")
    def sync_boundaries_view():
        """The classified inventory of every durable object the app owns.

        Nothing here syncs anything — V0.7.0 builds no cloud sync. This is the
        classification that has to exist before one could, exposed because the
        list is only useful if it is visible: `never_sync` in particular is the
        answer to "what must not leave this machine", and it should be readable
        without grepping the source."""
        return sync_boundaries.report()

    # ── Trading Intelligence Engine ──────────────────────────────────────
    # Every route below projects the SAME snapshot. None of them analyses
    # anything itself; the engine caches on a fingerprint the orchestrator
    # owns, so a page that polls four of these costs one analysis at most.

    @app.get("/api/intelligence")
    def intelligence_view(full: bool = True):
        """The complete analysis — metrics, behaviours, patterns, scores,
        goals, recommendations, lessons, timeline, achievements, reports."""
        with server.lock:
            snapshot = server.orch.intelligence_snapshot()
        return {
            **_intelligence_payload(snapshot, full=full),
            "duration_ms": round(server.orch.intelligence.last_duration_ms, 1),
        }

    @app.get("/api/intelligence/summary")
    def intelligence_summary():
        """The dashboard projection: headline metrics, scores, top actions,
        goals, achievements and the latest report summary."""
        with server.lock:
            snapshot = server.orch.intelligence_snapshot()
        return _intelligence_summary(snapshot)

    @app.get("/api/intelligence/trade/{trade_id}")
    def intelligence_trade(trade_id: str):
        """Everything the analysis already knows about ONE trade — the patterns
        it belongs to, the habits it is evidence for, where its result sits in
        the trader's distribution, and how comparable trades performed."""
        with server.lock:
            return server.orch.intelligence.trade_insight(trade_id)

    @app.get("/api/intelligence/reports")
    def intelligence_reports():
        with server.lock:
            snapshot = server.orch.intelligence_snapshot()
        return {"reports": [r.to_dict() for r in snapshot.reports]}

    @app.get("/api/intelligence/goals")
    def intelligence_goals():
        """Active goals with computed progress, plus the suggested templates and
        the metric vocabulary a custom goal may target."""
        with server.lock:
            snapshot = server.orch.intelligence_snapshot()
            active = {g.id for g in server.orch.intelligence.list_goals()}
        return {
            "goals": [g.to_dict() for g in snapshot.goals],
            "templates": [{**t.to_dict(), "added": t.id in active}
                          for t in GOAL_TEMPLATES],
            "metrics": [{"key": key, "label": label, "unit": unit,
                         "higher_is_better": higher}
                        for key, (label, unit, higher, _) in METRIC_SPECS.items()],
        }

    @app.post("/api/intelligence/goals")
    def intelligence_goal_add(payload: dict):
        try:
            goal = Goal.from_dict(payload)
            if goal is None:
                raise ValueError("a goal needs an id, a metric and a numeric target")
            with server.lock:
                server.orch.intelligence.add_goal(goal)
                snapshot = server.orch.intelligence_snapshot(force=True)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"goals": [g.to_dict() for g in snapshot.goals]}

    @app.delete("/api/intelligence/goals/{goal_id}")
    def intelligence_goal_remove(goal_id: str):
        with server.lock:
            removed = server.orch.intelligence.remove_goal(goal_id)
            snapshot = server.orch.intelligence_snapshot(force=True)
        if not removed:
            return JSONResponse({"error": f"no goal {goal_id!r}"}, status_code=404)
        return {"goals": [g.to_dict() for g in snapshot.goals]}

    @app.get("/api/experience")
    def experience_view():
        """Experience Engine statistics + recent trades (advisory memory)."""
        with server.lock:
            exp = server.orch.experience
            return {
                "statistics": exp.statistics(),
                "recent": [_experience_dict(r) for r in exp.recent(30)],
            }

    @app.get("/api/experience/similar")
    def experience_similar(symbol: str, k: int = 20):
        """Similar historical trades for a symbol's current setup (advisory —
        never places or changes a trade)."""
        with server.lock:
            try:
                return server.orch.experience_for_symbol(
                    symbol.upper(), k=max(1, min(int(k), 100)))
            except Exception as exc:  # noqa: BLE001
                log.error("experience/similar failed for %s: %s", symbol, exc)
                return JSONResponse(
                    {"error": f"could not evaluate {symbol}"}, status_code=502)

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

    # ── auto-updater ─────────────────────────────────────────────────────────
    @app.get("/api/update/status")
    def update_status():
        """Current updater state (version, availability, prefs, progress).

        Pure read of in-memory state — never triggers a network call, so the
        Settings panel and the dialog can poll it freely.
        """
        return server.updater.snapshot()

    @app.post("/api/update/check")
    def update_check():
        """Manual 'Check for Updates' — runs a synchronous check and returns the
        fresh snapshot. Never raises; a network failure comes back as
        ``error`` in the snapshot with ``update_available=False``."""
        server.updater.check_now()
        return server.updater.snapshot()

    @app.post("/api/update/download")
    def update_download():
        started = server.updater.start_download()
        if not started:
            return JSONResponse(
                {"error": "no update is available to download"}, status_code=409)
        return server.updater.snapshot()

    @app.get("/api/update/progress")
    def update_progress():
        return server.updater.snapshot()["progress"]

    @app.post("/api/update/cancel")
    def update_cancel():
        server.updater.cancel_download()
        return {"cancelled": True}

    @app.post("/api/update/apply")
    def update_apply():
        result = server.updater.apply_update()
        if not result.get("ok"):
            return JSONResponse(result, status_code=422)
        return result

    @app.post("/api/update/skip")
    def update_skip():
        return server.updater.skip_current()

    @app.post("/api/update/settings")
    def update_settings(payload: dict):
        try:
            return server.updater.set_preferences(**(payload or {}))
        except UpdateError as exc:
            return JSONResponse({"error": exc.message}, status_code=422)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        # 1s cadence with change detection: full payload only when something
        # actually changed, otherwise a tiny heartbeat — the frontend skips
        # re-rendering entirely on heartbeats.
        await socket.accept()
        last_digest = ""
        try:
            while True:
                payload = server.status_payload()
                digest = json.dumps(
                    {k: v for k, v in payload.items() if k != "ts"},
                    sort_keys=True, default=str)
                if digest != last_digest:
                    last_digest = digest
                    await socket.send_json(payload)
                else:
                    await socket.send_json({"ts": payload["ts"],
                                            "heartbeat": True})
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    register_v1_routes(app, server)

    return app


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8787,
          run_loop: bool = True,
          runtime: RuntimeSettings | None = None,
          data_dir: str | Path | None = None) -> None:  # pragma: no cover - blocking server
    import uvicorn

    app = create_app(config, run_loop=run_loop, runtime=runtime, data_dir=data_dir)
    print(f"OptionsPilot dashboard: http://{host}:{port}  (paper trading only)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
