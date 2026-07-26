"""Market data subsystem.

Layers, top to bottom (full design in `docs/MARKET_DATA.md`):

    CachedProvider      MarketDataProvider facade the rest of the app speaks
    MarketDataService   the tier ladder: memo -> cache -> providers -> stale
    ProviderRegistry    ordering, eligibility, circuit breaking
    HistoryAdapter      one normalized shape per source (Yahoo, yfinance, Stooq)
    CandleCache         durable, integrity-checked local history
    quality             semantic validation; nothing unvalidated reaches a chart
    diagnostics         one trace per request, for after-the-fact explanation

`build_provider()` is the composition root — the one place the shipped chain is
assembled — so nothing else has to know which providers exist or in what order.

Imports here are kept to the light modules only: `yfinance` costs ~0.3s and is
loaded lazily by its adapter on first use, so importing this package does not
slow app startup or any CLI command that never fetches data.
"""

from pathlib import Path

from optionspilot.data.adapter import (
    HistoryAdapter, HistoryRequest, ProviderError, ProviderRangeError,
    ProviderRateLimited, ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.base import MarketDataProvider
from optionspilot.data.cache import CandleCache
from optionspilot.data.cached import CachedProvider
from optionspilot.data.capabilities import ProviderCapabilities
from optionspilot.data.diagnostics import Diagnostics
from optionspilot.data.legacy import LegacyProviderAdapter
from optionspilot.data.quality import HistoryReport, validate_history
from optionspilot.data.registry import ProviderRegistry, default_registry
from optionspilot.data.service import HistoryResult, MarketDataService
from optionspilot.data.stooq_provider import StooqAdapter
from optionspilot.data.yahoo_provider import YahooChartAdapter
from optionspilot.data.yfinance_adapter import YFinanceAdapter
from optionspilot.data.yfinance_provider import YFinanceProvider


def build_provider(cache_db: str | Path | None = None, *,
                   include_stooq: bool = True) -> CachedProvider:
    """Assemble the shipped market-data stack.

    History goes through the full provider chain (Yahoo JSON -> yfinance ->
    Stooq) with one shared cache and one diagnostics recorder. Quotes, option
    chains and expirations still come from `YFinanceProvider`, which remains
    the only source for options data in this build.
    """
    service = MarketDataService(default_registry(include_stooq=include_stooq),
                                cache_db=cache_db)
    return CachedProvider(YFinanceProvider(), service=service)


__all__ = [
    "MarketDataProvider", "CandleCache", "CachedProvider", "YFinanceProvider",
    "MarketDataService", "HistoryResult", "ProviderRegistry",
    "default_registry", "build_provider",
    "HistoryAdapter", "HistoryRequest", "LegacyProviderAdapter",
    "YahooChartAdapter", "YFinanceAdapter", "StooqAdapter",
    "ProviderCapabilities", "Diagnostics", "HistoryReport", "validate_history",
    "ProviderError", "ProviderUnavailable", "ProviderRateLimited",
    "ProviderRangeError", "ProviderSymbolError",
]
