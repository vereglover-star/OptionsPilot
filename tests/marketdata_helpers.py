"""Shared fakes for the market-data tests — no sockets, no yfinance.

Everything in the market-data stack is injectable at its transport boundary,
which is what lets the whole subsystem (adapters, registry, service, cache,
validation, diagnostics) be exercised deterministically and offline. These are
the pieces that make that convenient:

  - `FakeResponse` / `fake_opener` satisfy the structural contract urllib's
    opener has (context manager returning something with `.read()`), so the
    Yahoo and Stooq adapters run against canned bytes.
  - `yahoo_payload` builds a genuine v8 chart JSON body, including the column
    -oriented `indicators.quote` layout and its `null` holes.
  - `ScriptedAdapter` is a `HistoryAdapter` whose every response is dictated by
    a list, so provider chains, retries, failovers and breaker trips can be
    driven exactly.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import HistoryAdapter, ProviderUnavailable
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, YAHOO_CAPABILITIES,
)


# ── HTTP fakes ───────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def fake_opener(body, *, status: int = 200, record: list | None = None):
    """An opener returning `body` (bytes/str/dict) or raising for status >= 400.

    `record` collects every requested URL, so tests can assert on which hosts
    and parameters were used — including that an impossible request was never
    made at all.
    """
    if isinstance(body, dict):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode()

    def opener(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if record is not None:
            record.append(url)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "error", {},
                                         io.BytesIO(body))
        return FakeResponse(body)

    return opener


def sequence_opener(responses, record: list | None = None):
    """An opener that plays back `responses` in order.

    Each item is either bytes/str/dict (a 200 body), an `(status, body)` tuple
    (an HTTPError), or an exception instance (raised as a transport failure).
    The final item repeats once exhausted.
    """
    state = {"i": 0}

    def opener(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if record is not None:
            record.append(url)
        item = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple):
            status, body = item
            if isinstance(body, dict):
                body = json.dumps(body)
            if isinstance(body, str):
                body = body.encode()
            raise urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(body))
        if isinstance(item, dict):
            item = json.dumps(item)
        if isinstance(item, str):
            item = item.encode()
        return FakeResponse(item)

    return opener



# ── payload builders ─────────────────────────────────────────────────────────

def yahoo_payload(n: int = 10, *, interval: str = "5m", start: datetime | None = None,
                  symbol: str = "SPY", holes: tuple[int, ...] = (),
                  granularity: str | None = None) -> dict:
    """A realistic Yahoo v8 chart body: column-oriented, with `null` holes."""
    step = _interval_seconds(interval)
    start = start or datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
    stamps = [int((start + timedelta(seconds=step * i)).timestamp())
              for i in range(n)]
    o, h, l, c, v = [], [], [], [], []
    for i in range(n):
        if i in holes:
            o.append(None); h.append(None); l.append(None)
            c.append(None); v.append(None)
            continue
        base = 100.0 + i
        o.append(base); h.append(base + 1); l.append(base - 1)
        c.append(base + 0.5); v.append(1000 + i)
    return {"chart": {"error": None, "result": [{
        "meta": {"currency": "USD", "symbol": symbol,
                 "dataGranularity": granularity or interval,
                 "regularMarketPrice": 123.45, "chartPreviousClose": 122.0,
                 "exchangeTimezoneName": "America/New_York",
                 "fullExchangeName": "NYSEArca", "marketState": "REGULAR"},
        "timestamp": stamps,
        "indicators": {"quote": [{"open": o, "high": h, "low": l,
                                  "close": c, "volume": v}]},
    }]}}


def yahoo_error(description: str, code: str = "Unprocessable Entity") -> dict:
    return {"chart": {"result": None,
                      "error": {"code": code, "description": description}}}


def stooq_csv(n: int = 5, start: str = "2026-07-20") -> str:
    rows = ["Date,Open,High,Low,Close,Volume"]
    day = pd.Timestamp(start)
    for i in range(n):
        base = 100 + i
        rows.append(f"{(day + pd.Timedelta(days=i)).date()},{base},{base+1},"
                    f"{base-1},{base+0.5},{1000+i}")
    return "\n".join(rows) + "\n"


# ── keyed-provider payload builders (V0.5.4) ─────────────────────────────────
#
# Real response shapes, reduced to the parts the adapters read. Every keyed
# provider test runs against these — no sockets, no API keys, no internet.

def finnhub_payload(n: int = 5, *, interval_seconds: int = 300,
                    start: datetime | None = None, status: str = "ok") -> dict:
    """Finnhub's column-oriented candle body. Timestamps are unix seconds UTC."""
    start = start or datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    stamps = [int((start + timedelta(seconds=interval_seconds * i)).timestamp())
              for i in range(n)]
    base = [100.0 + i for i in range(n)]
    return {"s": status, "t": stamps,
            "o": base, "h": [b + 1 for b in base], "l": [b - 1 for b in base],
            "c": [b + 0.5 for b in base], "v": [1000.0 + i for i in range(n)]}


def twelvedata_payload(n: int = 5, *, interval: str = "5min",
                       start: str = "2026-07-24 09:30:00",
                       timezone_name: str = "America/New_York",
                       newest_first: bool = True) -> dict:
    """Twelve Data's `values` body.

    `datetime` strings are NAIVE LOCAL TIME in `meta.exchange_timezone` — the
    property that makes `http_adapter.localize` necessary — and rows arrive
    newest-first by default, as the real API returns them.
    """
    step = pd.Timedelta(_interval_seconds(interval), unit="s")
    first = pd.Timestamp(start)
    values = []
    for i in range(n):
        base = 100.0 + i
        values.append({
            "datetime": (first + step * i).strftime("%Y-%m-%d %H:%M:%S"),
            "open": f"{base:.5f}", "high": f"{base + 1:.5f}",
            "low": f"{base - 1:.5f}", "close": f"{base + 0.5:.5f}",
            "volume": str(1000 + i),
        })
    if newest_first:
        values.reverse()
    return {"meta": {"symbol": "AAPL", "interval": interval,
                     "exchange_timezone": timezone_name,
                     "exchange": "NASDAQ", "type": "Common Stock"},
            "values": values, "status": "ok"}


def alphavantage_payload(n: int = 5, *, interval: str = "5min",
                         start: str = "2026-07-24 09:30:00",
                         timezone_name: str = "US/Eastern",
                         key: str | None = None) -> dict:
    """Alpha Vantage's nested body.

    Intraday timestamps are naive US/Eastern; the series key is dynamic
    (`Time Series (5min)`, `Time Series (Daily)`, ...), which is why the
    adapter finds it structurally instead of guessing.
    """
    daily = not interval.endswith("min")
    step = pd.Timedelta(days=1) if daily else pd.Timedelta(
        _interval_seconds(interval), unit="s")
    first = pd.Timestamp(start)
    series = {}
    for i in range(n):
        stamp = first + step * i
        base = 100.0 + i
        label = (stamp.strftime("%Y-%m-%d") if daily
                 else stamp.strftime("%Y-%m-%d %H:%M:%S"))
        series[label] = {"1. open": f"{base:.4f}", "2. high": f"{base + 1:.4f}",
                         "3. low": f"{base - 1:.4f}",
                         "4. close": f"{base + 0.5:.4f}",
                         "5. volume": str(1000 + i)}
    name = key or ("Time Series (Daily)" if daily
                   else f"Time Series ({interval})")
    return {"Meta Data": {"1. Information": "test", "2. Symbol": "AAPL",
                          "6. Time Zone": timezone_name},
            name: series}


def _interval_seconds(interval: str) -> int:
    table = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
             "1h": 3600, "60m": 3600, "90m": 5400, "1d": 86400,
             "1wk": 604800, "1mo": 2592000, "d": 86400,
             # keyed-provider spellings
             "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
             "45min": 2700, "60min": 3600, "2h": 7200, "4h": 14400,
             "1day": 86400, "1week": 604800, "1month": 2592000}
    return table[interval]


def frame(n: int = 20, timeframe: Timeframe = Timeframe.M5,
          end: datetime | None = None, *, start: datetime | None = None
          ) -> pd.DataFrame:
    """A canonical candle frame spaced for `timeframe`."""
    freq = pd.Timedelta(minutes=timeframe.minutes)
    if start is not None:
        idx = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    else:
        end = end or datetime.now(timezone.utc).replace(microsecond=0)
        idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    base = np.arange(n, dtype=float)
    return pd.DataFrame({
        "open": 100 + base, "high": 101 + base, "low": 99 + base,
        "close": 100.5 + base, "volume": 1000.0,
    }, index=idx.rename("ts"))


# ── a fully scripted adapter ─────────────────────────────────────────────────

UNLIMITED = ProviderCapabilities(
    intervals={tf: IntervalSpec(native=str(tf)) for tf in Timeframe},
    extended_hours=True,
)


class ScriptedAdapter(HistoryAdapter):
    """A `HistoryAdapter` whose answers come from a script.

    Each script entry is a DataFrame (served), an exception instance (raised),
    or None (an empty frame). The last entry repeats once exhausted, so
    "always fails" is `[SomeError(...)]`.
    """

    def __init__(self, name: str, script=None, *, priority: int = 100,
                 capabilities: ProviderCapabilities | None = None,
                 timeframe: Timeframe = Timeframe.M5,
                 config=None):
        self.provider_name = name
        self.provider_priority = priority
        self.capabilities = capabilities or UNLIMITED
        self.min_request_interval = 0.0
        self._script = list(script) if script is not None else [frame(20, timeframe)]
        self.calls: list[tuple] = []
        super().__init__(config)

    def _fetch_native(self, symbol, spec, start, end, prepost):
        self.calls.append((symbol, spec.native, start, end, prepost))
        item = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        if isinstance(item, BaseException):
            raise item
        if item is None:
            return pd.DataFrame()
        return item

    def _probe(self) -> None:
        if isinstance(self._script[0], BaseException):
            raise ProviderUnavailable(f"{self.provider_name} probe failed")


YAHOO_LIKE = YAHOO_CAPABILITIES

__all__ = [
    "FakeResponse", "fake_opener", "sequence_opener", "yahoo_payload",
    "yahoo_error", "stooq_csv", "frame", "ScriptedAdapter", "UNLIMITED",
    "YAHOO_LIKE", "finnhub_payload", "twelvedata_payload",
    "alphavantage_payload",
]
