"""yfinance history adapter — the secondary provider.

`yahoo_provider.YahooChartAdapter` is the primary because it is faster and,
crucially, tells us *why* it refused a request. This adapter exists for the one
failure it cannot cover: the chart JSON endpoint changing shape or being blocked
for our request signature while the underlying data is still available.
`yfinance` reaches the same data through an entirely different code path —
its own session, cookie/crumb handling, headers, and parser — so the two fail
for largely independent reasons. That independence is the whole point of having
a second adapter at all; two adapters over the same HTTP call would only
duplicate the failure.

Its known weaknesses (documented rather than hidden): a process-wide throttle,
an empty frame instead of an error on an out-of-window request, and a
~0.3s import cost. The base class's capability clamping means we no longer
*rely* on it to reject impossible requests, and the empty-frame ambiguity is
converted into a typed error here.
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderUnavailable, Snapshot,
)
from optionspilot.data.capabilities import IntervalSpec, YAHOO_CAPABILITIES

log = get_logger("data")

REQUEST_TIMEOUT = 10.0

# yfinance costs ~0.3s to import and drags in its whole scraping stack; defer it
# to the first actual request so app startup stays fast. NOTE: PyInstaller
# cannot see a dynamic import — `scripts/build_exe.ps1` carries a matching
# `--collect-all yfinance`, and `tests/test_packaging.py` fails if it is ever
# dropped (this exact import once shipped an exe with no yfinance in it).
_yf_module = None


def _yf():
    global _yf_module
    if _yf_module is None:
        _yf_module = importlib.import_module("yfinance")
    return _yf_module


class YFinanceAdapter(HistoryAdapter):
    """History via the `yfinance` package. Priority 20 — first fallback."""

    provider_name = "yfinance"
    provider_priority = 20
    capabilities = YAHOO_CAPABILITIES
    min_request_interval = 0.15

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT, ticker_factory=None):
        super().__init__()
        self._timeout = timeout
        # Injected in tests so this adapter is exercised without yfinance or a
        # network; production passes None and we import lazily.
        self._ticker_factory = ticker_factory

    def _ticker(self, symbol: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(symbol)
        return _yf().Ticker(symbol)

    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        from optionspilot.data.yahoo_provider import yahoo_symbols

        errors: list[str] = []
        for candidate in yahoo_symbols(symbol):
            try:
                raw = self._ticker(candidate).history(
                    start=start, end=end, interval=spec.native,
                    auto_adjust=False, actions=False, prepost=prepost,
                    timeout=self._timeout,
                )
            except Exception as exc:  # noqa: BLE001 — timeouts, parse errors
                errors.append(f"{candidate}: {exc}")
                continue
            if raw is None or raw.empty:
                errors.append(f"{candidate}: empty frame")
                continue
            return _normalize(raw)
        # yfinance cannot distinguish "no data" from "request refused", so an
        # all-empty result becomes a retryable unavailability rather than a
        # silent success with zero bars.
        raise ProviderUnavailable(
            f"yfinance returned no data for {symbol} {spec.native} ({'; '.join(errors)})")

    def _probe(self) -> None:
        raw = self._ticker("SPY").history(period="1d", interval="1d",
                                          timeout=self._timeout)
        if raw is None or raw.empty:
            raise ProviderUnavailable("yfinance probe returned no data")

    def _fetch_snapshot_impl(self, symbol: str) -> Snapshot:
        from optionspilot.data.yahoo_provider import yahoo_symbols

        for candidate in yahoo_symbols(symbol):
            try:
                info = self._ticker(candidate).fast_info
                last = float(info["last_price"])
            except Exception:  # noqa: BLE001 — try the next spelling
                continue
            if last > 0:
                return Snapshot(symbol=symbol.upper(), last=last,
                                previous_close=_maybe(info, "previous_close"))
        raise ProviderUnavailable(f"yfinance has no snapshot for {symbol}")


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    if getattr(df.index, "tz", None) is None:   # daily bars come back tz-naive
        df.index = df.index.tz_localize("UTC")
    return df


def _maybe(info, key):
    try:
        value = info[key]
    except Exception:  # noqa: BLE001 — fast_info keys vary by yfinance version
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return None


__all__ = ["YFinanceAdapter"]
