"""Provider registry — who to ask, in what order, and when to stop asking.

The registry owns three decisions the service must not re-derive at each call
site:

**Ordering.** Adapters are tried by `provider_priority` (lowest first). Order
is a property of the registry, not of any provider, so re-prioritising is one
constructor argument.

**Eligibility.** An adapter that cannot serve the interval, the symbol, or the
requested session is skipped *before* the network — a capability check is free
and a failed request is not. This is what stops the app asking Stooq for
5-minute bars or Yahoo for 5-minute bars from last spring.

**Circuit breaking.** A provider that has failed repeatedly is taken out of
rotation for a growing cooldown instead of being retried on every chart tick.
Without this, one dead provider adds its full timeout to every single request —
the difference between a fast failover and a chart that takes 30 seconds to
give up. The breaker is *half-open* at the end of each cooldown: exactly one
probe request is allowed through, so recovery is automatic and costs one
request rather than a restart.

Rate-limit state is honoured as a form of breaker: a provider that told us to
back off is skipped until its window expires, no matter how healthy it looks.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import HistoryAdapter

log = get_logger("data")

#: Consecutive failures before a provider is taken out of rotation.
BREAKER_THRESHOLD = 3
#: First cooldown, doubling per subsequent trip, capped.
BREAKER_BASE_COOLDOWN = 15.0
BREAKER_MAX_COOLDOWN = 300.0


@dataclass(slots=True)
class _Breaker:
    trips: int = 0
    open_until: float = 0.0
    half_open: bool = False

    def is_open(self, now: float) -> bool:
        return self.open_until > now

    def cooldown(self) -> float:
        return min(BREAKER_BASE_COOLDOWN * (2 ** max(0, self.trips - 1)),
                   BREAKER_MAX_COOLDOWN)


class ProviderRegistry:
    """Ordered, health-aware collection of `HistoryAdapter`s. Thread-safe."""

    def __init__(self, adapters: list[HistoryAdapter] | None = None):
        self._lock = threading.Lock()
        self._adapters: list[HistoryAdapter] = []
        self._breakers: dict[str, _Breaker] = {}
        for adapter in adapters or []:
            self.register(adapter)

    # ── membership ───────────────────────────────────────────────────────────

    def register(self, adapter: HistoryAdapter) -> None:
        with self._lock:
            if any(a.provider_name == adapter.provider_name for a in self._adapters):
                raise ValueError(
                    f"provider {adapter.provider_name!r} is already registered")
            self._adapters.append(adapter)
            self._adapters.sort(key=lambda a: a.provider_priority)
            self._breakers.setdefault(adapter.provider_name, _Breaker())

    def unregister(self, name: str) -> None:
        with self._lock:
            self._adapters = [a for a in self._adapters if a.provider_name != name]
            self._breakers.pop(name, None)

    def get(self, name: str) -> HistoryAdapter | None:
        with self._lock:
            for adapter in self._adapters:
                if adapter.provider_name == name:
                    return adapter
        return None

    @property
    def adapters(self) -> list[HistoryAdapter]:
        with self._lock:
            return list(self._adapters)

    def __len__(self) -> int:
        with self._lock:
            return len(self._adapters)

    # ── selection ────────────────────────────────────────────────────────────

    def candidates(self, symbol: str, timeframe: Timeframe, *,
                   extended_hours: bool = False,
                   end: datetime | None = None,
                   now: datetime | None = None,
                   include_open_breakers: bool = False) -> list[HistoryAdapter]:
        """Adapters that could serve this request, best first.

        When `end` and `now` are both given, a provider whose depth floor is at
        or past `end` — i.e. the ENTIRE requested window predates anything it
        can serve — is filtered out. That is the check that stops a scroll into
        last spring's 5-minute bars from burning one guaranteed-422 request per
        provider, per scroll.
        """
        mono = _time.monotonic()
        out: list[HistoryAdapter] = []
        for adapter in self.adapters:
            if not adapter.supports_interval(timeframe):
                continue
            if not adapter.supports_symbol(symbol):
                continue
            if end is not None and now is not None:
                earliest = adapter.earliest(timeframe, now)
                if earliest is not None and end <= earliest:
                    continue
            if not include_open_breakers and not self._allow(adapter, mono):
                continue
            out.append(adapter)
        if extended_hours:
            # An RTH-only provider is still a useful fallback for the same
            # window, so it is deprioritised rather than dropped.
            out.sort(key=lambda a: (not a.supports_extended_hours(timeframe),
                                    a.provider_priority))
        return out

    def deepest_earliest(self, symbol: str, timeframe: Timeframe,
                         now: datetime) -> datetime | None:
        """Oldest bar time ANY registered provider could serve for `timeframe`.

        None means at least one provider has unlimited depth. This is the value
        the chart uses to say "start of available history" truthfully rather
        than retrying a window nothing can serve.
        """
        floors: list[datetime] = []
        for adapter in self.adapters:
            if not adapter.supports_interval(timeframe):
                continue
            if not adapter.supports_symbol(symbol):
                continue
            earliest = adapter.earliest(timeframe, now)
            if earliest is None:
                return None
            floors.append(earliest)
        return min(floors) if floors else None

    # ── breaker ──────────────────────────────────────────────────────────────

    def _allow(self, adapter: HistoryAdapter, now: float) -> bool:
        health = adapter.health()
        if health.rate_limited_until and health.rate_limited_until > now:
            return False
        with self._lock:
            breaker = self._breakers.setdefault(adapter.provider_name, _Breaker())
            if not breaker.is_open(now):
                return True
            return False

    def record_success(self, adapter: HistoryAdapter) -> None:
        with self._lock:
            breaker = self._breakers.setdefault(adapter.provider_name, _Breaker())
            if breaker.trips or breaker.half_open:
                log.info("provider %s recovered — closing circuit breaker",
                         adapter.provider_name)
            breaker.trips = 0
            breaker.open_until = 0.0
            breaker.half_open = False

    def record_failure(self, adapter: HistoryAdapter) -> None:
        """Count a failure; trip the breaker once the threshold is reached.

        Only failures that say something about the provider's *health* should
        reach here — a range or symbol error is a correct answer and is not
        counted (the service enforces that; this stays a dumb counter so the
        policy lives in one place).
        """
        health = adapter.health()
        with self._lock:
            breaker = self._breakers.setdefault(adapter.provider_name, _Breaker())
            if health.consecutive_failures >= BREAKER_THRESHOLD:
                breaker.trips += 1
                breaker.open_until = _time.monotonic() + breaker.cooldown()
                breaker.half_open = False
                log.warning("provider %s tripped its circuit breaker for %.0fs "
                            "(%d consecutive failures, last: %s)",
                            adapter.provider_name, breaker.cooldown(),
                            health.consecutive_failures, health.last_error)

    def half_open_candidates(self) -> list[HistoryAdapter]:
        """Adapters whose cooldown has just expired — each gets exactly one
        probe request. Called by the service when every closed provider failed,
        so a total outage still self-heals without a restart."""
        now = _time.monotonic()
        out = []
        with self._lock:
            for adapter in self._adapters:
                breaker = self._breakers.get(adapter.provider_name)
                if breaker and breaker.open_until and not breaker.is_open(now) \
                        and not breaker.half_open:
                    breaker.half_open = True
                    out.append(adapter)
        return out

    def force_open(self, name: str, seconds: float) -> None:
        """Test/ops hook: take a provider out of rotation immediately."""
        with self._lock:
            breaker = self._breakers.setdefault(name, _Breaker())
            breaker.trips += 1
            breaker.open_until = _time.monotonic() + seconds

    def reset(self) -> None:
        with self._lock:
            for breaker in self._breakers.values():
                breaker.trips = 0
                breaker.open_until = 0.0
                breaker.half_open = False

    # ── observability ────────────────────────────────────────────────────────

    def health_report(self) -> list[dict]:
        now = _time.monotonic()
        report = []
        for adapter in self.adapters:
            entry = adapter.health().as_dict(now)
            with self._lock:
                breaker = self._breakers.get(adapter.provider_name, _Breaker())
                entry["circuit_open_for"] = (round(breaker.open_until - now, 1)
                                             if breaker.is_open(now) else None)
                entry["breaker_trips"] = breaker.trips
            entry["priority"] = adapter.provider_priority
            entry["rate_limit"] = adapter.rate_limit_state
            entry["intervals"] = sorted(
                str(tf) for tf in adapter.capabilities.intervals)
            report.append(entry)
        return report


def default_registry(*, include_stooq: bool = True) -> ProviderRegistry:
    """The shipped provider chain: Yahoo JSON -> yfinance -> Stooq.

    Ordering rationale (see `docs/MARKET_DATA.md` §5): the primary is the
    fastest source that reports its own limits; the secondary reaches the same
    data by an independent code path; the tertiary is the only source that does
    not depend on Yahoo at all, which is what makes a Yahoo-wide outage
    survivable for daily charts.
    """
    from optionspilot.data.stooq_provider import StooqAdapter
    from optionspilot.data.yahoo_provider import YahooChartAdapter
    from optionspilot.data.yfinance_adapter import YFinanceAdapter

    adapters: list[HistoryAdapter] = [YahooChartAdapter(), YFinanceAdapter()]
    if include_stooq:
        adapters.append(StooqAdapter())
    return ProviderRegistry(adapters)


__all__ = ["ProviderRegistry", "default_registry", "BREAKER_THRESHOLD",
           "BREAKER_BASE_COOLDOWN", "BREAKER_MAX_COOLDOWN"]
