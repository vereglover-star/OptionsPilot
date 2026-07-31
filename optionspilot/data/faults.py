"""Fault injection — make a failure happen on purpose, so it can be watched.

Every failure mode this subsystem handles is documented, tested against canned
payloads, and impossible to *see*. "The chart falls back to yfinance when Yahoo
times out" is a sentence in `docs/MARKET_DATA.md` and a green test; it is not
something a maintainer has ever observed happening in the real UI, because
making Yahoo time out on demand meant unplugging a network cable.

This module makes it a checkbox. A fault is registered against a provider name,
consulted once inside `HistoryAdapter.fetch_history`, and raises exactly the
`ProviderError` the real condition would raise — so everything downstream (the
health monitor, the breaker, the ranking, the tier ladder, the diagnostics
trace, the frontend state machine) behaves identically to the genuine article.
That is the whole point: a simulation that took a shortcut past the error types
would prove nothing about the paths it skipped.

## Why this is safe to ship in the product

Three properties, in the order that matters:

1. **It is off unless a maintainer turns it on.** `market_data.qa_mode` defaults
   to False in `config.yaml`, the endpoints that reach this module 404 without
   it, and the QA panel is not rendered. A normal install cannot get here.
2. **The hot path costs one attribute read.** `_ACTIVE` is a plain bool checked
   before the dict lookup, so a fetch with no faults registered does no work
   beyond `if False:`.
3. **Nothing here can persist.** Faults live in memory, are cleared on restart,
   and `clear_all()` is one call. A simulated outage cannot outlive the session
   that asked for it — which also means a maintainer cannot leave one armed and
   later mistake it for a real provider failure.

## What it deliberately does NOT do

It does not simulate *corrupt data* by writing bad rows into the real cache.
An injected `unusable` fault returns a frame the validator will reject, which
exercises the same quarantine path (`service._quarantine`) with none of the
"the QA tool trashed my cache" risk. The cache-corruption drill lives in
`MarketDataControl` instead, where it operates on a scratch copy.
"""

from __future__ import annotations

import threading
import time as _time

import pandas as pd

from optionspilot.core.logging_setup import get_logger

log = get_logger("data")


def _errors():
    """The `ProviderError` classes, imported on first use.

    `adapter.py` imports THIS module (the fault check lives inside
    `fetch_history`, which is the only place a simulated failure is
    indistinguishable from a real one), so importing it back at module scope
    would be a cycle. The import is deferred to the moment a fault actually
    fires — which by construction is never in a normal install — so the
    indirection costs nothing on any path a user reaches.
    """
    from optionspilot.data import adapter
    return adapter

# ── the fault vocabulary ─────────────────────────────────────────────────────
#
# Each entry names a real condition the subsystem claims to survive. Adding one
# here is how a new claim becomes demonstrable.

FAULT_OUTAGE = "outage"            # the provider is unreachable
FAULT_TIMEOUT = "timeout"          # the request never comes back
FAULT_RATE_LIMIT = "rate_limit"    # upstream says slow down
FAULT_QUOTA = "quota"              # the plan's allowance is spent
FAULT_AUTH = "auth"                # the key is rejected
FAULT_LATENCY = "latency"          # answers correctly, but slowly
FAULT_EMPTY = "empty"              # answers with zero bars
FAULT_UNUSABLE = "unusable"        # answers with bars validation will reject

ALL_FAULTS = (FAULT_OUTAGE, FAULT_TIMEOUT, FAULT_RATE_LIMIT, FAULT_QUOTA,
              FAULT_AUTH, FAULT_LATENCY, FAULT_EMPTY, FAULT_UNUSABLE)

#: Plain-English description of each, shown next to its control in the QA panel
#: so a maintainer picks a fault by what it proves rather than by its slug.
FAULT_TEXT: dict[str, str] = {
    FAULT_OUTAGE: "the provider is unreachable — the ladder should fall "
                  "through to the next one",
    FAULT_TIMEOUT: "the request never returns — the per-provider timeout "
                   "should abandon it, not the whole chart",
    FAULT_RATE_LIMIT: "upstream asks us to back off — the provider should "
                      "leave rotation and return on its own",
    FAULT_QUOTA: "the plan's allowance is spent — the provider should leave "
                 "rotation until the budget resets",
    FAULT_AUTH: "the key is rejected — the provider should be benched "
                "stickily rather than retried on every chart load",
    FAULT_LATENCY: "answers correctly but slowly — dynamic ranking should "
                   "demote it below a faster provider",
    FAULT_EMPTY: "answers with zero bars — a weekend is not an outage and "
                 "must not trip a breaker",
    FAULT_UNUSABLE: "answers promptly with bars validation rejects — the "
                    "provider must NOT be credited with a success",
}


class _Fault:
    """One armed fault. `remaining` counts down; None means "until cleared"."""

    __slots__ = ("kind", "remaining", "seconds", "armed_at")

    def __init__(self, kind: str, remaining: int | None, seconds: float):
        self.kind = kind
        self.remaining = remaining
        self.seconds = seconds
        self.armed_at = _time.time()

    def as_dict(self) -> dict:
        return {"kind": self.kind, "remaining": self.remaining,
                "seconds": self.seconds,
                "description": FAULT_TEXT.get(self.kind, self.kind)}


class FaultInjector:
    """Per-provider armed faults. Thread-safe.

    One process-wide instance (`FAULTS`) rather than an injectable dependency,
    because the thing being tested is the *shipped* wiring: an injector passed
    into a test-built adapter would prove the test's chain works, which is not
    the question. The global is only reachable through QA-gated endpoints.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faults: dict[str, _Fault] = {}
        #: Read on every fetch. A plain bool so the common case — no faults
        #: anywhere — costs one attribute load and no lock acquisition.
        self.active = False

    # ── arming ───────────────────────────────────────────────────────────────

    def arm(self, provider: str, kind: str, *, count: int | None = None,
            seconds: float = 2.0) -> dict:
        """Arm `kind` against `provider`.

        `count=None` keeps the fault until it is cleared, which is what a
        maintainer wants for "watch the chart while this provider is down".
        A finite `count` is what a test wants: it proves recovery happens by
        itself rather than because someone disarmed it.
        """
        if kind not in ALL_FAULTS:
            raise ValueError(f"unknown fault {kind!r} "
                             f"(known: {', '.join(ALL_FAULTS)})")
        fault = _Fault(kind, count, max(0.0, float(seconds)))
        with self._lock:
            self._faults[provider] = fault
            self.active = True
        log.warning("QA MODE: armed fault %r against market-data provider %s "
                    "(%s)", kind, provider,
                    "until cleared" if count is None else f"{count} request(s)")
        return {"provider": provider, **fault.as_dict()}

    def clear(self, provider: str) -> bool:
        with self._lock:
            existed = self._faults.pop(provider, None) is not None
            self.active = bool(self._faults)
        if existed:
            log.warning("QA MODE: cleared the fault on market-data provider %s",
                        provider)
        return existed

    def clear_all(self) -> int:
        with self._lock:
            count = len(self._faults)
            self._faults.clear()
            self.active = False
        if count:
            log.warning("QA MODE: cleared %d armed market-data fault(s)", count)
        return count

    def armed(self) -> dict[str, dict]:
        with self._lock:
            return {name: f.as_dict() for name, f in self._faults.items()}

    # ── the hot path ─────────────────────────────────────────────────────────

    def check(self, provider: str) -> pd.DataFrame | None:
        """Apply any armed fault for `provider`.

        Returns None (the overwhelmingly common case) to mean "carry on with
        the real fetch". Returns a DataFrame for faults whose whole point is a
        *successful-looking* answer, and raises the genuine `ProviderError`
        subclass for the rest — never a bespoke exception, because the classes
        are what the retry/failover policy is written against.
        """
        if not self.active:
            return None
        with self._lock:
            fault = self._faults.get(provider)
            if fault is None:
                return None
            if fault.remaining is not None:
                fault.remaining -= 1
                if fault.remaining <= 0:
                    del self._faults[provider]
                    self.active = bool(self._faults)
            kind, seconds = fault.kind, fault.seconds

        if kind == FAULT_LATENCY:
            # A real slow provider blocks the calling thread, and that is the
            # behaviour being demonstrated — the ranking demotes it because the
            # measured latency is real, not because a number was written down.
            _time.sleep(seconds)
            return None
        err = _errors()
        if kind == FAULT_TIMEOUT:
            raise err.ProviderTimeout(
                f"[QA MODE] simulated timeout after {seconds:.0f}s")
        if kind == FAULT_OUTAGE:
            raise err.ProviderUnavailable("[QA MODE] simulated provider outage")
        if kind == FAULT_RATE_LIMIT:
            raise err.ProviderRateLimited("[QA MODE] simulated rate limit",
                                          retry_after=max(1.0, seconds))
        if kind == FAULT_QUOTA:
            raise err.ProviderQuotaExceeded(
                "[QA MODE] simulated quota exhaustion",
                retry_after=max(1.0, seconds))
        if kind == FAULT_AUTH:
            raise err.ProviderAuthError(
                "[QA MODE] simulated authentication failure")
        if kind == FAULT_EMPTY:
            return _empty_frame()
        if kind == FAULT_UNUSABLE:
            return _unusable_frame()
        return None  # pragma: no cover — `arm` rejects anything unhandled


def _empty_frame() -> pd.DataFrame:
    """A legitimately empty answer: a weekend, a holiday, a pre-listing range."""
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                        index=pd.DatetimeIndex([], tz="UTC", name="ts"))


def _unusable_frame() -> pd.DataFrame:
    """Bars that parse but that `quality.validate_history` must reject.

    Three bars spaced a *day* apart under whatever interval was requested: the
    interval-conformance check reads a median spacing far from 1.0 and refuses
    the frame. This is the exact shape of the defect the V0.5.3 consolidation
    exposed — a provider answering promptly with garbage and being credited
    with a success — so arming it is how that fix stays demonstrably fixed.
    """
    index = pd.date_range("2020-01-01", periods=3, freq="1D", tz="UTC",
                          name="ts")
    return pd.DataFrame(
        {"open": [1.0, 1.0, 1.0], "high": [1.0, 1.0, 1.0],
         "low": [1.0, 1.0, 1.0], "close": [1.0, 1.0, 1.0],
         "volume": [1.0, 1.0, 1.0]}, index=index)


#: The process-wide injector. Reached only through QA-gated endpoints.
FAULTS = FaultInjector()


__all__ = ["FaultInjector", "FAULTS", "ALL_FAULTS", "FAULT_TEXT",
           "FAULT_OUTAGE", "FAULT_TIMEOUT", "FAULT_RATE_LIMIT", "FAULT_QUOTA",
           "FAULT_AUTH", "FAULT_LATENCY", "FAULT_EMPTY", "FAULT_UNUSABLE"]
