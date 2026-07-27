"""Provider registry — who to ask, in what order, and when to stop asking.

The registry owns three decisions the service must not re-derive at each call
site:

**Ordering.** Adapters are tried best-first. What "best" means is the one thing
V0.5.3 changed here: it used to be the static `provider_priority` alone, and it
is now `ProviderHealthMonitor.rank()` — priority as the anchor, moved by
measured latency, failure rate and data quality (the formula and its scale are
documented in `data/health.py`). A cold system therefore produces exactly the
old fixed order, and a Yahoo that has degraded to 2.4-second responses loses to
a Stooq answering in 260ms without anyone editing a priority. Set
`MarketDataConfig.dynamic_ranking = False` to pin the static order.

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

The breaker *state* no longer lives here. It lives on each adapter's
`ProviderHealthMonitor`, next to the counters that decide when it should trip —
the registry used to hold a `_Breaker` whose trip condition was a read of the
adapter's failure counter, which is one invariant spread across two objects.
The registry now expresses policy ("this attempt counted against the provider")
and the monitor owns mechanism.

Rate-limit state is honoured as a form of breaker: a provider that told us to
back off is skipped until its window expires, no matter how healthy it looks.
"""

from __future__ import annotations

import threading
from datetime import datetime

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import HistoryAdapter
from optionspilot.data.config import MarketDataConfig
from optionspilot.data.health import DEFAULT_BREAKER

#: Kept as the shipped defaults' public names — the values now live on
#: `health.BreakerPolicy`, which per-provider configuration can override.
BREAKER_THRESHOLD = DEFAULT_BREAKER.threshold
BREAKER_BASE_COOLDOWN = DEFAULT_BREAKER.base_cooldown
BREAKER_MAX_COOLDOWN = DEFAULT_BREAKER.max_cooldown

log = get_logger("data")


class ProviderRegistry:
    """Ordered, health-aware collection of `HistoryAdapter`s. Thread-safe."""

    def __init__(self, adapters: list[HistoryAdapter] | None = None, *,
                 config: MarketDataConfig | None = None):
        self._lock = threading.Lock()
        self._adapters: list[HistoryAdapter] = []
        self.config = config or MarketDataConfig()
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

    def unregister(self, name: str) -> None:
        with self._lock:
            self._adapters = [a for a in self._adapters if a.provider_name != name]

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

        Ordering is by health rank (see the module docstring); with no traffic
        recorded yet every rank equals the provider's priority, so the shipped
        chain starts in its documented order.
        """
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
            if not include_open_breakers and not adapter.monitor.available():
                continue
            out.append(adapter)
        # An RTH-only provider is still a useful fallback for the same window,
        # so extended hours deprioritises it rather than dropping it.
        out.sort(key=lambda a: (
            (not a.supports_extended_hours(timeframe)) if extended_hours else False,
            self._rank(a)))
        return out

    def _rank(self, adapter: HistoryAdapter) -> float:
        """Lower is better. Falls back to the static priority when dynamic
        ranking is switched off, which reproduces the pre-V0.5.3 order."""
        if not self.config.dynamic_ranking:
            return float(adapter.provider_priority)
        return adapter.monitor.rank()

    def ranking(self, symbol: str = "SPY",
                timeframe: Timeframe | None = None) -> list[dict]:
        """The current ranking, with each provider's score — what the
        diagnostics dashboard shows under "current priority"."""
        adapters = self.adapters
        if timeframe is not None:
            adapters = [a for a in adapters if a.supports_interval(timeframe)
                        and a.supports_symbol(symbol)]
        rows = [{"name": a.provider_name,
                 "priority": a.provider_priority,
                 "rank": round(self._rank(a), 2),
                 "available": a.monitor.available()}
                for a in adapters]
        rows.sort(key=lambda r: r["rank"])
        for position, row in enumerate(rows, start=1):
            row["position"] = position
        return rows

    def healthiest(self, symbol: str, timeframe: Timeframe, *,
                   now: datetime | None = None) -> HistoryAdapter | None:
        """The provider that should answer this request right now, or None when
        nothing is eligible."""
        eligible = self.candidates(symbol, timeframe, now=now)
        return eligible[0] if eligible else None

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

    def record_success(self, adapter: HistoryAdapter) -> None:
        """The provider answered usefully — it is back in rotation.

        Breaker state only: the adapter already recorded the request when it
        fetched, and counting it a second time here would double every
        provider's request total. The success is only *confirmed* at this level
        — a frame that failed validation never gets here.
        """
        adapter.monitor.close_breaker()

    def record_failure(self, adapter: HistoryAdapter) -> None:
        """This attempt counted against the provider — re-evaluate the breaker.

        Only failures that say something about the provider's *health* should
        reach here; a range or symbol error is a correct answer to an
        impossible question. That policy lives in `health.COUNTS_AGAINST_HEALTH`
        and the counters themselves live on the monitor, so this is the seam
        between "the service judged this the provider's fault" and "the breaker
        decides what to do about it".
        """
        adapter.monitor.evaluate_breaker()

    def half_open_candidates(self) -> list[HistoryAdapter]:
        """Adapters whose cooldown has just expired — each gets exactly one
        probe request. Called by the service when every closed provider failed,
        so a total outage still self-heals without a restart."""
        return [a for a in self.adapters if a.monitor.take_half_open_probe()]

    def force_open(self, name: str, seconds: float) -> None:
        """Test/ops hook: take a provider out of rotation immediately."""
        adapter = self.get(name)
        if adapter is not None:
            adapter.monitor.force_open(seconds)

    def reset(self) -> None:
        for adapter in self.adapters:
            adapter.monitor.reset()

    # ── observability ────────────────────────────────────────────────────────

    def health_report(self) -> list[dict]:
        """One row per provider: counters, latency, breaker, rank, capabilities.

        This is the dashboard's data source, so it carries everything the page
        shows and nothing it has to compute.
        """
        report = []
        for adapter in self.adapters:
            entry = adapter.monitor.snapshot()
            entry["rate_limit"] = adapter.rate_limit_state
            entry["intervals"] = sorted(
                str(tf) for tf in adapter.capabilities.intervals)
            entry["extended_hours"] = adapter.capabilities.extended_hours
            entry["max_lookback_days"] = {
                str(tf): spec.max_lookback_days
                for tf, spec in adapter.capabilities.intervals.items()}
            entry["config"] = adapter.config.as_dict()
            report.append(entry)
        report.sort(key=lambda r: r["rank"])
        return report


def default_registry(*, include_stooq: bool = True,
                     config: MarketDataConfig | None = None) -> ProviderRegistry:
    """The shipped provider chain: Yahoo JSON -> yfinance -> Stooq.

    Ordering rationale (see `docs/MARKET_DATA.md` §5): the primary is the
    fastest source that reports its own limits; the secondary reaches the same
    data by an independent code path; the tertiary is the only source that does
    not depend on Yahoo at all, which is what makes a Yahoo-wide outage
    survivable for daily charts. That ordering is the *starting* rank; health
    moves it from there.

    `config` disables providers and overrides their timeouts, retries and
    breaker thresholds without touching adapter code — the point of
    `data/config.py`.
    """
    from optionspilot.data.stooq_provider import StooqAdapter
    from optionspilot.data.yahoo_provider import YahooChartAdapter
    from optionspilot.data.yfinance_adapter import YFinanceAdapter

    config = config or MarketDataConfig()
    classes = [YahooChartAdapter, YFinanceAdapter]
    if include_stooq:
        classes.append(StooqAdapter)
    adapters: list[HistoryAdapter] = []
    for cls in classes:
        provider_config = config.for_provider(cls.provider_name)
        if not provider_config.enabled:
            log.info("market-data provider %s is disabled by configuration",
                     cls.provider_name)
            continue
        adapters.append(cls(provider_config))
    return ProviderRegistry(adapters, config=config)


__all__ = ["ProviderRegistry", "default_registry", "BREAKER_THRESHOLD",
           "BREAKER_BASE_COOLDOWN", "BREAKER_MAX_COOLDOWN"]
