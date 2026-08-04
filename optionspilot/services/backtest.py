"""BacktestService — the single backtest slot, and what fills it.

V0.9.2-C5, the last of the four extractions. What moves here is the *slot*: the
atomic claim, the parameter hand-off, the candle windows, the report writing and
the user-visible `job` record. What deliberately does **not** move is task
registration — `ui/server.py` registers `TaskSpec("backtest", ...)` on the
runtime, exactly where V0.9.1-C6 put it, and this service is what that task ends
up calling.

**The claim is one atomic fact.** `_lock` guards the slot test, the parameter
stash and the job record together. `TaskSpec.callback` takes no arguments, so a
parameterised job has to hand its parameters over through state — and a slot
claimed with nobody's parameters behind it would run the previous request's
symbol. Equally, a slot claimed and then abandoned (a trigger that fails) would
wedge every later backtest for the life of the process, so the claim is released
rather than trusted.

**This lock is not the orchestrator's.** A backtest never touches the live
orchestrator: it builds its own config copy, its own components and its own
candle set. That is why `ui/server.py`'s module docstring has always said
backtests "never touch the live orchestrator", and it is why this service owns
its lock outright instead of receiving one, unlike `TradingService`.

**The `Backtester` is injected, not imported**, and the rule is the one C3 set:
a pure, single-purpose helper is imported so a second cannot be substituted
(`data/report.py`), but heavy machinery is injected (`data/replay.py`).
`Backtester` drives engine + risk + broker + journal to simulate trades — the
whole trading stack — and importing it here would mean a client that wants a win
rate pulls a simulator in to get one.

Nothing here raises. A backtest is a user-visible job, and an exception escaping
into the worker pool would leave `job` reading "running" forever with no trace;
the failure is recorded in the job instead.
"""

from __future__ import annotations

import threading
from datetime import timedelta

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe, utcnow

log = get_logger("ui")

#: History fetched per entry/HTF timeframe, keyed by the timeframe's minutes.
#: Bounded by what the providers actually serve — this is the backtest's own
#: window table and is deliberately not `orchestrator.WINDOW_DAYS`, which sizes
#: a *live* chart rather than a simulation's warm-up.
BACKTEST_WINDOW_DAYS = {1: 5, 5: 10, 15: 55, 60: 60, 240: 100, 1440: 300}


class BacktestService:
    """One slot, one job record, one report pair on disk."""

    def __init__(self, *, config, provider, reports_dir, runtime, task_name,
                 backtester, clock=None):
        self._cfg = config
        #: A callable, so a provider swapped on a live orchestrator is the one
        #: the next backtest reads.
        self._provider = provider
        self._reports_dir = reports_dir
        self._runtime = runtime
        self._task_name = task_name
        #: `optionspilot.backtest.Backtester`, handed down by the host.
        self._backtester = backtester
        self._clock = clock or utcnow

        self._lock = threading.Lock()
        self.job: dict = {"state": "idle"}
        self._pending: tuple[str, int, float | None] | None = None

    # ── the slot ─────────────────────────────────────────────────────────────

    def start(self, symbol: str, days: int, min_confidence: float | None
              ) -> dict:
        """Claim the single slot and let the runtime execute it.

        The claim is not a second copy of the runtime's per-task overlap guard:
        that guard is dispatch state, while `job` is the user-visible record
        carrying the symbol, the report and any error, and `GET /api/backtest`
        returns it.
        """
        if self._backtester is None or self._runtime is None:
            # A host that supplies neither cannot backtest. Saying so beats an
            # absent attribute, which fails at whatever line first touches it.
            return {"state": "error", "symbol": symbol.upper(),
                    "error": "backtesting is not available on this host"}
        with self._lock:
            if self.job.get("state") == "running":
                return self.job
            self._pending = (symbol.upper(), days, min_confidence)
            self.job = {"state": "running", "symbol": symbol.upper(),
                        "started": self._clock().isoformat()}
            claimed = self.job
        # The runtime owns the execution, so it has to be alive to own it —
        # `start()` is idempotent and registers no scheduled work.
        self._runtime.start()
        if not self._runtime.trigger(self._task_name):
            # Unreachable while the task is registered at construction, and
            # released rather than trusted: a claimed slot with nothing behind
            # it would wedge every later backtest for the life of the process.
            with self._lock:
                self._pending = None
                self.job = {"state": "error", "symbol": symbol.upper(),
                            "error": "the background runtime is unavailable"}
                return self.job
        return claimed

    def take_pending(self) -> tuple[str, int, float | None] | None:
        """Drain the claim exactly once.

        Returns None for a trigger with no claim behind it — inventing
        parameters there would run a backtest nobody requested.
        """
        with self._lock:
            pending, self._pending = self._pending, None
        return pending

    # ── the run ──────────────────────────────────────────────────────────────

    def run(self, symbol: str, days: int, min_confidence: float | None) -> None:
        try:
            cfg = self._cfg.model_copy(deep=True)
            if min_confidence is not None:
                cfg.engine.min_confidence = min_confidence
            end = self._clock()
            provider = self._provider()
            # Exactly the original expression: `days` shortens the 15-minute
            # window and nothing else. It is a cap, never an extension, and the
            # other five are fixed by what providers actually serve.
            windows = dict(BACKTEST_WINDOW_DAYS)
            windows[15] = min(days, windows[15])
            candles = {}
            for name in {*cfg.engine.entry_timeframes,
                         *cfg.engine.htf_trend_timeframes}:
                tf = Timeframe.from_string(name)
                candles[tf] = provider.get_candles(
                    symbol, tf, end - timedelta(days=windows[tf.minutes]), end)
            report = self._backtester(cfg).run(symbol, candles)
            report.save_json(self._reports_dir / f"{symbol.lower()}.json")
            report.save_html(self._reports_dir / f"{symbol.lower()}.html")
            with self._lock:
                self.job = {"state": "done", "symbol": symbol,
                            "report": report.to_dict()}
        except Exception as exc:  # noqa: BLE001
            log.exception("backtest failed: %s", exc)
            with self._lock:
                self.job = {"state": "error", "symbol": symbol,
                            "error": str(exc)}
