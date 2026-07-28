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
from optionspilot.data.config import (
    ORDER_DYNAMIC, ORDER_STATIC, MarketDataConfig,
)
from optionspilot.data.credentials import CredentialStore
from optionspilot.data.health import DEFAULT_BREAKER
from optionspilot.data.ratelimit import QuotaStore

#: Kept as the shipped defaults' public names — the values now live on
#: `health.BreakerPolicy`, which per-provider configuration can override.
BREAKER_THRESHOLD = DEFAULT_BREAKER.threshold
BREAKER_BASE_COOLDOWN = DEFAULT_BREAKER.base_cooldown
BREAKER_MAX_COOLDOWN = DEFAULT_BREAKER.max_cooldown

log = get_logger("data")


class ProviderRegistry:
    """Ordered, health-aware collection of `HistoryAdapter`s. Thread-safe."""

    def __init__(self, adapters: list[HistoryAdapter] | None = None, *,
                 config: MarketDataConfig | None = None,
                 credentials: CredentialStore | None = None):
        self._lock = threading.Lock()
        self._adapters: list[HistoryAdapter] = []
        self.config = config or MarketDataConfig()
        #: The store `default_registry` resolved keys from, kept so a live key
        #: change has one place to write to and one place to re-read from. A
        #: registry built by hand (tests, scripts) gets an in-memory store, so
        #: this is never None and no caller needs a null check.
        self.credentials = credentials if credentials is not None else \
            CredentialStore(self.config.credentials_path)
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
        """Lower is better. What "better" means is the ordering mode.

        The three modes and why they exist are documented once, in
        `data/config.py`'s ORDER_* block. This is the only place that reads
        them, so the vocabulary cannot drift into a second interpretation.
        """
        mode = self.config.ordering()
        if mode == ORDER_STATIC:
            return float(adapter.provider_priority)
        return adapter.monitor.rank(include_latency=(mode == ORDER_DYNAMIC))

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
                 "available": a.monitor.available(),
                 "mode": self.config.ordering()}
                for a in adapters]
        rows.sort(key=lambda r: r["rank"])
        for position, row in enumerate(rows, start=1):
            row["position"] = position
        return rows

    # ── live control (V0.5.7) ────────────────────────────────────────────────
    #
    # The registry owns the ADAPTER LIST, so it owns applying a settings change
    # to it. `MarketDataControl` decides what the change should be and persists
    # it; these methods are the mechanism, and they exist here so that nothing
    # outside this module has to reach into `self._adapters`.

    def apply_config(self, config: MarketDataConfig, *,
                     credentials: CredentialStore | None = None) -> None:
        """Re-apply `config` to the adapters already constructed.

        Used when a setting changes at runtime. Deliberately does NOT rebuild
        the registry: reconstructing the adapters would throw away every
        counter, latency sample and breaker state the session has accumulated,
        so changing one provider's priority would erase the evidence for why
        the other five are ranked where they are.
        """
        with self._lock:
            self.config = config
            adapters = list(self._adapters)
        for adapter in adapters:
            provider = config.for_provider(adapter.provider_name)
            adapter.set_enabled(provider.enabled)
            if provider.priority is not None:
                adapter.set_priority(provider.priority)
            if credentials is not None and adapter.requires_api_key:
                adapter.set_api_key(credentials.resolve(adapter.provider_name)
                                    or provider.api_key)
        with self._lock:
            self._adapters.sort(key=lambda a: a.provider_priority)

    def order(self) -> list[str]:
        """Provider names in configured-priority order, best first.

        This is the order the settings page shows and reorders — the STATIC
        anchor, not the live rank. Showing the live rank in a list with Move
        Up / Move Down buttons would let a latency change reorder the list
        under the user's cursor between reading it and clicking it.
        """
        return [a.provider_name
                for a in sorted(self.adapters, key=lambda a: a.provider_priority)]

    def reorder(self, names: list[str]) -> list[str]:
        """Set the configured order from a list of provider names, best first.

        Priorities are rewritten as 10, 20, 30… rather than 1, 2, 3: the rank
        formula is calibrated so that **10 rank points is one second of
        latency** (`health.LATENCY_MS_PER_RANK_POINT`), so consecutive
        priorities would mean a provider 100ms slower than its neighbour
        outranked it — dynamic ordering would become almost-static, silently,
        the first time a user pressed Move Up.

        Unknown names are ignored and registered providers the caller omitted
        keep their relative order at the end, so a stale list from an older
        build (or a downgrade) reorders what it can and breaks nothing.

        **Repeats are collapsed to their first occurrence.** A list naming the
        same provider twice would otherwise be assigned two priorities — the
        last winning — and `order()` would report a chain longer than the
        registry, with one provider appearing several times in the settings
        page. There is no legitimate way to type that in the UI, but this
        method also parses a JSON file a user can edit by hand.
        """
        wanted: list[str] = []
        for name in names:
            if self.get(name) is not None and name not in wanted:
                wanted.append(name)
        seen = set(wanted)
        tail = [a.provider_name for a in
                sorted(self.adapters, key=lambda a: a.provider_priority)
                if a.provider_name not in seen]
        final = wanted + tail
        for position, name in enumerate(final, start=1):
            adapter = self.get(name)
            if adapter is not None:
                adapter.set_priority(position * 10)
        with self._lock:
            self._adapters.sort(key=lambda a: a.provider_priority)
        return final

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Switch one provider on or off. Returns True when it exists."""
        adapter = self.get(name)
        if adapter is None:
            return False
        adapter.set_enabled(enabled)
        log.info("market-data provider %s %s from settings", name,
                 "enabled" if enabled else "disabled")
        return True

    def healthiest(self, symbol: str, timeframe: Timeframe, *,
                   now: datetime | None = None) -> HistoryAdapter | None:
        """The provider that should answer this request right now, or None when
        nothing is eligible."""
        eligible = self.candidates(symbol, timeframe, now=now)
        return eligible[0] if eligible else None

    def deepest_earliest(self, symbol: str, timeframe: Timeframe,
                         now: datetime) -> datetime | None:
        """Oldest bar time any USABLE provider could serve for `timeframe`.

        None means at least one such provider has unlimited depth. This is the
        value the chart uses to say "start of available history" truthfully
        rather than retrying a window nothing can serve.

        **Permanently-unusable providers are excluded, temporarily-unavailable
        ones are not.** The distinction is load-bearing. A keyed provider with
        no API key declares deep intraday history it can never actually serve;
        counting it would tell the chart that 5-minute bars reach back 180 days
        when the only reachable source stops at 59 — and the chart would then
        scroll into a window nothing can answer and retry it forever. That is
        precisely the bug class V0.5.2 was built to eliminate, so a provider
        that cannot be used *at all* in this install contributes no floor.

        A provider that is merely rate-limited, over quota, or behind an open
        breaker DOES contribute: it will be back, and the true start of history
        should not lurch about as breakers open and close.

        "Permanently unusable" is `monitor.permanently_unusable`, not just
        `disabled_reason`: a **rejected key** and an **insufficient plan** are
        equally permanent until the user acts, and both were being counted.
        Finnhub on a free plan is the live example — it declares 180 days of
        5-minute history and, since Finnhub moved candles behind a paid tier,
        can serve none of it.
        """
        floors: list[datetime] = []
        for adapter in self.adapters:
            if adapter.monitor.permanently_unusable:
                continue
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
            # The capability profile the dashboard shows, so a user can see
            # what each provider actually IS rather than only how it is doing.
            entry["asset_classes"] = list(adapter.capabilities.asset_classes)
            entry["realtime"] = adapter.capabilities.realtime
            entry["description"] = adapter.capabilities.description
            entry["signup_url"] = adapter.signup_url
            # API keys are REDACTED here — this payload reaches the diagnostics
            # endpoint, the JSON export and the text report, all of which users
            # are invited to attach to public bug reports.
            entry["config"] = adapter.config.as_dict()
            report.append(entry)
        report.sort(key=lambda r: r["rank"])
        return report


def default_registry(*, include_stooq: bool = True,
                     config: MarketDataConfig | None = None,
                     environ: dict | None = None,
                     credentials: CredentialStore | None = None) -> ProviderRegistry:
    """The shipped provider chain, keyless first then keyed.

        Yahoo JSON (10) -> yfinance (20) -> Stooq (30)
            -> Finnhub (40) -> Twelve Data (50) -> Alpha Vantage (60)

    Ordering rationale (see `docs/MARKET_DATA.md` §5 and §23): the primary is
    the fastest source that reports its own limits; the secondary reaches the
    same data by an independent code path; the tertiary is the only source that
    does not depend on Yahoo at all. The keyed providers sit behind them
    because they cost a metered request where the keyless ones cost nothing —
    but they are *genuinely independent* of Yahoo, which is what makes a
    Yahoo-wide outage survivable at intraday resolution for the first time.
    Among themselves they are ordered by how much budget they have to spend:
    Finnhub (60/min, no daily cap), Twelve Data (800/day), Alpha Vantage
    (25/day). All of that is the *starting* rank; health and budget move it.

    **A keyed provider with no key disables itself, quietly.** It is still
    constructed and still appears in diagnostics — reporting `missing_api_key`
    and where to get one — so the page can explain its absence. It is simply
    never selected. The application must start and serve charts with zero keys
    configured, which is the shipped default.

    `config` disables providers and overrides timeouts, retries, breaker
    thresholds, budgets and credentials without touching adapter code.
    """
    from optionspilot.data.alphavantage_provider import AlphaVantageAdapter
    from optionspilot.data.finnhub_provider import FinnhubAdapter
    from optionspilot.data.stooq_provider import StooqAdapter
    from optionspilot.data.twelvedata_provider import TwelveDataAdapter
    from optionspilot.data.yahoo_provider import YahooChartAdapter
    from optionspilot.data.yfinance_adapter import YFinanceAdapter

    config = config or MarketDataConfig()
    classes = [YahooChartAdapter, YFinanceAdapter]
    if include_stooq:
        classes.append(StooqAdapter)
    classes += [FinnhubAdapter, TwelveDataAdapter, AlphaVantageAdapter]

    # One store shared by every metered provider, so a restart cannot mint an
    # allowance the plan has not granted. Best-effort: an unusable path costs
    # persistence, never startup.
    quota_store = None
    if config.quota_state_path:
        try:
            quota_store = QuotaStore(config.quota_state_path)
        except Exception as exc:  # noqa: BLE001 — never fail launch over this
            log.warning("quota state at %s is unusable (%s) — counting in "
                        "memory only", config.quota_state_path, exc)

    # Keys pasted into Settings, merged into each provider's config. The
    # environment still wins: `overlay` fills in `ProviderConfig.api_key`, and
    # `resolve_api_key` consults the environment BEFORE that field — so the
    # documented precedence needs no second implementation here. See
    # `data/credentials.py`.
    credentials = (credentials if credentials is not None
                   else CredentialStore(config.credentials_path))
    config = credentials.overlay(config)

    adapters: list[HistoryAdapter] = []
    for cls in classes:
        provider_config = config.for_provider(cls.provider_name)
        # `enabled: false` used to skip construction entirely. It no longer
        # does, and the difference is the whole reason the control centre can
        # exist: a provider that is not constructed cannot be listed, cannot
        # explain itself, and cannot be switched back on without editing a file
        # and restarting. A disabled provider is now built exactly like one
        # with a missing API key — present, reporting `disabled`, and never
        # selected (`monitor.available()` is False, so `candidates` skips it
        # and `deepest_earliest` counts no floor for it).
        if not provider_config.enabled:
            log.info("market-data provider %s is disabled by configuration",
                     cls.provider_name)
        try:
            adapter = cls(provider_config, quota_store=quota_store,
                          environ=environ)
        except Exception as exc:  # noqa: BLE001 — one bad adapter must not
            log.error("market-data provider %s could not be constructed "  # kill
                      "(%s) — continuing without it", cls.provider_name, exc)
            continue
        if adapter.monitor.disabled_reason:
            status, detail = adapter.monitor.status()
            log.info("market-data provider %s is unavailable: %s%s",
                     cls.provider_name, detail,
                     f" — get a key at {adapter.signup_url}"
                     if status == "missing_api_key" and adapter.signup_url else "")
        adapters.append(adapter)
    registry = ProviderRegistry(adapters, config=config,
                                credentials=credentials)
    # A user-chosen order is applied AFTER construction rather than folded into
    # each adapter's config, so there is one implementation of "what does this
    # list of names mean" (`reorder`) shared by startup and by the settings
    # page. An order saved against a build that had more providers than this
    # one reorders what it recognises and ignores the rest.
    if config.provider_order:
        registry.reorder(list(config.provider_order))
    return registry


__all__ = ["ProviderRegistry", "default_registry", "BREAKER_THRESHOLD",
           "BREAKER_BASE_COOLDOWN", "BREAKER_MAX_COOLDOWN"]
