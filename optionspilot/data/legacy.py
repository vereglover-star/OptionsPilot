"""Adapter that lets any plain `MarketDataProvider` join the provider chain.

The `MarketDataProvider` ABC predates the adapter architecture and is what the
backtester's fixtures, the test suite's fakes, and any externally-supplied
provider still implement. Rather than force every one of them to be rewritten
— or, worse, keep a second history code path alive for them — this wraps one in
a `HistoryAdapter` so it walks exactly the same validation, caching, fallback
and diagnostics ladder as a first-class provider.

Its capabilities are deliberately permissive (every timeframe, no depth limit),
because a plain provider tells us nothing about its own limits. That means the
service will not pre-emptively skip a request it cannot serve, and a genuinely
out-of-range window comes back as an empty frame instead of a typed range
error — the pre-existing behaviour for those providers, preserved on purpose.
A provider that knows its own limits should implement `HistoryAdapter`
directly and declare them.
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pandas as pd

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderUnavailable, Snapshot,
)
from optionspilot.data.base import MarketDataProvider
from optionspilot.data.capabilities import IntervalSpec, ProviderCapabilities

#: No depth limits and no resampling: a plain provider is assumed to handle its
#: own interval mapping internally (the pre-adapter `YFinanceProvider` did).
LEGACY_CAPABILITIES = ProviderCapabilities(
    intervals={tf: IntervalSpec(native=str(tf)) for tf in Timeframe},
    extended_hours=True,
)


class LegacyProviderAdapter(HistoryAdapter):
    """Wraps a `MarketDataProvider` as a history adapter."""

    provider_priority = 50

    def __init__(self, inner: MarketDataProvider, config=None):
        self._inner = inner
        self.provider_name = getattr(inner, "name", None) or type(inner).__name__
        self.capabilities = LEGACY_CAPABILITIES
        # Whether the inner provider accepts the display-only extended-hours
        # kwarg. Plain 4-argument providers (test fakes, older adapters) must
        # keep working, so we only pass it when the signature has it.
        self._takes_extended = _accepts_extended_hours(inner.get_candles)
        super().__init__(config)

    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        timeframe = Timeframe.from_string(spec.native)
        if prepost and self._takes_extended:
            return self._inner.get_candles(symbol, timeframe, start, end,
                                           extended_hours=True)
        return self._inner.get_candles(symbol, timeframe, start, end)

    def _fetch_snapshot_impl(self, symbol: str) -> Snapshot:
        try:
            quote = self._inner.get_quote(symbol)
        except Exception as exc:  # noqa: BLE001 — surface as a provider failure
            raise ProviderUnavailable(
                f"{self.provider_name} snapshot failed: {exc}") from exc
        return Snapshot(symbol=symbol.upper(), last=float(quote.last))

    def _probe(self) -> None:
        # A plain provider has no cheap health endpoint; treating construction
        # as success keeps `connect()` honest rather than inventing a request.
        return None


def _accepts_extended_hours(func) -> bool:
    try:
        return "extended_hours" in inspect.signature(func).parameters
    except (TypeError, ValueError):  # pragma: no cover — C-implemented callables
        return False


__all__ = ["LegacyProviderAdapter", "LEGACY_CAPABILITIES"]
