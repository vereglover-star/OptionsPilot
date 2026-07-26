"""Stooq CSV adapter — a non-Yahoo daily/weekly/monthly source.

Every other adapter in this app ultimately reads Yahoo. That makes them
correlated: a Yahoo outage, a Yahoo IP block, or a Yahoo schema change takes
all of them down at once. Stooq is the only free, key-less source found during
the provider survey (see `docs/MARKET_DATA.md` §4) that is *independent* of
Yahoo and serves decades of end-of-day history over a plain CSV URL.

It is deliberately scoped to daily and coarser. Stooq's intraday feed is not
reliably available without an account, and a provider that half-works at
intraday resolution is worse than one that honestly declares it cannot.

**Availability caveat, measured:** from some networks Stooq answers an
anti-bot HTML challenge page instead of CSV. That is why `_parse_csv` refuses
anything that does not look like the expected CSV header rather than trying to
salvage it — a challenge page parsed as data would be far worse than an
outage. When this happens the adapter reports itself unavailable, the registry
opens its circuit breaker, and nothing else in the app notices.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderRateLimited, ProviderSymbolError,
    ProviderUnavailable,
)
from optionspilot.data.capabilities import IntervalSpec, STOOQ_CAPABILITIES

log = get_logger("data")

BASE_URL = "https://stooq.com/q/d/l/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REQUEST_TIMEOUT = 12.0

EXPECTED_HEADER = ("date", "open", "high", "low", "close", "volume")


def stooq_symbol(symbol: str) -> str:
    """US equities are suffixed `.us` and lower-cased on Stooq."""
    raw = symbol.lower().strip().replace(".", "-")
    return raw if "." in raw else f"{raw}.us"


class StooqAdapter(HistoryAdapter):
    """Stooq end-of-day CSV. Priority 30 — the independent last resort."""

    provider_name = "stooq"
    provider_priority = 30
    capabilities = STOOQ_CAPABILITIES
    min_request_interval = 0.25
    # Blocks, HTML challenges, changed headers and unknown symbols all raise;
    # a valid CSV header with no data rows is a genuine empty window.
    reports_empty_reliably = True

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT, opener=None):
        super().__init__()
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _get_csv(self, params: dict) -> str:
        url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/csv,text/plain",
        })
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimited("stooq rate limited",
                                          retry_after=120.0) from exc
            if exc.code == 404:
                raise ProviderSymbolError(f"stooq 404 for {params.get('s')}") from exc
            raise ProviderUnavailable(f"stooq HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 — timeouts, DNS, TLS
            raise ProviderUnavailable(f"stooq unreachable: {exc}") from exc

    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        body = self._get_csv({
            "s": stooq_symbol(symbol),
            "i": spec.native,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
        })
        return _parse_csv(body, symbol)

    def _probe(self) -> None:
        body = self._get_csv({"s": "spy.us", "i": "d"})
        _parse_csv(body, "SPY")


def _parse_csv(body: str, symbol: str) -> pd.DataFrame:
    text = body.strip()
    if not text:
        raise ProviderUnavailable(f"stooq returned an empty body for {symbol}")
    lowered = text[:200].lower()
    if lowered.startswith("<") or "<html" in lowered or "<!doctype" in lowered:
        # An anti-bot / challenge page. Refuse it loudly — silently parsing
        # HTML as prices is the failure mode this check exists to prevent.
        raise ProviderUnavailable(
            f"stooq served an HTML page instead of CSV for {symbol} "
            "(anti-bot challenge or block)")
    if text.lower().startswith("no data"):
        raise ProviderSymbolError(f"stooq has no data for {symbol}")

    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:  # pragma: no cover — guarded by the empty check above
        raise ProviderUnavailable(f"stooq returned a headerless body for {symbol}") from None
    if tuple(header[:len(EXPECTED_HEADER)]) != EXPECTED_HEADER:
        raise ProviderUnavailable(
            f"stooq CSV header changed for {symbol}: {header[:8]}")

    rows: list[tuple] = []
    for row in reader:
        if len(row) < len(EXPECTED_HEADER):
            continue
        try:
            rows.append((
                row[0],
                float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                float(row[5] or 0.0),
            ))
        except ValueError:
            continue        # a malformed line is dropped, not fatal
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low",
                                        "close", "volume"])
    # Stooq stamps daily bars with a bare date. Every other source in this app
    # stamps the daily bar at 00:00 UTC, and the cache is keyed on that, so
    # localizing to UTC (not to exchange time) keeps the two interchangeable.
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["ts"])
    return frame.set_index("ts")


__all__ = ["StooqAdapter", "stooq_symbol"]
