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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from optionspilot import __version__
from optionspilot.backtest import Backtester
from optionspilot.broker.base import BrokerError
from optionspilot.coach import CoachProfile, build_dashboard
from optionspilot.config.runtime import RuntimeSettings
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger, uvicorn_logging_kwargs
from optionspilot.core.models import utcnow
from optionspilot.core.paths import AppPaths
from optionspilot.data import replay as mdreplay
from optionspilot.data import symbols as symdir
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
from optionspilot.services.errors import ServiceError
from optionspilot.ui.errors import status_for
from optionspilot.services.runtime import BackgroundRuntime, RuntimeSnapshot, TaskSpec
from optionspilot.services import trading as trading_service
from optionspilot.host import current_host
from optionspilot.services import intelligence as intel_view
from optionspilot.services import sync as sync_boundaries
from optionspilot.services import guide
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
#: Re-exported from the service that owns it (V0.9.2-C4), so the cap is
#: one fact rather than two that drift.
MAX_EQUITY_POINTS = trading_service.MAX_EQUITY_POINTS

#: The runtime task a user's "Scan now" request triggers (V0.9.1-C5). It is
#: `on_demand`, so the interval is never used to schedule it — the value exists
#: only because `TaskSpec` requires a positive one, and it is deliberately long
#: enough that reading it as a schedule would be obviously wrong.
MANUAL_SCAN_TASK = "manual_scan"
MANUAL_SCAN_INTERVAL = 24 * 60 * 60.0

#: The other two on-demand jobs, brought onto the runtime in V0.9.1-C6. Both
#: share `MANUAL_SCAN_INTERVAL`'s nominal-interval reasoning above: they run
#: once per `trigger()` and never on a schedule.
BACKTEST_TASK = "backtest"
INTELLIGENCE_TASK = "intelligence_refresh"

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
        self._close_lock = threading.Lock()
        self._closed = False
        self.background = BackgroundRuntime(health_check=self._health_check)
        self._runtime_health: dict = {"state": "healthy", "last_check": None,
                                      "issues": [], "repairs": []}
        # The scan pipeline, the cycle lock and the scan/summary/equity state
        # moved to `services/trading.py` in V0.9.2-C4. They are still reachable
        # under their old names through the properties below, because the
        # status payload, the WebSocket push and `tests/test_runtime_lifecycle`
        # all read them.
        #
        # Registered at construction, not in `start_loop`: a manual scan must
        # work on a server started with `run_loop=False`, where no scheduled
        # task is ever registered. The interval is nominal — the task only ever
        # runs when `trigger()` makes it due, which is what a user's request
        # does. Worker lane for the same reason `market_monitor` is: a cycle
        # fetches the whole watchlist over the network and must not sit on the
        # coordinator thread.
        self.background.register(TaskSpec(
            MANUAL_SCAN_TASK, MANUAL_SCAN_INTERVAL, self._background_scan,
            policy="essential", lane="worker", on_demand=True,
            restartable=False))
        # V0.9.1-C6, and registered here for the same reason the manual scan
        # is: `POST /api/backtest` is served whether or not `run_loop` is set,
        # so the task has to exist on a server that never starts a schedule.
        #
        # `policy="essential"` — a user who starts a backtest and then hides
        # the window to the tray asked for a report, and the reduced hidden
        # profile runs `essential` and `monitoring` only. `restartable=False`
        # because `_run_backtest` records its own failure in `backtest_job`;
        # a runtime retry would overwrite that with a second attempt the user
        # never asked for and cannot see.
        self.background.register(TaskSpec(
            BACKTEST_TASK, MANUAL_SCAN_INTERVAL, self._background_backtest,
            policy="essential", lane="worker", on_demand=True,
            restartable=False))
        # `policy="normal"` — warming a cache is genuinely deferrable, so a
        # hidden window may skip it; the next read simply computes inline. That
        # is the difference between this and a backtest, which produces a file
        # the user is waiting for.
        self.background.register(TaskSpec(
            INTELLIGENCE_TASK, MANUAL_SCAN_INTERVAL,
            self._background_intelligence, policy="normal", lane="worker",
            on_demand=True, restartable=False))
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
            # The default history window per timeframe. `services/` may not
            # import the orchestrator, and duplicating the table there would be
            # a second owner of one fact, so the composition root hands it down.
            window_days=WINDOW_DAYS,
            # Through the module attribute, not `utcnow` directly: this is a
            # frozen seam the whole UI suite relies on, and handing over the
            # function object would capture the real clock forever.
            clock=lambda: utcnow(),
            # The replay engine, handed down rather than imported: it reaches
            # the provider registry and the adapters, which `services/` must
            # not depend on.
            replay=mdreplay.replay,
            # Read per cycle, not captured: a watchlist edited between scans
            # must take effect on the next one.
            watchlist_symbols=lambda: list(self.cfg.data.watchlist),
            tz=ET,
            # The backtest slot (V0.9.2-C5). The runtime and the task name come
            # from here because REGISTRATION stays here; the `Backtester` is
            # injected rather than imported by `services/`.
            reports_dir=self._reports_dir,
            background=self.background,
            backtest_task=BACKTEST_TASK,
            backtester=Backtester,
        )

    # ── cycle loop ───────────────────────────────────────────────────────────

    def start_loop(self) -> None:
        if self._closed:
            raise RuntimeError("UI server is closed")
        if self.background.snapshot().running:
            return
        prefs = self.runtime.runtime_prefs()
        self.background.set_profile(prefs["hidden_profile"])
        if not prefs["auto_resume_monitoring"]:
            self.background.pause()
        task_names = {task["name"] for task in self.background.snapshot().tasks}
        if "market_monitor" not in task_names:
            # V0.9.1-C3: the worker lane. `_background_cycle` runs a full
            # watchlist fetch plus an option chain per symbol over the network,
            # and executing that inline on the coordinator froze every other
            # task for its duration — `tray_status` has a 10-second interval
            # precisely because the tooltip is meant to stay current, and it
            # was the most visible casualty.
            #
            # This is the one-argument activation C2 was shaped around:
            # deleting `lane="worker"` restores the previous behaviour exactly,
            # with no other code change. Lock scope is unaffected —
            # `run_cycle_now` already takes `_cycle_lock` and then the
            # orchestrator lock only for the stateful part, so the same code
            # runs under the same locks, just not on the coordinator's thread.
            self.background.register(TaskSpec(
                "market_monitor", self.cfg.engine.scan_interval_seconds,
                self._background_cycle, policy="monitoring", lane="worker"))
        if "symbol_metadata" not in task_names:
            self.background.register(TaskSpec(
                "symbol_metadata", 60.0, self._refresh_pending_meta,
                policy="normal"))
        self.background.start()

    def stop_loop(self) -> None:
        # V0.9.1-C8: `self._stop.set()` used to precede this. That Event existed
        # only for the deleted `_loop`; nothing else ever read it, so setting it
        # stopped nothing. Stopping the runtime is the whole of stopping the
        # loop, and now says so.
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
        # V0.9.1-C9: a `memory` block used to be built here from `tracemalloc`,
        # plus one rule — "traced memory grew beyond the health threshold" —
        # comparing current usage against a baseline captured once at
        # `start_loop`. No client ever read the block: not `index.html`, not the
        # API contract check, not the docs. The rule measured growth against the
        # moment the app started, which on a long session is not a statement
        # about now. Tracing every allocation in a pandas/numpy process for that
        # was not a trade worth making; real memory questions want an external
        # profiler against a real workload.
        self._runtime_health = {
            "state": "degraded" if issues else "healthy",
            "last_check": utcnow().isoformat(),
            "issues": issues,
            "repairs": repairs,
        }
        if issues:
            self.orch.notifier.notify(
                "error", "Background health check needs attention", issues[0])

    # V0.9.1-C8: `_loop` used to sit here — a complete second scheduler
    # (`while not self._stop.is_set()`, calling `run_cycle_now` and
    # `_maybe_send_summaries` on its own cadence) kept "for embedders". Nothing
    # ever called it; `_loop_thread` was declared and never assigned, and
    # `self._stop` was set by `stop_loop` and read only inside `_loop`.
    #
    # It is deleted rather than left dormant because two schedulers over one
    # task set run every cycle twice, and `market_monitor` PLACES TRADES —
    # `test_repeated_sessions_do_not_accumulate_schedulers` exists for that
    # reason. A dead loop with a docstring inviting `Thread(target=self._loop)`
    # is one call away from being the second scheduler that test forbids.
    # `BackgroundRuntime` is the only path to a cycle;
    # `TestThereIsOnlyOneSchedulingPath` names every caller of `run_cycle_now`
    # and fails if a new one appears.

    # ── the scan cycle, owned by services/trading.py (V0.9.2-C4) ─────────────
    #
    # The state below is read by `status_payload`, the WebSocket push and
    # `tests/test_runtime_lifecycle`, so it keeps its old names here and
    # forwards. `run_cycle_now` stays a method rather than becoming a bare
    # attribute because the runtime task callbacks call it and a test
    # monkeypatches it.

    @property
    def scan_state(self) -> dict:
        return self.services.trading.scan_state

    @property
    def last_summary(self) -> dict:
        return self.services.trading.last_summary

    @property
    def equity_history(self) -> list[tuple[str, float]]:
        return self.services.trading.equity_history

    @equity_history.setter
    def equity_history(self, value) -> None:
        # Settable because the equity cap is asserted by seeding it directly.
        self.services.trading.equity_history = list(value)

    @property
    def _cycle_lock(self):
        """Serialises whole cycles, so the scheduled scan and a manual one can
        never interleave. A different object from `self.lock`, deliberately."""
        return self.services.trading.cycle_lock

    def run_cycle_now(self, *, blocking: bool = True) -> dict:
        """One full cycle. See `services/trading.py::TradingService.run_cycle`
        for the lock discipline: the candle prefetch runs outside the
        orchestrator lock, the stateful cycle inside it."""
        return self.services.trading.run_cycle(blocking=blocking)

    def request_scan(self) -> dict:
        """Non-blocking manual scan: ask the runtime to run a cycle and return
        immediately. Progress is surfaced in every status payload / WS push as
        `scan`.

        V0.9.1-C5: the runtime owns this, exactly as it owns the scheduled
        scan. What used to be here was a raw thread behind a check-then-act
        test — `if not running and not locked: Thread(...).start()` — which two
        concurrent requests both passed, so the second ran a whole extra cycle
        once `_cycle_lock` released. Checking a slot and then starting
        something that claims it is not claiming it; `MarketDataControl`
        shipped the same shape and admitted 8 of 8 simultaneous requests.

        The runtime's per-task overlap guard is now the only arbiter, and the
        caller decides nothing. That also means a manual scan is finally
        pausable, drainable at shutdown and visible in the runtime snapshot,
        none of which was true of a thread the runtime could not see.
        """
        # The runtime owns scan execution, so it has to be alive to own it.
        # `start()` is idempotent and registers no scheduled work, so a server
        # constructed with `run_loop=False` still scans only when asked.
        self.background.start()
        self.background.trigger(MANUAL_SCAN_TASK)
        return {"state": "started", "scan": self.scan_state}

    def _background_scan(self) -> None:
        try:
            # Non-blocking: a request arriving while a cycle is already running
            # is declined, not queued. Queueing it would run a second full
            # cycle the instant the first returned, which is not what the old
            # `if not _cycle_lock.locked()` check did and not what the user
            # asked for — they asked for a scan, and one is already happening.
            self.run_cycle_now(blocking=False)
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
        """Delegates to `services/charts.py` (V0.9.2-C2).

        Kept as a method because the route, `tests/test_ui_server.py` and the
        diagnostics page all call it; it forwards rather than reimplements, so
        there is still exactly one chart payload in the system.
        """
        return self.services.charts.candles_payload(
            symbol, timeframe, start=start, end=end,
            extended_hours=extended_hours)

    # ── market data console (V0.9.2-C3) ──────────────────────────────────────
    #
    # Every method below delegates to `services/marketdata.py`. They keep their
    # names because the routes, `tests/test_ui_server.py` and the browser suite
    # all call them; the logic — diagnostics, the text report, replay and the
    # twelve control-centre calls — lives in the service.
    #
    # None of these take `self.lock`, and that is a decision rather than an
    # omission. They touch the provider stack, which is thread-safe and
    # independent of the orchestrator's mutable state. Taking the lock would
    # mean a running scan could block the settings page, which is precisely
    # when a user is most likely to be looking at it.

    def marketdata_diagnostics(self, traces: int = 25) -> dict:
        return self.services.marketdata.diagnostics(traces)

    def marketdata_report(self, traces: int = 25) -> str:
        return self.services.marketdata.report(traces)

    def marketdata_replay(self, trace_id: int) -> dict:
        return self.services.marketdata.replay(trace_id)

    @property
    def marketdata(self):
        """The control plane, or None. Reached directly by the QA routes."""
        return self.services.marketdata.control

    def marketdata_dashboard(self) -> dict:
        return self.services.marketdata.dashboard()

    def marketdata_set_key(self, name: str, api_key: str) -> dict:
        return self.services.marketdata.set_key(name, api_key)

    def marketdata_remove_key(self, name: str) -> dict:
        return self.services.marketdata.remove_key(name)

    def marketdata_set_enabled(self, name: str, enabled: bool) -> dict:
        return self.services.marketdata.set_enabled(name, enabled)

    def marketdata_move(self, name: str, direction: str) -> dict:
        return self.services.marketdata.move(name, direction)

    def marketdata_set_order(self, order: list[str]) -> dict:
        return self.services.marketdata.set_order(order)

    def marketdata_reset_order(self) -> dict:
        return self.services.marketdata.reset_order()

    def marketdata_set_ordering_mode(self, mode: str) -> dict:
        return self.services.marketdata.set_ordering_mode(mode)

    def marketdata_test(self, name: str) -> dict:
        return self.services.marketdata.test_connection(name)

    def marketdata_maintenance(self, action: str) -> dict:
        return self.services.marketdata.maintenance(action)

    def marketdata_maintenance_status(self) -> dict:
        return self.services.marketdata.maintenance_status()

    def marketdata_maintenance_cancel(self) -> dict:
        return self.services.marketdata.maintenance_cancel()

    # ── manual trading (Human Mode order flow) ───────────────────────────────

    def chain_payload(self, symbol: str, expiration: str = "") -> dict:
        return self.services.trading.chain_payload(symbol, expiration)

    def place_order(self, payload: dict) -> dict:
        return self.services.trading.place_order(payload)

    def account_metrics(self) -> dict:
        return self.services.trading.account_metrics()

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

    # The slot, the claim and the run moved to `services/backtest.py` in
    # V0.9.2-C5. Task REGISTRATION stays here, where V0.9.1-C6 put it.

    @property
    def _bt_lock(self):
        """The slot lock, still reachable: `GET /api/backtest` reads the job
        under it so a status poll cannot observe a half-written record."""
        return self.services.backtest._lock

    @property
    def backtest_job(self) -> dict:
        return self.services.backtest.job

    @backtest_job.setter
    def backtest_job(self, value: dict) -> None:
        self.services.backtest.job = value

    def start_backtest(self, symbol: str, days: int, min_confidence: float | None
                       ) -> dict:
        return self.services.backtest.start(symbol, days, min_confidence)

    def _background_backtest(self) -> None:
        """The runtime task body.

        It drains the claim from the service and then calls `self._run_backtest`
        rather than the service directly, because that method is an overridable
        seam: `tests/test_runtime_lifecycle.py` replaces it to block a worker
        deterministically, and reaching past it would silently ignore the
        replacement — the "registry that captures a bound method freezes a
        seam" lesson, in the one place it still matters.
        """
        pending = self.services.backtest.take_pending()
        if pending is None:
            return
        self._run_backtest(*pending)

    def refresh_intelligence(self) -> dict:
        """Warm the intelligence cache off the request path, under the runtime.

        V0.9.1-C6: this replaces `IntelligenceEngine.refresh_in_background()`,
        which started its own daemon thread. `intelligence/` imports `core`
        only, so it cannot reach the runtime and therefore cannot own the
        thread's lifecycle — the owner has to be a caller that can, which is
        this layer.
        """
        self.background.start()
        started = self.background.trigger(INTELLIGENCE_TASK)
        return {"state": "started" if started else "unavailable"}

    def _background_intelligence(self) -> None:
        # A failed analysis returns an empty snapshot rather than raising
        # (docs/TRADING_INTELLIGENCE.md), so this cannot fail the task; the
        # guard is here because a worker escape would vanish into the pool.
        try:
            self.services.intelligence.snapshot(force=True)
        except Exception as exc:  # noqa: BLE001 — surfaced via logs/status
            log.exception("intelligence refresh failed: %s", exc)

    def _run_backtest(self, symbol: str, days: int,
                      min_confidence: float | None) -> None:
        """The overridable seam `_background_backtest` calls. Delegates."""
        self.services.backtest.run(symbol, days, min_confidence)


def create_app(config: AppConfig, orchestrator: Orchestrator | None = None,
               run_loop: bool = False,
               runtime: RuntimeSettings | None = None,
               data_dir: str | Path | None = None) -> FastAPI:
    server = UIServer(config, orchestrator, runtime, data_dir)
    app = FastAPI(title="OptionsPilot", version=__version__)
    app.state.server = server

    @app.exception_handler(ServiceError)
    def _service_error(request, exc: ServiceError):
        """Every legacy route's classified failure, mapped in ONE place.

        V0.9.2-C8. Before this, each route decided its own status from its own
        `except` tuple, which is how a client's unparseable timeframe came back
        as a 502. The shape stays `{"error": "<message>"}` because that is what
        `index.html` reads — `/api/v1/*` gets the full envelope. Only the
        status is newly correct.
        """
        return JSONResponse({"error": exc.message},
                            status_code=status_for(exc.code))
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
        except ServiceError:
            # The client's own mistake (an unparseable timeframe). Let the
            # app-wide handler give it a 4xx instead of blaming an upstream —
            # a 502 here sent users to check their internet over a typo.
            raise
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
        except ServiceError:
            raise
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
            # `ServiceError` is no longer listed: the app-wide handler maps it
            # from its code. What remains catches the builtins that can still
            # arrive from layers below the service.
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
    # The packaged exe passes its arguments through to this CLI, so `serve`
    # runs inside the same windowed process the desktop launcher does — where
    # uvicorn's default logging config has no stdout to ask about colours.
    uvicorn.run(app, host=host, port=port, log_level="warning",
                **uvicorn_logging_kwargs())
