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


def _interval_seconds(interval: str) -> int:
    table = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
             "1h": 3600, "60m": 3600, "90m": 5400, "1d": 86400,
             "1wk": 604800, "1mo": 2592000, "d": 86400}
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
    "YAHOO_LIKE",
]
