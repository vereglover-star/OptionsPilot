"""What a market-data provider can actually serve.

The single most damaging class of chart bug in this app has been asking a
provider for data it structurally cannot return, and then treating the empty
response as a transient failure. Yahoo, for example, refuses any 5-minute
request whose start is more than ~60 days before *now* — it answers with an
HTTP 422 whose body reads "The requested range must be within the last 60
days". The old clamp measured that window from the *requested end* instead of
from now, so every history-paging request into older intraday data sailed
through the clamp, came back empty, and the UI retried it forever.

A `ProviderCapabilities` value states those limits declaratively so the service
layer can answer three questions *before* spending a network request:

  - can this provider serve this interval at all?          `supports_interval`
  - what is the oldest bar it can serve right now?         `earliest(tf, now)`
  - is the caller's window entirely outside that?          `window_for(...)`

The third answer is what lets the chart say "◄ Start of available history"
honestly instead of spinning. Capabilities are per-provider data, not
per-provider code, so adding a provider with deeper history is a table entry.

Intervals a provider doesn't serve natively are declared with a `resample`
rule: `Timeframe.H4 -> ("1h", "4h")` means "fetch 1h, resample to 4h". The
adapter base class applies it, so no consumer ever learns which intervals were
native.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from optionspilot.core.models import Timeframe

# Every timeframe the app can chart. A provider may support a subset.
ALL_TIMEFRAMES: tuple[Timeframe, ...] = tuple(Timeframe)


@dataclass(frozen=True, slots=True)
class IntervalSpec:
    """How one app timeframe maps onto one provider's native intervals.

    `native` is the provider's own interval string. `resample` is a pandas
    offset alias when the app timeframe must be aggregated up from `native`
    (Yahoo has no 3m/10m/2h/4h), else None. `max_lookback_days` is how far
    before *now* the provider will serve this interval — None means unlimited.
    """

    native: str
    resample: str | None = None
    max_lookback_days: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """The declarative contract a provider advertises about itself."""

    intervals: dict[Timeframe, IntervalSpec]
    extended_hours: bool = False
    #: Provider serves adjusted-for-splits, unadjusted-for-dividends bars
    #: (the app's convention — see `quality.ADJUSTMENT_CONVENTION`).
    split_adjusted: bool = True
    dividend_adjusted: bool = False
    #: Best-effort ceiling on requests per minute; the registry paces to it.
    requests_per_minute: int | None = None
    #: Symbols the provider is known NOT to serve (e.g. indices on a stock-only
    #: feed). Empty means "assume anything until proven otherwise".
    unsupported_symbols: frozenset[str] = field(default_factory=frozenset)

    # ── queries ──────────────────────────────────────────────────────────────

    def supports_interval(self, tf: Timeframe) -> bool:
        return tf in self.intervals

    def supports_symbol(self, symbol: str) -> bool:
        return symbol.upper() not in self.unsupported_symbols

    def supports_extended_hours(self, tf: Timeframe) -> bool:
        # Daily+ bars are RTH aggregates upstream everywhere, so extended hours
        # is meaningless there regardless of what the provider advertises.
        return self.extended_hours and tf.minutes < Timeframe.D1.minutes

    def spec(self, tf: Timeframe) -> IntervalSpec | None:
        return self.intervals.get(tf)

    def max_lookback_days(self, tf: Timeframe) -> int | None:
        spec = self.intervals.get(tf)
        return spec.max_lookback_days if spec else None

    def earliest(self, tf: Timeframe, now: datetime) -> datetime | None:
        """Oldest bar open time this provider can serve for `tf` right now, or
        None when it has no depth limit. Measured from `now` — NOT from the
        request's end — because that is how the upstream limit actually works.
        """
        days = self.max_lookback_days(tf)
        if days is None:
            return None
        return now - timedelta(days=days)

    def window_for(self, tf: Timeframe, start: datetime, end: datetime,
                   now: datetime) -> tuple[datetime, datetime] | None:
        """Clamp `[start, end)` into what this provider can serve.

        Returns None when the request is entirely outside the provider's depth
        — the caller must NOT spend a request on it, and (if no other provider
        can go deeper) the chart has genuinely reached the start of history.
        """
        floor = self.earliest(tf, now)
        if floor is not None:
            if end <= floor:
                return None
            if start < floor:
                start = floor
        return (start, end) if start < end else None


def _intraday(native: str, days: int, resample: str | None = None) -> IntervalSpec:
    return IntervalSpec(native=native, resample=resample, max_lookback_days=days)


# ── Yahoo Finance (both the JSON chart API and yfinance sit on this feed) ────
#
# Depth limits below are MEASURED, not assumed: `scripts/marketdata_probe.py`
# walks each interval back day by day until Yahoo 422s, and Yahoo states the
# limit in the error body ("Only 8 days worth of 1m granularity data are
# allowed"; "must be within the last 60 days"; 1h dies at 730). We sit one day
# inside each measured cliff so a request built a moment before midnight UTC
# can't land on the far side of it.
YAHOO_INTERVALS: dict[Timeframe, IntervalSpec] = {
    Timeframe.M1:  _intraday("1m", 7),
    Timeframe.M2:  _intraday("2m", 59),
    Timeframe.M3:  _intraday("1m", 7, resample="3min"),
    Timeframe.M5:  _intraday("5m", 59),
    Timeframe.M10: _intraday("5m", 59, resample="10min"),
    Timeframe.M15: _intraday("15m", 59),
    Timeframe.M30: _intraday("30m", 59),
    Timeframe.H1:  _intraday("1h", 729),
    Timeframe.H2:  _intraday("1h", 729, resample="2h"),
    Timeframe.H4:  _intraday("1h", 729, resample="4h"),
    Timeframe.D1:  IntervalSpec("1d"),
    Timeframe.W1:  IntervalSpec("1wk"),
    Timeframe.MN1: IntervalSpec("1mo"),
}

YAHOO_CAPABILITIES = ProviderCapabilities(
    intervals=YAHOO_INTERVALS,
    extended_hours=True,
    requests_per_minute=180,
)

# ── Stooq (free CSV, no key) — daily and coarser only, but decades deep ──────
STOOQ_CAPABILITIES = ProviderCapabilities(
    intervals={
        Timeframe.D1:  IntervalSpec("d"),
        Timeframe.W1:  IntervalSpec("w"),
        Timeframe.MN1: IntervalSpec("m"),
    },
    extended_hours=False,
    requests_per_minute=60,
)


__all__ = [
    "ALL_TIMEFRAMES", "IntervalSpec", "ProviderCapabilities",
    "YAHOO_INTERVALS", "YAHOO_CAPABILITIES", "STOOQ_CAPABILITIES",
]
