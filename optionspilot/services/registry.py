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
from optionspilot.host import current_host
from optionspilot.services.charts import ChartService
from optionspilot.services.intelligence import IntelligenceService
from optionspilot.services.marketdata import MarketDataAdminService
from optionspilot.services.notifications import NotificationService
from optionspilot.services.portfolio import PortfolioService
from optionspilot.services.viewmodels import HostView
from optionspilot.services.watchlist import WatchlistService
from optionspilot.services.workspace import WorkspaceService


class ServiceRegistry:
    """The application layer, bound to one running orchestrator."""

    def __init__(self, *, orchestrator, runtime, config, lock, directory,
                 trades, verify_symbol, on_symbols_added=None, host=None,
                 log=None, window_days=None, clock=None, replay=None):
        self._orch = orchestrator
        self._runtime = runtime
        self._cfg = config
        self._lock = lock
        self._host = host

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
        self.workspace = WorkspaceService(runtime)
        self.intelligence = IntelligenceService(orchestrator)
        self.notifications = NotificationService(orchestrator.notifier, runtime)

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
