"""ServiceRegistry — one place that wires the services to a running app.

A registry rather than each transport constructing its own set, for the reason
this codebase keeps rediscovering: two constructions of the same service will
eventually be given different collaborators, and the resulting disagreement is
invisible from either side. `ui/server.py` builds one of these; a future mobile
backend host would build one of these; a test builds one of these. Nobody
constructs a `PortfolioService` by hand.

It is deliberately thin — no lifecycle, no lazy singletons beyond simple
memoisation, no dependency-injection framework. The registry's whole job is to
know which collaborator each service needs, so that knowledge exists once.

The constructor signature is the *interface a host must satisfy*, and it is
short on purpose: an orchestrator, a runtime settings store, a config, a lock,
and a symbol directory. That list is the honest answer to "what does a second
client's backend have to provide" — everything else is derived.
"""

from __future__ import annotations

from optionspilot.config.runtime import MAX_WATCHLIST
from optionspilot.core.models import utcnow
from optionspilot.host import current_host
from optionspilot.services.backtest import BacktestService
from optionspilot.services.charts import ChartService
from optionspilot.services.home import HomeService
from optionspilot.services.intelligence import IntelligenceService
from optionspilot.services.marketdata import MarketDataAdminService
from optionspilot.services.notifications import NotificationService
from optionspilot.services.portfolio import ET, PortfolioService
from optionspilot.services.statusline import StatusInputs
from optionspilot.services.trading import TradingService
from optionspilot.services.viewmodels import HostView
from optionspilot.services.watchlist import WatchlistService
from optionspilot.services.workspace import WorkspaceService


class ServiceRegistry:
    """The application layer, bound to one running orchestrator."""

    def __init__(self, *, orchestrator, runtime, config, lock, directory,
                 trades, verify_symbol, on_symbols_added=None, host=None,
                 log=None, window_days=None, clock=None, replay=None,
                 watchlist_symbols=None, tz=None, reports_dir=None,
                 background=None, backtest_task=None, backtester=None):
        self._orch = orchestrator
        self._runtime = runtime
        self._cfg = config
        self._lock = lock
        self._host = host
        # Kept for `_status_facts`, which needs the same clock and timezone the
        # rest of the app reads "now" from, and the same journal the metrics do.
        self._clock = clock
        self._tz = tz
        self._trades = trades

        self.portfolio = PortfolioService(
            broker=orchestrator.broker,
            trades=trades,
            starting_balance=config.risk.starting_balance,
            lock=lock,
        )
        self.watchlist = WatchlistService(
            config, runtime, lock,
            directory=directory,
            verify_symbol=verify_symbol,
            max_symbols=MAX_WATCHLIST,
            on_added=on_symbols_added,
            log=log,
        )
        self.charts = ChartService(
            # Lambdas, not the objects: a provider (or one of its methods)
            # swapped on a live orchestrator must be the one the next chart
            # request uses. Capturing them here would freeze the seam.
            provider=lambda: orchestrator.provider,
            indicators=config.indicators,
            market_open=lambda now: orchestrator.market_open(now),
            # `orchestrator.WINDOW_DAYS` stays that module's fact; the host
            # hands it down rather than this layer keeping a second copy.
            window_days=window_days,
            # And the host's clock, for the same reason: the chart window and
            # the market-open flag are both read from "now", and a service
            # holding its own clock answers from a different one than the app
            # around it.
            clock=clock,
        )
        self.marketdata = MarketDataAdminService(
            provider=lambda: orchestrator.provider,
            # `getattr`, because an orchestrator built over an injected
            # provider double has no control plane at all — and resolving it
            # per call is what lets a test attach one afterwards.
            control=lambda: getattr(orchestrator, "marketdata", None),
            # `data.replay.replay`. Injected rather than imported: it reaches
            # the provider registry and the adapters, and this layer's value is
            # that it does not need them.
            replay=replay,
        )
        self.trading = TradingService(
            orchestrator=orchestrator,
            # THE SERVER'S lock, not a new one. C4's whole review focus is that
            # the scope is unchanged, and two lock objects would mean the
            # orchestrator was reachable from two threads each holding "a" lock.
            lock=lock,
            portfolio=self.portfolio,
            # Read per cycle, so a watchlist edited between scans takes effect
            # on the next one rather than at construction.
            watchlist=watchlist_symbols or (lambda: list(config.data.watchlist)),
            clock=clock,
            tz=tz,
        )
        self.backtest = BacktestService(
            config=config,
            provider=lambda: orchestrator.provider,
            reports_dir=reports_dir,
            runtime=background,
            task_name=backtest_task,
            # The `Backtester` class, injected: it drives engine + risk +
            # broker + journal, and importing it here would pull the whole
            # trading stack into a layer whose value is not needing one. A host
            # that supplies none simply cannot backtest, and the service says
            # so rather than the attribute being absent.
            backtester=backtester,
            clock=clock,
        )
        self.workspace = WorkspaceService(runtime)
        self.intelligence = IntelligenceService(orchestrator)
        self.notifications = NotificationService(orchestrator.notifier, runtime)
        self.home = HomeService(
            portfolio=self.portfolio,
            # Bound with the clock here so `HomeService` never holds one: a
            # service answering from its own "now" disagrees with the app
            # around it, which is why `ChartService` takes one injected too.
            performance=lambda: self.portfolio.performance(self._now_et()),
            facts=self._status_facts,
        )

    # ── the facts only a host can answer ─────────────────────────────────────

    def _now_et(self):
        clock = self._clock or utcnow
        return clock().astimezone(self._tz or ET)

    def _status_facts(self) -> StatusInputs:
        """Assemble the status line's inputs from this host's orchestrator.

        Lives here rather than in `HomeService` because half of it is not
        reachable from `services/` at all: the halt lives on `RiskManager`, and
        `tests/test_architecture.py` forbids `services/` from importing `risk/`.
        A backtest or replay host has entirely different answers, which is the
        second reason this is the registry's job and not the service's.

        Every read is guarded. A fact this host cannot answer stays at its
        `StatusInputs` default, which is the quiet reading — and the quiet
        readings are all reachable only after the alarming ones are ruled out,
        so a missing fact can never manufacture a false "nothing needs you".
        """
        orch = self._orch
        now = self._now_et()
        hour = now.hour
        part = ("morning" if hour < 12 else
                "afternoon" if hour < 18 else "evening")

        positions, account_value, today = [], 0.0, 0.0
        try:
            positions = self.portfolio.positions()
            account_value = self.portfolio.account().equity
            today = self.portfolio.pnl_windows(now).today
        except Exception:  # noqa: BLE001 - a broker read must not break the line
            pass

        halt = ""
        risk = getattr(orch, "risk", None)
        if risk is not None:
            halt = str(getattr(risk, "halt_reason", "") or "")

        try:
            has_traded = bool(self._trades())
        except Exception:  # noqa: BLE001
            has_traded = bool(positions)

        return StatusInputs(
            part_of_day=part,
            market_open=bool(orch.market_open(utcnow())),
            positions=len(positions),
            today_pnl=today,
            account_value=account_value,
            has_traded=has_traded,
            halt_reason=halt,
        )

    @property
    def host(self):
        """The host adapter, resolved on first use.

        Resolving constructs an `AppPaths` and reads `sys.platform`, so a
        registry whose caller never asks about the host pays for neither — and
        a test does not have to supply one to build the other five services.
        """
        if self._host is None:
            self._host = current_host()
        return self._host

    def host_view(self) -> HostView:
        described = self.host.describe()
        profile = described["profile"]
        return HostView(
            host=described["host"],
            python_platform=described["python_platform"],
            capabilities=profile["capabilities"],
            missing=profile["missing"],
            notes=profile["notes"],
            implemented=profile["implemented"],
        )
