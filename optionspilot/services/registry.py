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


def _text_of(item) -> str:
    """The headline of an intelligence record, whatever shape it arrives in.

    `intelligence/` records are read STRUCTURALLY across this boundary — the
    engine imports `core` only and nothing imports its record classes — so this
    reads keys rather than attributes, and tolerates a plain string.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        # `headline` first: it is what `intelligence/risk.py::_observation` and
        # the recommendation records actually call this field. The rest are
        # tolerated shapes, not guesses — a key list that misses the real one
        # renders an item with no text, which is what the first version of this
        # did (two blank rows at the top of H4, found by looking at the screen).
        for key in ("headline", "title", "text", "message", "summary", "name"):
            value = item.get(key)
            if value:
                return str(value)
    return ""


def _detail_of(item) -> str:
    """Its evidence line, if it carries one."""
    if isinstance(item, dict):
        for key in ("detail", "rationale", "why", "evidence", "body"):
            value = item.get(key)
            if value:
                return str(value)
    return ""


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
            intelligence=self._next_actions,
            working_orders=self._working_orders,
            # The BROKER's recorded history, not `TradingService.equity_history`.
            # The latter is the in-memory session list, and §5.5 is explicit
            # that a session curve on a freshly launched paper account is noise
            # — which is exactly why band 3 defaults to 30 days.
            equity=self._equity_history,
            watchlist=self._watchlist_confidence,
        )

    # ── H4: what to do next ──────────────────────────────────────────────────

    def _working_orders(self) -> list:
        book = getattr(self._orch, "orders", None)
        if book is None or not hasattr(book, "working"):
            return []
        out = []
        for order in book.working():
            out.append({
                "id": getattr(order, "id", ""),
                "contract": getattr(order, "contract_symbol", ""),
                "kind": getattr(getattr(order, "kind", None), "value",
                                str(getattr(order, "kind", ""))),
                "quantity": getattr(order, "quantity", 0),
                "limit": getattr(order, "limit_price", None),
                "stop": getattr(order, "stop_price", None),
            })
        return out

    def _equity_history(self) -> list:
        """Recorded equity snapshots, oldest first, as `[iso, value]` pairs.

        Capped at the same 500 `PerformanceView` uses, so the two never carry
        different amounts of the same curve. The client windows it to 30/90/all
        — the range control is a view over one series, not three requests.
        """
        broker = self._orch.broker
        if not hasattr(broker, "equity_history"):
            return []
        with self._lock:
            history = list(broker.equity_history())
        return [[ts, value] for ts, value in history[-500:]]

    def _watchlist_confidence(self) -> list:
        """H6: symbol, verdict, confidence, and the confidence it NEEDED.

        `required` is the tick the design asks for beside each bar, and it is
        carried per symbol rather than as one global number because in
        High-Risk Mode the floor adapts to setup quality — one shared threshold
        would draw the tick in the wrong place for every symbol but one.
        """
        summary = self.trading.last_summary or {}
        signals = summary.get("signals") or {}
        rows = []
        for symbol, sig in signals.items():
            if not isinstance(sig, dict):
                continue
            rows.append({
                "symbol": symbol,
                "direction": sig.get("direction") or "",
                "confidence": sig.get("confidence"),
                "required": sig.get("min_confidence_required"),
                "accepted": bool(sig.get("accepted")),
            })
        # Tradeable first, then by confidence — the engine's own ordering for
        # this list, mirrored from the legacy opportunities panel so the two
        # cannot disagree about which setup is strongest while both exist.
        rows.sort(key=lambda r: (not r["accepted"], -(r["confidence"] or 0)))
        return rows

    def _next_actions(self) -> list | None:
        """H4's ranking: risk condition -> evidenced finding -> cleared setups.

        The priority order is `UI_V2_DESIGN.md` §5.4's, and the UI renders this
        list verbatim — it does not re-rank, filter or extend it (§2.4). What
        this method must not do is *invent* a ranking: within each tier the
        order is the source's own, because `intelligence/` already ranks with a
        false-discovery correction applied and a second sort here would be an
        opinion about evidence formed by the layer with the least of it.

        Returns `None` when the analysis could not be read at all. "No
        findings" and "I could not look" are different answers and §2.10
        requires the second to be visible — a silent empty region is
        indistinguishable from "nothing is wrong".
        """
        items: list[dict] = []
        try:
            summary = self.intelligence.summary()
        except Exception:  # noqa: BLE001 - the region reports, Home survives
            return None

        # 1. Risk conditions that are true right now.
        risk = summary.get("risk") or {}
        if risk.get("assessable"):
            for obs in (risk.get("observations") or [])[:2]:
                text = _text_of(obs)
                # An item with no headline is a blank row, and a blank row in a
                # ranked list reads as "something is here that I cannot show
                # you". Drop it rather than rendering it.
                if text:
                    items.append({"kind": "risk", "text": text,
                                  "detail": "", "action": None})

        # 2. One evidenced behavioural finding, with its evidence attached.
        #    `intelligence/` never states what it cannot evidence, so whatever
        #    reaches here already carries its sample size — and §2.13 requires
        #    that to be IN the item, not in a tooltip, because tooltip-only
        #    evidence is invisible to a keyboard and to assistive technology.
        for rec in (summary.get("recommendations") or [])[:1]:
            text = _text_of(rec)
            if text:
                items.append({"kind": "finding", "text": text,
                              "detail": _detail_of(rec),
                              "action": {"label": "Show me", "tab": "coach"}})

        # 3. Cleared setups — the engine's current opportunities, compact.
        signals = (self.trading.last_summary or {}).get("signals") or {}
        cleared = [(sym, sig) for sym, sig in signals.items()
                   if isinstance(sig, dict) and sig.get("accepted")]
        for sym, sig in cleared[:3]:
            conf = sig.get("confidence")
            items.append({
                "kind": "setup", "symbol": sym,
                "text": f"{sym} {sig.get('direction', '')}".strip(),
                "detail": f"{round(conf * 100)}% confidence" if conf else "",
                "action": {"label": "Trade", "tab": "trade", "symbol": sym},
            })
        return items

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
