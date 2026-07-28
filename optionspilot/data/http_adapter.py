"""Shared machinery for keyed HTTP/JSON providers.

Finnhub, Twelve Data and Alpha Vantage differ in their URLs, their auth
parameter, their JSON shape and their error dialect. They are identical in
everything else: `urllib` GET with a timeout, HTTP status → typed failure, JSON
parse, timezone normalisation, canonical frame. That identical part lives here,
so each concrete adapter is only the four things the milestone brief allows to
be provider-specific:

    _build_url(...)          endpoint + parameters
    _authenticate(...)       how the key is attached
    _parse(payload, ...)     response shape -> canonical frame
    _translate(payload)      provider error dialect -> typed failure

## Why Yahoo and Stooq were NOT retrofitted onto this

They keep their bespoke transports, deliberately. Yahoo's does multi-host
failover and maps HTTP 422 to `ProviderRangeError` by reading the refusal text;
Stooq's detects an anti-bot HTML challenge and refuses to parse it as prices.
Neither is boilerplate — both are the *reason* those adapters are reliable, and
folding them into a generic helper would either bloat the helper with two
providers' special cases or quietly drop behaviour that took a milestone to get
right. They already work and are covered by tests; churn there buys nothing.

## The two rules every subclass inherits

**Errors are translated, never leaked.** `_translate` maps the provider's own
wording onto the typed failures the service reacts to. Higher layers must never
see "You have exceeded your API call frequency" — they see
`ProviderQuotaExceeded`, which the ranking, the breaker and the diagnostics
page all already understand.

**HTTP 200 does not mean success.** All three of these providers report errors
in a 200 body at least some of the time (Twelve Data always does). The payload
is therefore always offered to `_translate` before it is parsed, and a subclass
that forgets is not possible: `_fetch_native` here calls it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderAuthError, ProviderEntitlementError, ProviderError,
    ProviderRateLimited, ProviderUnavailable, timeout_or_unavailable,
)
from optionspilot.data.base import SESSION_TZ
from optionspilot.data.capabilities import IntervalSpec

log = get_logger("data")

USER_AGENT = "OptionsPilot/0.5 (+https://github.com/vereglover-star/optionspilot)"

#: Cap on a response body. These APIs answer in kilobytes; a multi-hundred-
#: megabyte body means something is very wrong (a proxy error page, a captive
#: portal), and reading it into memory would be the actual damage.
MAX_BODY_BYTES = 64 * 1024 * 1024


class KeyedHTTPAdapter(HistoryAdapter):
    """A `HistoryAdapter` for a keyed JSON REST provider."""

    requires_api_key = True

    def __init__(self, config=None, *, timeout: float | None = None,
                 opener=None, quota_store=None, environ: dict | None = None):
        super().__init__(config, quota_store=quota_store, environ=environ)
        if timeout is not None:
            self.timeout = timeout
        # Injected in tests so every adapter runs fully offline against canned
        # bytes; production passes None and gets urllib's opener.
        self._opener = opener or urllib.request.urlopen

    # ── subclass contract ────────────────────────────────────────────────────

    def _build_url(self, symbol: str, spec: IntervalSpec,
                   start: datetime, end: datetime) -> str:
        raise NotImplementedError

    def _parse(self, payload: dict, spec: IntervalSpec) -> pd.DataFrame:
        raise NotImplementedError

    def _translate(self, payload: dict) -> None:
        """Raise a typed failure if `payload` is an error document.

        Called before `_parse` on every response, including HTTP 200s, because
        all three of these providers signal at least some errors in a 200 body.
        """

    # ── transport ────────────────────────────────────────────────────────────

    def _get_json(self, url: str, *, translate=None) -> dict:
        """One request. Returns a parsed body or raises a typed failure.

        The provider's own error wording never escapes this method or
        `_translate`: everything above sees the typed failures.

        `translate` overrides which validator runs on the body. It exists for
        the one caller that fetches something OTHER than history —
        `verify_credentials`, which asks a provider's cheap credential
        endpoint. The default `_translate` is written against the history
        response and would reject a perfectly good quote document for not
        looking like a candle payload; passing a narrower translator keeps the
        history check as strict as it is while letting a second endpoint share
        the transport, the timeout handling and the status mapping.
        """
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = response.read(MAX_BODY_BYTES)
        except urllib.error.HTTPError as exc:
            raise self._from_status(exc.code, _safe_body(exc)) from exc
        except urllib.error.URLError as exc:
            raise timeout_or_unavailable(
                f"{self.provider_name} unreachable: {exc.reason}", exc) from exc
        except Exception as exc:  # noqa: BLE001 — DNS, TLS, resets, timeouts
            raise timeout_or_unavailable(
                f"{self.provider_name} unreachable: {exc}", exc) from exc

        payload = _decode(body, self.provider_name)
        # Always before parsing: a 200 with an error document is the normal way
        # these providers report quota and auth problems.
        (translate or self._translate)(payload)
        return payload

    def _from_status(self, code: int, body: str) -> ProviderError:
        """HTTP status → typed failure. Shared across every keyed provider.

        A subclass may override for a status its provider uses unusually, but
        none of the three shipped ones need to: the differences between them
        are all in the *body*, which is `_translate`'s job.

        **401 and 403 are different failures and used to be conflated here.**
        That single line cost a user a support cycle: Finnhub moved
        `/stock/candle` behind a paid plan and answers a perfectly good free key
        with 403, so the app reported "the API key was rejected" and sent them
        to regenerate a key that was never wrong. Verified live against the API:

            invalid key   ->  401 {"error":"Invalid API key."}
            no key        ->  401 {"error":"Please use an API key."}
            valid key,
            wrong plan    ->  403 {"error":"You don't have access to this
                                            resource."}

        which is simply the standard meaning of the two codes — 401 is "I do not
        know who you are", 403 is "I know exactly who you are and you may not
        have this". Splitting them here gives every keyed provider the correct
        diagnosis without any provider-specific code.
        """
        detail = body.strip()[:200]
        if code == 401:
            return ProviderAuthError(
                f"{self.provider_name} rejected the API key (HTTP 401)"
                + (f": {detail}" if detail else ""))
        if code == 403:
            return ProviderEntitlementError(
                f"{self.provider_name} accepted the API key but refused this "
                f"data (HTTP 403) — the plan does not include it"
                + (f": {detail}" if detail else ""))
        if code == 429:
            return ProviderRateLimited(
                f"{self.provider_name} rate limited (HTTP 429)", retry_after=60.0)
        if code in (502, 503, 504):
            return ProviderUnavailable(
                f"{self.provider_name} is temporarily unavailable (HTTP {code})")
        return ProviderUnavailable(
            f"{self.provider_name} returned HTTP {code}"
            + (f": {detail}" if detail else ""))

    # ── HistoryAdapter contract ──────────────────────────────────────────────

    def _fetch_native(self, symbol: str, spec: IntervalSpec,
                      start: datetime, end: datetime,
                      prepost: bool) -> pd.DataFrame:
        payload = self._get_json(self._build_url(symbol, spec, start, end))
        frame = self._parse(payload, spec)
        # Trim to the window asked for. These providers round to their own
        # boundaries (Alpha Vantage's `outputsize=full` ignores dates entirely
        # and returns everything it has), and returning more than was requested
        # makes the same request produce different frames from different tiers.
        if not frame.empty:
            frame = frame[(frame.index >= pd.Timestamp(start))
                          & (frame.index <= pd.Timestamp(end))]
        return frame


# ── shared parsing helpers ───────────────────────────────────────────────────

def _decode(body: bytes, provider: str) -> dict:
    """Bytes → JSON object, or a typed failure. Never returns a partial body."""
    if not body:
        raise ProviderUnavailable(f"{provider} returned an empty body")
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # A captive portal, a proxy error page, or a truncated response. This
        # is the check that stops HTML being parsed as prices.
        raise ProviderUnavailable(
            f"{provider} returned a malformed (non-JSON) response") from exc
    if not isinstance(payload, dict):
        raise ProviderUnavailable(
            f"{provider} returned an unexpected payload type "
            f"({type(payload).__name__})")
    return payload


def _safe_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(8192).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — an error body is a nicety, not a need
        return ""


def localize(index: pd.Index, timeframe: Timeframe,
             tz_name: str | None) -> pd.DatetimeIndex:
    """Put a provider's timestamps onto the app's canonical UTC index.

    This is the single most consequential helper in the V0.5.4 adapters, and
    the reason it is shared rather than written three times.

    **Intraday bars** arrive as naive local time in the *exchange's* timezone
    (Twelve Data says which in `meta.exchange_timezone`; Alpha Vantage in
    `Meta Data."6. Time Zone"`). Reading those as UTC would shift every bar by
    4–5 hours and — because the offset changes across a DST boundary — by a
    DIFFERENT amount either side of March and November. The result is not a
    visibly broken chart; it is a subtly wrong one that also poisons the shared
    cache, because bars are keyed by timestamp.

    **Daily and coarser bars** are date-only and are stamped at **00:00 UTC**,
    matching what Yahoo and Stooq already write. That convention is load-
    bearing: the cache is keyed `(symbol, timeframe, ts)`, so a provider that
    stamped daily bars at 05:00 UTC would create a second row for every day
    already held and the chart would render doubled candles.

    An unknown timezone name falls back to UTC with a warning rather than
    raising — a chart that is a few hours out is recoverable and visible; a
    provider that refuses to parse at all is not.
    """
    index = pd.to_datetime(pd.Index(index), errors="coerce")
    if timeframe.minutes >= Timeframe.D1.minutes:
        # Date-only: the session DATE, stamped at midnight in the EXCHANGE's
        # zone (`base.session_index`). This used to stamp 00:00 **UTC** on the
        # belief that it was "the convention every other provider uses" — it
        # was not. Yahoo stamps the session open (13:30 UTC) and yfinance
        # exchange midnight (04:00 UTC), so a UTC-midnight bar was a third
        # instant for the same day, and the chart labels every timestamp
        # through an America/New_York formatter, where 00:00 UTC reads as
        # 19:00 on the PREVIOUS day — an off-by-one date on every daily bar
        # these providers would have served.
        return (pd.DatetimeIndex(index).tz_localize(None).normalize()
                .tz_localize(SESSION_TZ, ambiguous="NaT",
                             nonexistent="shift_forward")
                .tz_convert("UTC"))
    if getattr(index, "tz", None) is not None:
        return pd.DatetimeIndex(index).tz_convert("UTC")
    zone = _zone(tz_name)
    return (pd.DatetimeIndex(index)
            # A DST "spring forward" hour does not exist locally, and a "fall
            # back" hour happens twice. Neither should throw away a whole
            # response, so ambiguous/nonexistent stamps are shifted forward
            # rather than raising.
            .tz_localize(zone, ambiguous="NaT", nonexistent="shift_forward")
            .tz_convert("UTC"))


def _zone(tz_name: str | None):
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("unknown provider timezone %r — treating bars as UTC",
                    tz_name)
        return ZoneInfo("UTC")


def to_frame(rows: dict, index, timeframe: Timeframe,
             tz_name: str | None) -> pd.DataFrame:
    """Build the canonical OHLCV frame from parallel columns.

    Values arrive as strings from two of the three providers, so everything is
    coerced numerically and non-finite rows are dropped here rather than being
    left for `validate_candles` to find — the adapter knows the row was
    unparseable, the validator only knows it was odd.
    """
    frame = pd.DataFrame(rows)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame.index = localize(index, timeframe, tz_name)
    frame = frame[frame.index.notna()]
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame["volume"] = frame["volume"].fillna(0.0)
    return frame.sort_index()


__all__ = ["KeyedHTTPAdapter", "localize", "to_frame", "USER_AGENT",
           "MAX_BODY_BYTES"]
