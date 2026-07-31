"""Alpha Vantage time-series adapter.

    GET /query?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=5min
              &outputsize=full&apikey=<key>

    {"Meta Data": {"6. Time Zone": "US/Eastern", ...},
     "Time Series (5min)": {
         "2026-07-24 19:55:00": {"1. open": "1.0", "2. high": "1.1",
                                 "3. low": "0.9", "4. close": "1.05",
                                 "5. volume": "1000"}, ...}}

**This provider exists to be last, and its budget is why.** The free plan
allows **25 requests per day** — two orders of magnitude tighter than anything
else in the chain. A single chart session switching symbols would spend a whole
day's allowance in well under a minute, and the resulting outage lasts until
midnight with no way to buy it back. It therefore sits at priority 60 (last),
carries the tightest `RateLimitPolicy` in the app, and is the reason
`ratelimit.py` enforces budgets *before* a request rather than reacting to a
429 afterwards.

Three shape quirks worth knowing:

**Errors arrive with HTTP 200, under four different keys.** `Error Message`
(bad symbol or call), `Note` (rate limited), `Information` (the free-tier daily
cap, and also what an invalid key returns), and occasionally a bare
`{"Information": ...}` with no series at all. All four are handled in
`_translate`; none of the wording escapes this file.

**The series key is dynamic.** `Time Series (5min)`, `Time Series (Daily)`,
`Weekly Time Series`, `Monthly Time Series` — the name depends on the function.
`_series_key` finds it structurally rather than by guessing every spelling.

**Intraday timestamps are US/Eastern, not UTC**, and `Meta Data."6. Time Zone"`
says so. Reading them as UTC would shift every bar by 4–5 hours and by a
different amount across a DST boundary. Daily+ bars are date-only and are
stamped at 00:00 UTC to match Yahoo and Stooq, because the cache is keyed on
the timestamp and a second convention would double every row.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    ProviderAuthError, ProviderQuotaExceeded, ProviderSymbolError,
    ProviderUnavailable,
)
from optionspilot.data.capabilities import (
    ALPHAVANTAGE_CAPABILITIES, IntervalSpec,
)
from optionspilot.data.http_adapter import KeyedHTTPAdapter, to_frame
from optionspilot.data.ratelimit import RateLimitPolicy

log = get_logger("data")

BASE_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT = 15.0          # documented as slower than the others

#: Free plan: 25 requests/day, 5/minute. The tightest budget in the app.
RATE_LIMIT = RateLimitPolicy(per_minute=5, per_day=25)

#: Column names in the response, keyed by their canonical name.
_COLUMNS = {"open": "1. open", "high": "2. high", "low": "3. low",
            "close": "4. close", "volume": "5. volume"}

#: Fragments meaning "your allowance is spent". Checked BEFORE the auth
#: markers, because the real daily-cap message mentions the API key.
_QUOTA_MARKERS = ("rate limit", "requests per day", "call frequency",
                  "premium", "higher api call volume", "thank you for using")
#: Fragments meaning "your credential is wrong".
_AUTH_MARKERS = ("invalid", "apikey", "api key", "missing")


class AlphaVantageAdapter(KeyedHTTPAdapter):
    """Alpha Vantage time series. Priority 60 — the last resort."""

    provider_name = "alphavantage"
    provider_priority = 60
    capabilities = ALPHAVANTAGE_CAPABILITIES
    default_timeout = REQUEST_TIMEOUT
    rate_limit = RATE_LIMIT
    api_key_env_vars = ("ALPHAVANTAGE_API_KEY", "ALPHA_VANTAGE_API_KEY",
                        "OPTIONSPILOT_ALPHAVANTAGE_API_KEY")
    signup_url = "https://www.alphavantage.co/support/#api-key"
    # Every failure path raises; an empty series is a genuine empty window.
    reports_empty_reliably = True
    # 5/minute. The daily budget is the binding constraint, but pacing keeps a
    # burst from wasting three of twenty-five requests on 429s.
    min_request_interval = 1.0

    # ── provider specifics ───────────────────────────────────────────────────

    def _build_url(self, symbol: str, spec: IntervalSpec,
                   start: datetime, end: datetime) -> str:
        intraday = spec.native.endswith("min")
        params = {
            "function": "TIME_SERIES_INTRADAY" if intraday else spec.native,
            "symbol": symbol.upper(),
            # This API takes no date range at all: `full` returns everything it
            # has and the base class trims. That is exactly why each request is
            # expensive and why the budget matters.
            "outputsize": "full",
            "datatype": "json",
            "apikey": self.api_key or "",
        }
        if intraday:
            params["interval"] = spec.native
            params["extended_hours"] = "false"   # the trading path is RTH-only
        return f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    def _translate(self, payload: dict) -> None:
        """Alpha Vantage's four error keys → typed failures."""
        error = str(payload.get("Error Message") or "").strip()
        if error:
            lowered = error.lower()
            if "apikey" in lowered or "api key" in lowered:
                raise ProviderAuthError(f"alphavantage: {error}")
            # The documented wording for an unknown ticker.
            raise ProviderSymbolError(f"alphavantage: {error}")

        # `Note` is the classic per-minute throttle message.
        note = str(payload.get("Note") or "").strip()
        if note:
            raise ProviderQuotaExceeded(f"alphavantage: {note}", retry_after=60.0)

        # `Information` carries BOTH the daily-cap message and the invalid-key
        # message, so the text is the only discriminator available.
        #
        # QUOTA IS CHECKED FIRST, and the order is not arbitrary: the real
        # daily-cap message is "We have detected your API key ... our standard
        # API rate limit is 25 requests per day", which CONTAINS "API key".
        # Testing for the key first therefore reports a spent quota as a
        # rejected credential — sending the user off to regenerate a key that
        # was never the problem, and marking the provider auth-failed (sticky)
        # instead of quota-exceeded (clears tomorrow).
        info = str(payload.get("Information") or "").strip()
        if info:
            lowered = info.lower()
            if any(marker in lowered for marker in _QUOTA_MARKERS):
                # A spent daily allowance: hours away, not seconds.
                raise ProviderQuotaExceeded(f"alphavantage: {info}")
            if any(marker in lowered for marker in _AUTH_MARKERS):
                raise ProviderAuthError(f"alphavantage: {info}")
            raise ProviderUnavailable(f"alphavantage: {info}")

    def _parse(self, payload: dict, spec: IntervalSpec) -> pd.DataFrame:
        key = _series_key(payload)
        if key is None:
            # No error key and no series: an unexpected shape rather than an
            # empty window, so it must not be reported as "no bars here".
            raise ProviderUnavailable(
                "alphavantage response carried no time series")
        series = payload.get(key) or {}
        if not isinstance(series, dict):
            raise ProviderUnavailable(
                "alphavantage time series was not an object — malformed")
        if not series:
            return pd.DataFrame()

        meta = payload.get("Meta Data") or {}
        tz_name = meta.get("6. Time Zone") or meta.get("5. Time Zone")

        stamps, columns = [], {name: [] for name in _COLUMNS}
        for stamp, row in series.items():
            if not isinstance(row, dict):
                continue                   # a malformed row is dropped, not fatal
            stamps.append(stamp)
            for name, source in _COLUMNS.items():
                columns[name].append(row.get(source))
        if not stamps:
            raise ProviderUnavailable(
                "alphavantage returned rows with no usable timestamps")

        # The NATIVE interval — see the note in finnhub_provider._parse.
        timeframe = Timeframe.from_string(_APP_TF[spec.native])
        return to_frame(columns, stamps, timeframe, tz_name)

    def _probe(self) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        self._get_json(self._build_url(
            "IBM", self.capabilities.spec(Timeframe.D1),
            now - _dt.timedelta(days=7), now))


def _series_key(payload: dict) -> str | None:
    """Find the time-series key structurally.

    The name varies by function (`Time Series (5min)`, `Time Series (Daily)`,
    `Weekly Time Series`, `Monthly Adjusted Time Series`, ...). Matching on
    "contains 'time series', case-insensitively, and holds an object" is
    stable across every function this adapter uses and across the ones it does
    not — which is what stops a future interval quietly returning zero bars.
    """
    for key, value in payload.items():
        if "time series" in key.lower() and isinstance(value, dict):
            return key
    return None


#: Native function/interval -> the app timeframe it represents. Used only to tell
#: The app timeframe each NATIVE interval represents. Used only to tell
#: intraday bars (which need timezone conversion) from daily ones.
_APP_TF = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
           "60min": "1h", "TIME_SERIES_DAILY": "1d",
           "TIME_SERIES_WEEKLY": "1w", "TIME_SERIES_MONTHLY": "1mo"}


__all__ = ["AlphaVantageAdapter", "BASE_URL", "RATE_LIMIT"]
