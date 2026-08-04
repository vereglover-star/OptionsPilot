"""V0.9.2-C4 — the trading surface, moved out of the transport.

The highest-consequence extraction of the milestone: this is the manual order
path, the option chain it is assembled from, and the scan cycle that opens and
manages AI positions. C2 and C3 legitimately took no lock at all; here the lock
is real, and the specification's review focus is that its **scope is identical
to before**.

So the characterization asserts the scope directly rather than the outputs
alone. `threading.RLock` exposes `_is_owned()`, so an instrumented collaborator
can record whether the caller held the lock at the moment it was called, and the
test pins the answer for every step of both paths:

    place_order   chain fetch, quote, risk approval and `orders.place` are
                  INSIDE; `order.to_dict()` is OUTSIDE, which is what the old
                  code's `return` placement said and nothing had ever checked.

    run_cycle     `fetch_watchlist_candles` is OUTSIDE — the documented reason
                  the UI stays responsive during a scan, since candle fetching
                  only touches the thread-safe provider — and `run_cycle` plus
                  the equity/summary bookkeeping are INSIDE.

Every test here was written and passing against `ui/server.py` before
`services/trading.py` existed, except the two that name the new module.
"""

from __future__ import annotations

import ast
import pathlib
import threading

import pytest

from optionspilot.broker.base import BrokerError
from optionspilot.core.models import OptionRight, Timeframe
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles

TRADING_PY = (pathlib.Path(__file__).resolve().parent.parent
              / "optionspilot" / "services" / "trading.py")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    from optionspilot.ui.server import UIServer

    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    cfg = CFG.model_copy(deep=True)
    orch = Orchestrator(
        cfg, provider=FakeProvider(candles, spot, NOW.date()),
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    return UIServer(cfg, orchestrator=orch, data_dir=tmp_path)


def _first_contract(server, symbol="SPY"):
    """A real contract from the fake chain, for building an order payload."""
    exp = server.orch.provider.get_expirations(symbol)[0]
    chain = server.orch.provider.get_option_chain(symbol, exp)
    return exp, next(c for c in chain if c.right is OptionRight.CALL)


class TestTheLockScopeIsUnchanged:
    """The review focus, asserted rather than reviewed.

    `_is_owned()` answers "does the calling thread hold this RLock right now",
    so wrapping a collaborator records the lock state at the exact instant the
    real code reaches it.
    """

    def test_the_server_and_the_service_share_one_lock(self, server):
        """Not a copy, not a second lock — the same object.

        A service that constructed its own lock would serialise itself against
        nothing, and the orchestrator would be reachable from two threads at
        once with both of them holding "a" lock.
        """
        assert server.services.trading._lock is server.lock

    def test_place_order_holds_the_lock_for_the_whole_decision(self, server):
        """Chain lookup, quote, risk approval and `place` are all inside."""
        held: dict[str, bool] = {}
        lock = server.lock
        exp, contract = _first_contract(server)

        real_chain = server.orch.provider.get_option_chain
        real_quote = server.orch.provider.get_quote
        real_place = server.orch.orders.place
        real_approve = server.orch.approve_manual_entry

        def chain(symbol, expiration):
            held["get_option_chain"] = lock._is_owned()
            return real_chain(symbol, expiration)

        def quote(symbol):
            held["get_quote"] = lock._is_owned()
            return real_quote(symbol)

        def approve(*a, **k):
            held["approve_manual_entry"] = lock._is_owned()
            return real_approve(*a, **k)

        def place(**kwargs):
            held["orders.place"] = lock._is_owned()
            return real_place(**kwargs)

        server.orch.provider.get_option_chain = chain
        server.orch.provider.get_quote = quote
        server.orch.approve_manual_entry = approve
        server.orch.orders.place = place

        server.place_order({
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": exp.isoformat(), "strike": contract.strike,
            "right": contract.right.value, "quantity": 1,
        })
        assert held == {
            "get_option_chain": True, "get_quote": True,
            "approve_manual_entry": True, "orders.place": True,
        }
        assert not lock._is_owned(), "the lock outlived place_order"

    def test_the_response_is_built_after_the_lock_is_released(self, server):
        """`order.to_dict()` sits outside the `with`, and that is deliberate:
        serialising a response is not orchestrator state and must not extend a
        lock that a running scan is waiting on. Nothing had ever asserted it —
        the placement was one line of indentation away from being lost.
        """
        lock = server.lock
        exp, contract = _first_contract(server)
        real_place = server.orch.orders.place
        seen: dict[str, bool] = {}

        class _RecordingOrder:
            """A proxy, because `WorkingOrder` uses __slots__ and its methods
            cannot be replaced in place."""

            def __init__(self, inner):
                object.__setattr__(self, "_inner", inner)

            def to_dict(self):
                seen["to_dict_under_lock"] = lock._is_owned()
                return self._inner.to_dict()

            def __getattr__(self, name):
                return getattr(object.__getattribute__(self, "_inner"), name)

        def place(**kwargs):
            order, event = real_place(**kwargs)
            return _RecordingOrder(order), event

        server.orch.orders.place = place
        server.place_order({
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": exp.isoformat(), "strike": contract.strike,
            "right": contract.right.value, "quantity": 1,
        })
        assert seen["to_dict_under_lock"] is False

    def test_chain_payload_holds_the_lock_throughout(self, server):
        lock = server.lock
        held: dict[str, bool] = {}
        real_expirations = server.orch.provider.get_expirations

        def expirations(symbol):
            held["get_expirations"] = lock._is_owned()
            return real_expirations(symbol)

        server.orch.provider.get_expirations = expirations
        server.chain_payload("SPY")
        assert held["get_expirations"] is True
        assert not lock._is_owned()

    def test_candle_fetching_stays_outside_the_lock(self, server):
        """The documented reason a scan does not freeze the UI.

        Candle fetching only touches the thread-safe provider, so it runs
        outside; the stateful cycle runs inside. Taking the lock across the
        fetch would block every status read for the length of a network round
        trip per watchlist symbol.
        """
        lock = server.lock
        held: dict[str, bool] = {}
        real_fetch = server.orch.fetch_watchlist_candles
        real_cycle = server.orch.run_cycle

        def fetch(symbols, on_symbol=None):
            held["fetch_watchlist_candles"] = lock._is_owned()
            return real_fetch(symbols, on_symbol=on_symbol)

        def cycle(**kwargs):
            held["run_cycle"] = lock._is_owned()
            return real_cycle(**kwargs)

        server.orch.fetch_watchlist_candles = fetch
        server.orch.run_cycle = cycle
        server.run_cycle_now()
        assert held == {"fetch_watchlist_candles": False, "run_cycle": True}


class TestTheOrderPathIsUnchanged:
    def test_a_market_buy_fills_and_is_registered_as_a_manual_entry(self, server):
        """An immediate fill never reaches `OrderManager.evaluate()`'s
        fill-time risk callback, so the entry is registered here — that is what
        counts it against the daily trade limit and gets it coached."""
        exp, contract = _first_contract(server)
        seen = {}
        real_register = server.orch.register_manual_entry

        def register(symbol, *, entry_ts):
            seen["symbol"] = symbol
            return real_register(symbol, entry_ts=entry_ts)

        server.orch.register_manual_entry = register
        out = server.place_order({
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": exp.isoformat(), "strike": contract.strike,
            "right": contract.right.value, "quantity": 1,
        })
        assert out["event"] == "filled"
        assert out["order"]["quantity"] == 1
        assert seen["symbol"] == contract.symbol

    def test_a_risk_veto_refuses_the_order(self, server):
        """The preflight gate, which exists because a market buy bypasses the
        fill-time risk callback entirely. If this stops firing, manual entries
        silently ignore the circuit breaker and the entry limits."""
        exp, contract = _first_contract(server)

        class _Vetoed:
            approved = False
            veto = "circuit breaker open"

        server.orch.approve_manual_entry = lambda *a, **k: _Vetoed()
        with pytest.raises(BrokerError, match="circuit breaker open"):
            server.place_order({
                "kind": "market", "side": "buy_to_open", "symbol": "SPY",
                "expiration": exp.isoformat(), "strike": contract.strike,
                "right": contract.right.value, "quantity": 1,
            })

    def test_an_unknown_contract_is_refused_by_name(self, server):
        exp, contract = _first_contract(server)
        with pytest.raises(ValueError, match="no call @ 99999"):
            server.place_order({
                "kind": "market", "side": "buy_to_open", "symbol": "SPY",
                "expiration": exp.isoformat(), "strike": 99999.0,
                "right": "call", "quantity": 1,
            })

    def test_a_missing_quote_does_not_stop_a_buy(self, server):
        """Spot is advisory for a buy — the ask is what fills it. A provider
        that cannot quote the underlying must not block an order the user has
        already priced from the chain."""
        exp, contract = _first_contract(server)

        def boom(symbol):
            raise RuntimeError("quote feed down")

        server.orch.provider.get_quote = boom
        out = server.place_order({
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": exp.isoformat(), "strike": contract.strike,
            "right": contract.right.value, "quantity": 1,
        })
        assert out["event"] == "filled"


class TestTheChainPayloadIsUnchanged:
    def test_it_returns_rows_with_liquidity_and_dte(self, server):
        payload = server.chain_payload("SPY")
        assert payload["symbol"] == "SPY"
        assert payload["expirations"]
        assert payload["expiration"] == payload["expirations"][0]
        assert payload["spot"] > 0
        row = payload["chain"][0]
        assert set(row) == {"strike", "right", "bid", "ask", "mid", "delta",
                            "iv", "volume", "open_interest", "liquidity", "dte"}

    def test_a_symbol_with_no_expirations_returns_an_empty_chain(self, server):
        server.orch.provider.get_expirations = lambda symbol: []
        assert server.chain_payload("ZZZZ") == {
            "symbol": "ZZZZ", "expirations": [], "chain": []}

    def test_the_symbol_is_upper_cased(self, server):
        assert server.chain_payload("spy")["symbol"] == "SPY"


class TestTheScanLifecycleIsUnchanged:
    def test_a_cycle_records_state_summary_and_equity(self, server):
        server.run_cycle_now()
        assert server.scan_state == {"running": False, "done": 1, "total": 1}
        assert server.last_summary.get("ts")
        assert len(server.equity_history) == 1

    def test_a_second_cycle_is_declined_rather_than_queued(self, server):
        """`blocking=False` declines. Queueing would run a whole extra cycle
        the instant the first returned, which is not what the user asked for —
        they asked for a scan, and one is already happening."""
        server._cycle_lock.acquire()
        try:
            assert server.run_cycle_now(blocking=False) == {}
        finally:
            server._cycle_lock.release()

    def test_progressive_feedback_publishes_a_quote_per_symbol(self, server):
        server.run_cycle_now()
        assert "SPY" in server.last_summary.get("quotes", {})

    def test_the_equity_history_is_capped(self, server):
        from optionspilot.ui.server import MAX_EQUITY_POINTS

        server.equity_history = [("t", 1.0)] * (MAX_EQUITY_POINTS + 50)
        server.run_cycle_now()
        assert len(server.equity_history) == MAX_EQUITY_POINTS

    def test_the_status_payload_still_reports_scan_state(self, server):
        """The transport reads this state; moving its owner must not change
        what a client sees."""
        payload = server.status_payload()
        assert payload["scan"] == {"running": False, "done": 0, "total": 0}
        server.run_cycle_now()
        assert server.status_payload()["scan"]["done"] == 1
        assert server.status_payload()["equity_history"]


class TestTheServiceIsRegisteredAndTransportFree:
    def test_the_registry_exposes_it(self, server):
        from optionspilot.services.trading import TradingService

        assert isinstance(server.services.trading, TradingService)

    def test_it_constructs_the_cycle_lock_and_nothing_else(self):
        """Precisely one lock is this module's to own.

        `cycle_lock` genuinely belongs here — it serialises cycles, which is
        this service's job. The ORCHESTRATOR lock does not: it is the server's,
        shared with every other service, and a second one would serialise this
        service against nothing while leaving the orchestrator reachable from
        two threads that each hold "a" lock.

        So the assertion is not "constructs no lock" (the first version of this
        test, which was simply wrong) but "constructs no RLock, and the one
        Lock it builds is assigned to `cycle_lock`".
        """
        tree = ast.parse(TRADING_PY.read_text(encoding="utf-8"))
        built: list[tuple[int, str, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            name = ast.unparse(value.func).split(".")[-1]
            if name in {"Lock", "RLock"}:
                built.append((node.lineno, name,
                              ast.unparse(node.targets[0])))
        kinds_and_targets = [(kind, target) for _, kind, target in built]
        assert kinds_and_targets == [("Lock", "self.cycle_lock")], \
            f"unexpected lock construction in services/trading.py: {built}"

    def test_importing_it_does_not_pull_in_fastapi(self):
        import subprocess
        import sys

        code = ("import sys; import optionspilot.services.trading; "
                "print([m for m in ('fastapi','starlette','uvicorn') "
                "if m in sys.modules])")
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]"


class TestTheCycleLockIsStillSeparate:
    """Two locks, two jobs, and conflating them would deadlock or serialise.

    `_cycle_lock` serialises whole cycles so a scheduled scan and a manual one
    cannot interleave. `self.lock` guards orchestrator state and is held only
    for the stateful part. They are deliberately different objects.
    """

    def test_they_are_not_the_same_object(self, server):
        assert server._cycle_lock is not server.lock

    def test_the_cycle_lock_is_not_reentrant_by_accident(self, server):
        assert isinstance(server._cycle_lock, type(threading.Lock()))
