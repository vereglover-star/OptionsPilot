"""Yahoo Finance chart API adapter — the primary history provider.

This talks to Yahoo's public `v8/finance/chart` JSON endpoint directly over
`urllib`, rather than through `yfinance`. That is a deliberate reversal of the
app's original choice, for four measured reasons:

1. **It reports why it refused.** An out-of-window request answers HTTP 422
   with `"5m data not available ... The requested range must be within the last
   60 days"`. `yfinance` swallows that into an empty DataFrame, which is
   indistinguishable from "the network hiccuped" — the ambiguity behind years
   of blank-chart bugs here. A typed `ProviderRangeError` ends that ambiguity.
2. **It is faster and lighter.** ~0.2s per request against ~0.3s of import
   cost plus a scraping stack; no pandas round-trip inside the provider.
3. **It has no hidden global state.** `yfinance` serializes every caller
   through one process-wide throttle and a shared cookie/crumb session; a hung
   request there stalls unrelated symbols (root cause of V3.3.1's permanent
   blank chart).
4. **It is a smaller surface to break.** The JSON contract has been stable for
   years; `yfinance`'s scraping internals change release to release.

`yfinance` is retained as the *secondary* adapter (see `yfinance_adapter.py`):
it reaches the same upstream data by a completely different code path, so it
covers the case where this endpoint's shape changes rather than the data
disappearing.

Bars are returned as Yahoo serves them in `indicators.quote` — split-adjusted,
dividend-unadjusted, matching `quality.ADJUSTMENT_CONVENTION` and the previous
`auto_adjust=False` behaviour. `adjclose` is deliberately ignored so a
dividend never puts a step in the chart that never traded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderRangeError, ProviderRateLimited,
    ProviderSymbolError, ProviderUnavailable, Snapshot, timeout_or_unavailable,
)
from optionspilot.data.capabilities import IntervalSpec, YAHOO_CAPABILITIES

log = get_logger("data")

#: Both hosts serve the same API. Trying the second on a transport failure
#: costs one request and recovers from single-host trouble, which is common.
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
PATH = "/v8/finance/chart/"

#: Yahoo rejects requests without a browser-ish agent.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

REQUEST_TIMEOUT = 10.0


def yahoo_symbols(symbol: str) -> list[str]:
    """Yahoo spellings to try, most likely first.

    Yahoo writes class shares with a hyphen (BRK.B -> BRK-B) where users and
    most data sources type a dot; indices carry a caret (SPX -> ^SPX). Ordinary
    tickers stay on the single-candidate fast path.
    """
    raw = symbol.upper().strip()
    out = [raw]
    if "." in raw:
        out.append(raw.replace(".", "-"))
    if "-" in raw:
        out.append(raw.replace("-", "."))
    return list(dict.fromkeys(v for v in out if v))


class YahooChartAdapter(HistoryAdapter):
    """Yahoo's chart JSON endpoint. Priority 10 — the default primary."""

    provider_name = "yahoo"
    provider_priority = 10
    capabilities = YAHOO_CAPABILITIES
    min_request_interval = 0.05
    # Every failure path here raises, so an empty frame can only mean the
    # window genuinely holds no bars — a 200 response whose `timestamp` array
    # is absent. That is exactly the property that makes this the primary.
    reports_empty_reliably = True
    default_timeout = REQUEST_TIMEOUT

    def __init__(self, config=None, *, timeout: float | None = None,
                 opener=None, hosts: tuple[str, ...] = HOSTS):
        super().__init__(config)
        # An explicit `timeout=` wins over configuration; configuration wins
        # over `default_timeout`. Tests use the first, users the second.
        if timeout is not None:
            self.timeout = timeout
        # Injected in tests so the whole adapter runs offline; in production
        # this is urllib's default opener.
        self._opener = opener or urllib.request.urlopen
        self._hosts = hosts

    @property
    def _timeout(self) -> float:
        return self.timeout

    # ── transport ────────────────────────────────────────────────────────────

    def _get_json(self, symbol: str, params: dict) -> dict:
        """One logical request, tried across hosts. Raises a typed
        `ProviderError`; never returns a partial or non-JSON body."""
        query = urllib.parse.urlencode(params)
        quoted = urllib.parse.quote(symbol, safe="^=.-")
        last: Exception | None = None
        for host in self._hosts:
            url = f"https://{host}{PATH}{quoted}?{query}"
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            try:
                with self._opener(req, timeout=self._timeout) as resp:
                    body = resp.read()
                return _parse_json(body)
            except urllib.error.HTTPError as exc:
                detail = _error_detail(exc)
                if exc.code == 429:
                    raise ProviderRateLimited(
                        f"yahoo rate limited: {detail}", retry_after=60.0) from exc
                if exc.code == 404:
                    raise ProviderSymbolError(
                        f"yahoo does not know {symbol}: {detail}") from exc
                if exc.code == 422:
                    # Yahoo states the real limit in the body. Surface it —
                    # this is the message that stops the UI retrying forever.
                    raise ProviderRangeError(f"yahoo: {detail}") from exc
                if exc.code == 400 and "data doesn't exist" in detail.lower():
                    # "Data doesn't exist for startDate=... endDate=..." is a
                    # statement about the WINDOW, not about Yahoo's health —
                    # it is what a future or pre-listing range gets. Counting
                    # it as an outage let one absurd request trip the circuit
                    # breaker and take the provider out of rotation for real
                    # charts (found in the V0.5.2 self-audit).
                    raise ProviderRangeError(f"yahoo: {detail}") from exc
                if exc.code in (400, 401, 403):
                    raise ProviderUnavailable(
                        f"yahoo refused the request ({exc.code}): {detail}") from exc
                last = exc              # 5xx and friends: try the other host
            except ProviderUnavailable as exc:
                last = exc              # malformed body: the other host may be fine
            except Exception as exc:  # noqa: BLE001 — timeouts, DNS, TLS, resets
                last = exc
        # A timeout is reported as its own kind: a provider that is SLOW and one
        # that is BROKEN want different responses, and the ranking reacts to the
        # difference. Both are still `ProviderUnavailable` subclasses, so every
        # existing handler is unaffected.
        raise timeout_or_unavailable(
            f"yahoo unreachable for {symbol}: {last}", last or Exception())

    # ── HistoryAdapter contract ──────────────────────────────────────────────

    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        params = {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": spec.native,
            "includePrePost": "true" if prepost else "false",
            "events": "div,split",
        }
        errors: list[Exception] = []
        for candidate in yahoo_symbols(symbol):
            try:
                payload = self._get_json(candidate, params)
            except ProviderSymbolError as exc:
                errors.append(exc)
                continue        # a spelling variant may still resolve
            return _to_frame(payload, candidate, spec.native)
        raise ProviderSymbolError(
            f"yahoo could not resolve {symbol} ({errors[-1] if errors else 'no detail'})")

    def _probe(self) -> None:
        payload = self._get_json("SPY", {"range": "1d", "interval": "1d"})
        if not (payload.get("chart") or {}).get("result"):
            raise ProviderUnavailable("yahoo probe returned no result")

    def _fetch_snapshot_impl(self, symbol: str) -> Snapshot:
        errors: list[Exception] = []
        for candidate in yahoo_symbols(symbol):
            try:
                payload = self._get_json(candidate, {"range": "1d", "interval": "1d"})
            except ProviderSymbolError as exc:
                errors.append(exc)
                continue
            meta = _result(payload, candidate).get("meta") or {}
            last = meta.get("regularMarketPrice")
            if last is None:
                raise ProviderUnavailable(f"yahoo snapshot for {candidate} has no price")
            return Snapshot(
                symbol=symbol.upper(),
                last=float(last),
                previous_close=_opt_float(meta.get("chartPreviousClose")),
                currency=str(meta.get("currency") or "USD"),
                exchange=str(meta.get("fullExchangeName") or ""),
                market_state=str(meta.get("marketState") or ""),
                extra={"timezone": meta.get("exchangeTimezoneName")},
            )
        raise ProviderSymbolError(
            f"yahoo could not resolve {symbol} ({errors[-1] if errors else 'no detail'})")


# ── payload parsing ──────────────────────────────────────────────────────────

def _parse_json(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except Exception as exc:  # noqa: BLE001 — truncated/HTML body
        raise ProviderUnavailable(
            f"yahoo returned a non-JSON body ({len(body)} bytes)") from exc
    if not isinstance(payload, dict):
        raise ProviderUnavailable("yahoo returned a non-object JSON body")
    return payload


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Yahoo's own explanation, when it sent one."""
    try:
        body = json.loads(exc.read())
        detail = (((body.get("chart") or {}).get("error")) or {}).get("description")
        if detail:
            return str(detail)[:300]
    except Exception:  # noqa: BLE001 — best effort; fall back to the status line
        pass
    return f"HTTP {exc.code} {exc.reason}"


def _result(payload: dict, symbol: str) -> dict:
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise ProviderUnavailable("yahoo payload has no 'chart' object")
    error = chart.get("error")
    if error:
        description = str((error or {}).get("description") or error)[:300]
        code = str((error or {}).get("code") or "")
        if "not found" in description.lower() or code == "Not Found":
            raise ProviderSymbolError(f"yahoo: {description}")
        raise ProviderRangeError(f"yahoo: {description}")
    results = chart.get("result")
    if not results:
        raise ProviderUnavailable(f"yahoo returned no result for {symbol}")
    first = results[0]
    if not isinstance(first, dict):
        raise ProviderUnavailable("yahoo result[0] is not an object")
    return first


def _to_frame(payload: dict, symbol: str, interval: str) -> pd.DataFrame:
    """Yahoo's column-oriented payload -> a canonical candle frame.

    Yahoo emits `null` for bars it has no data for (halts, thin pre-market),
    and sometimes truncates one of the OHLCV lists relative to `timestamp`.
    Both are normal; both are handled by padding to the timestamp length with
    NaN and letting the shape validator drop those rows.
    """
    result = _result(payload, symbol)
    stamps = result.get("timestamp")
    if not stamps:
        # A valid, genuinely empty window (holiday, or a range before listing).
        return pd.DataFrame()
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    n = len(stamps)
    data = {}
    for src, dest in (("open", "open"), ("high", "high"), ("low", "low"),
                      ("close", "close"), ("volume", "volume")):
        values = quote.get(src) or []
        if len(values) < n:
            values = list(values) + [None] * (n - len(values))
        data[dest] = pd.to_numeric(pd.Series(values[:n], dtype="object"),
                                   errors="coerce").astype("float64")
    index = pd.to_datetime(np.asarray(stamps, dtype="int64"), unit="s", utc=True)
    frame = pd.DataFrame(data)
    frame.index = index
    frame.index.name = "ts"
    granularity = (result.get("meta") or {}).get("dataGranularity")
    if granularity and granularity != interval:
        # Yahoo silently downgrades granularity when it can't serve what was
        # asked (e.g. answering 1d for a 1m request). Rendering that as the
        # requested interval would be a lie; refuse it and let the service
        # fail over. quality.validate_history catches this too, from the data
        # side — this is the cheaper, explicit check.
        raise ProviderRangeError(
            f"yahoo served {granularity} bars for a {interval} request")
    return frame


def _opt_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return None


__all__ = ["YahooChartAdapter", "yahoo_symbols", "HOSTS", "REQUEST_TIMEOUT"]
