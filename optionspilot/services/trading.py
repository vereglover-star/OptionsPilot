"""TradingService — the order path, the chain it is built from, and the cycle.

V0.9.2-C4, the highest-consequence extraction of the milestone. C2 and C3 moved
code that legitimately took no lock at all; everything here either holds the
orchestrator lock or deliberately does not, and the specification's review focus
is that **the scope is identical to before**. It is, and
`tests/test_trading_service.py::TestTheLockScopeIsUnchanged` asserts it directly
rather than by reading the code: `RLock._is_owned()` lets an instrumented
collaborator record whether the lock was held at the instant it was called.

**The two locks do different jobs and must stay different objects.**

  `lock` (injected, the server's `RLock`) guards *orchestrator state*. It is
  held for the stateful part of a cycle and for the whole of an order decision.

  `cycle_lock` (a plain `Lock`, owned here) serialises *whole cycles*, so the
  scheduled scan and a manual one can never interleave. A second cycle arriving
  while one runs is **declined, not queued** — queueing would run a full extra
  cycle the instant the first returned, which is not what the user asked for.

**What is deliberately outside the lock**, because it is the reason the UI stays
responsive during a scan: `fetch_watchlist_candles` only touches the
thread-safe provider, so a status read is never blocked for the length of a
network round trip per watchlist symbol. And `order.to_dict()` — serialising a
response is not orchestrator state, and extending the lock across it would make
every scan wait on JSON.

**It does not become a second gatekeeper.** `RiskManager` is still the only
entry gate and `OrderManager` the only execution path; this service calls
`approve_manual_entry` and `orders.place` on the injected orchestrator and
decides nothing about risk itself. The preflight approval for an immediate
market buy is here because such a fill never reaches `OrderManager.evaluate()`'s
fill-time risk callback — remove it and manual entries silently bypass the
circuit breaker and the daily entry limits.

It imports the order *vocabulary* (`OrderKind`, `TIF`, `BrokerError`) and
nothing else from `broker/`; `tests/test_architecture.py` bounds that to
`broker.base` and `broker.orders`, which keeps `broker.registry` — where the
unimplemented live-broker stubs live — permanently out of this layer.
"""

from __future__ import annotations

import threading
from datetime import date

from optionspilot.analysis.options_metrics import enrich_greeks, liquidity_score
from optionspilot.broker.base import BrokerError
from optionspilot.broker.orders import OrderKind, TIF
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import OptionRight, utcnow
from optionspilot.services import quickpick, review
from optionspilot.services.errors import ValidationError
from optionspilot.services.viewmodels import QuickPickView

log = get_logger("ui")

#: How many equity points to retain. The Dashboard charts the tail of this, and
#: an unbounded list would grow for the life of a session.
MAX_EQUITY_POINTS = 2000


class TradingService:
    """Manual order flow, the option chain, and the scan cycle."""

    def __init__(self, *, orchestrator, lock, portfolio, watchlist,
                 clock=None, tz=None, max_equity_points=MAX_EQUITY_POINTS):
        self._orch = orchestrator
        #: The SERVER's lock, injected. Constructing one here would serialise
        #: this service against nothing while leaving the orchestrator
        #: reachable from two threads that each hold "a" lock.
        self._lock = lock
        self._portfolio = portfolio
        #: `lambda: list(cfg.data.watchlist)` — read per cycle, so a watchlist
        #: edited between scans takes effect on the next one.
        self._watchlist = watchlist
        self._clock = clock or utcnow
        self._tz = tz
        self._max_equity_points = max_equity_points

        #: Cycle serialisation. A plain Lock, not an RLock: nothing re-enters a
        #: cycle, and reentrancy here would let a cycle start inside a cycle.
        self.cycle_lock = threading.Lock()
        self.scan_state: dict = {"running": False, "done": 0, "total": 0}
        self.last_summary: dict = {}
        self.equity_history: list[tuple[str, float]] = []

    # ── the scan cycle ───────────────────────────────────────────────────────

    def run_cycle(self, *, blocking: bool = True) -> dict:
        """One full cycle: parallel candle prefetch (no orchestrator lock, with
        live progress for the UI), then the stateful cycle under the lock.

        ``blocking=False`` declines rather than waits when a cycle is already
        in flight, returning ``{}``. That preserves the pre-V0.9.1-C5 manual
        scan behaviour exactly: `request_scan` used to test
        ``_cycle_lock.locked()`` and skip, so a request arriving during a
        scheduled scan produced nothing rather than a second cycle immediately
        afterwards. The synchronous ``/api/scan {"wait": true}`` path keeps the
        default and still blocks.
        """
        if not self.cycle_lock.acquire(blocking=blocking):
            log.info("scan request declined: a cycle is already running")
            return {}
        try:
            return self._run_cycle_locked()
        finally:
            self.cycle_lock.release()

    def _run_cycle_locked(self) -> dict:
        """The cycle body. `cycle_lock` is held by the caller."""
        symbols = list(self._watchlist())
        self.scan_state = {"running": True, "done": 0, "total": len(symbols)}
        try:
            candles = self._orch.fetch_watchlist_candles(
                symbols, on_symbol=self.on_symbol_fetched)
            with self._lock:
                summary = self._orch.run_cycle(candles=candles)
                self.last_summary = summary
                equity = self._orch.broker.get_account().equity
                self.equity_history.append((summary["ts"], equity))
                del self.equity_history[:-self._max_equity_points]
                return summary
        finally:
            self.scan_state = {"running": False,
                               "done": len(symbols), "total": len(symbols)}

    def on_symbol_fetched(self, symbol: str, frames: dict) -> None:
        """Progressive scan feedback: as each symbol's candles land, publish
        its fresh quote so watchlist prices tick in while the scan runs."""
        quote = self._orch._quote_snapshot(frames)
        with self._lock:
            state = dict(self.scan_state)
            state["done"] = state.get("done", 0) + 1
            self.scan_state = state
            if quote:
                self.last_summary.setdefault("quotes", {})[symbol] = quote

    # ── manual trading (Human Mode order flow) ───────────────────────────────

    def chain_payload(self, symbol: str, expiration: str = "") -> dict:
        symbol = symbol.upper()
        with self._lock:
            provider = self._orch.provider
            expirations = [e.isoformat() for e in provider.get_expirations(symbol)]
            if not expirations:
                return {"symbol": symbol, "expirations": [], "chain": []}
            exp = expiration or expirations[0]
            spot = provider.get_quote(symbol).last
            today = self._clock().date()
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

    def quick_pick(self, *, symbol: str, intent: str, expiration: str = "",
                   right: str = "call") -> dict:
        """Resolve a quick-pick chip into a concrete contract (M4-C1).

        **Server-side, and that is the decision.** The client already holds a
        chain payload and could pick the nearest strike itself in ten lines of
        JavaScript — and then the rule would live in two languages. §6.3 says
        the chips are the same intents Pilot expresses and the same the AI
        engine states its opportunities in, so "the suggestion and the action
        are the same object" only holds if there is one implementation of what
        an intent means. This is it; `quickpick.py` holds the rules and this
        method supplies it with data.

        The two-phase shape is why `quickpick` splits its axes: the expiry is
        chosen from dates alone, THEN that expiry's chain is fetched. Choosing
        both at once would need the chain for an expiration not yet chosen.
        """
        spec = quickpick.BY_KEY.get(str(intent or ""))
        if spec is None:
            return QuickPickView(
                intent=str(intent or ""), right=right,
                reason="that is not one of the quick picks").to_dict()

        symbol = str(symbol or "").upper()
        with self._lock:
            expirations = [e.isoformat()
                           for e in self._orch.provider.get_expirations(symbol)]
        choice = quickpick.expiration_for(spec, expirations,
                                          self._clock().date(), expiration)
        if not choice.ok:
            return QuickPickView(intent=spec.key, right=spec.right or right,
                                 reason=choice.reason).to_dict()

        payload = self.chain_payload(symbol, choice.expiration)
        return quickpick.contract_for(
            spec, payload.get("chain"), payload.get("spot"),
            current_right=right, expiration=choice.expiration,
            dte=choice.dte).to_dict()

    def review_order(self, payload: dict) -> dict:
        """The consequence restatement for an order about to be placed (M4-C2).

        **It reads the same quote `place_order` will read, and prices it with
        the same model the broker fills with.** `review.estimate_premium`
        mirrors `PaperBroker`'s arithmetic — ask x (1 + slippage) for a buy,
        bid x (1 - slippage) for a sell — because a review that quotes the mid
        describes a trade the system is not going to place, and this codebase
        treats a confidently wrong number as worse than an absent one.

        It places nothing and takes no risk decision. `RiskManager` remains the
        only entry gate and `OrderManager` the only execution path; this is a
        pure read, and R-3 forbids it becoming anything else.
        """
        symbol = str(payload.get("symbol", "")).upper()
        expiration = str(payload.get("expiration", ""))
        strike = float(payload.get("strike") or 0.0)
        right = str(payload.get("right", "call"))

        bid = ask = mid = spot = None
        dte = None
        with self._lock:
            provider = self._orch.provider
            try:
                exp_date = date.fromisoformat(expiration)
                for c in provider.get_option_chain(symbol, exp_date):
                    if c.strike == strike and c.right.value == right:
                        bid, ask, mid = c.bid, c.ask, c.mid
                        dte = c.dte(self._clock().date())
                        break
            except Exception:  # noqa: BLE001 — an unpriceable contract is a
                # state the review must DESCRIBE, not a failure it raises. The
                # view model has a sentence for "there is no live quote".
                pass
            try:
                spot = provider.get_quote(symbol).last
            except Exception:  # noqa: BLE001 — spot is advisory here too
                spot = None
            equity = self._orch.broker.get_account().equity
            slippage = float(self._orch.cfg.broker.slippage_pct)

        return review.review(
            side=str(payload.get("side", "buy_to_open")),
            kind=str(payload.get("kind", "market")),
            quantity=int(payload.get("quantity", 1) or 1),
            symbol=symbol, strike=strike, right=right, expiration=expiration,
            dte=dte, bid=bid, ask=ask, mid=mid, spot=spot, equity=equity,
            limit=payload.get("limit"), stop=payload.get("stop"),
            tif=str(payload.get("tif", "day")), slippage_pct=slippage,
        ).to_dict()

    def place_order(self, payload: dict) -> dict:
        kind = OrderKind(str(payload.get("kind", "market")))
        tif = TIF(str(payload.get("tif", "day")))
        side = str(payload.get("side", "buy_to_open"))
        symbol = str(payload.get("symbol", "")).upper()
        expiration = date.fromisoformat(str(payload.get("expiration")))
        strike = float(payload.get("strike"))
        right = OptionRight(str(payload.get("right")))
        quantity = int(payload.get("quantity", 1))

        with self._lock:
            provider = self._orch.provider
            chain = provider.get_option_chain(symbol, expiration)
            contract = next(
                (c for c in chain
                 if c.strike == strike and c.right is right), None)
            if contract is None:
                # `ValidationError`, not `NotFound`: the endpoint exists and the
                # request was understood — the payload names a contract that
                # cannot be traded. `NotFound` is for addressing a resource that
                # is absent, which a POST to /api/orders is not doing.
                raise ValidationError(
                    f"no {right.value} @ {strike} for {symbol} {expiration}",
                    details={"symbol": symbol, "strike": strike,
                             "right": right.value,
                             "expiration": expiration.isoformat()})
            try:
                spot = provider.get_quote(symbol).last
            except Exception:  # noqa: BLE001 — spot is advisory for buys
                spot = 0.0
            if side == "buy_to_open" and kind is OrderKind.MARKET:
                # immediate fills never reach OrderManager.evaluate()'s
                # fill-time risk callback — preflight them here so manual
                # entries can't bypass the circuit breaker / entry limits
                decision = self._orch.approve_manual_entry(
                    contract, quantity, self._clock(), premium=contract.ask)
                if not decision.approved:
                    # A risk veto is "understood and not acceptable" — the
                    # circuit breaker is open, or the daily entry limit is
                    # spent. It was a `BrokerError` and reached the client as
                    # 422; it is now a service error carrying the same message
                    # and the same status, but classifiable by a caller that
                    # is not HTTP.
                    raise ValidationError(decision.veto,
                                          details={"reason": "risk_veto"})
            try:
                order, event = self._orch.orders.place(
                    kind=kind, side=side, contract=contract,
                    quantity=quantity, ts=self._clock(), tif=tif,
                    limit_price=float(payload.get("limit_price") or 0),
                    stop_level=float(payload.get("stop_level") or 0),
                    trail=float(payload.get("trail") or 0),
                    trail_pct=float(payload.get("trail_pct") or 0),
                    spot=spot,
                )
            except BrokerError as exc:
                # `OrderManager` refuses three order combinations (a limit with
                # no price, and two others). That refusal is a statement about
                # the REQUEST, so it leaves this service classified rather than
                # as a broker exception the transport has to recognise by type.
                raise ValidationError(str(exc)) from exc
            if (event and event["event"] == "filled"
                    and side == "buy_to_open"):
                # track immediately so fast round trips still get coached,
                # and count the entry against the daily trade limit
                self._orch.register_manual_entry(contract.symbol,
                                                 entry_ts=self._clock())
        # OUTSIDE the lock, deliberately: serialising a response is not
        # orchestrator state, and holding the lock across it would make a
        # waiting scan queue behind JSON.
        return {"order": order.to_dict(),
                "event": event["event"] if event else "working"}

    # ── account ──────────────────────────────────────────────────────────────

    def account_metrics(self) -> dict:
        now = self._clock()
        if self._tz is not None:
            now = now.astimezone(self._tz)
        return self._portfolio.performance(now).to_dict()
