"""Twelve Data time-series adapter.

    GET /time_series?symbol=AAPL&interval=5min&outputsize=5000&apikey=<key>

    {"meta": {"symbol": "AAPL", "interval": "5min",
              "exchange_timezone": "America/New_York", ...},
     "values": [{"datetime": "2026-07-24 15:55:00", "open": "1.0",
                 "high": "1.1", "low": "0.9", "close": "1.05",
                 "volume": "1000"}, ...],
     "status": "ok"}

Three properties of this API shape the adapter, and each has bitten real
integrations:

**Errors arrive with HTTP 200.** A spent quota returns `{"code": 429,
"message": "You have run out of API credits...", "status": "error"}` with a
perfectly successful HTTP status. Any integration that trusts the status line
silently treats an error document as data. `_translate` therefore inspects
every body, and `KeyedHTTPAdapter` guarantees it runs before `_parse`.

**Timestamps are naive local time in the EXCHANGE's timezone**, which `meta`
names. Reading them as UTC shifts every intraday bar by 4–5 hours, and by a
*different* amount either side of a DST boundary — a quietly wrong chart that
also poisons the shared cache, since bars are keyed by timestamp. The shared
`http_adapter.localize` handles it; `meta.exchange_timezone` is passed to it.

**Values are newest-first.** `to_frame` sorts, so the canonical frame is
ascending like every other provider's.

Free plan: 800 requests/day and 8/minute. The daily figure is comfortable for
one desktop user; the per-minute one is not, and is the reason
`min_request_interval` is set here — eight requests a minute is one every 7.5
seconds, which a burst of symbol switches would blow through instantly.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    ProviderAuthError, ProviderQuotaExceeded, ProviderRangeError,
    ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.capabilities import IntervalSpec, TWELVEDATA_CAPABILITIES
from optionspilot.data.http_adapter import KeyedHTTPAdapter, to_frame
from optionspilot.data.ratelimit import RateLimitPolicy

log = get_logger("data")

BASE_URL = "https://api.twelvedata.com/time_series"
REQUEST_TIMEOUT = 12.0

#: Free plan: 800 credits/day, 8 requests/minute.
RATE_LIMIT = RateLimitPolicy(per_minute=8, per_day=800)

#: The API's documented maximum rows per request.
MAX_OUTPUTSIZE = 5000


class TwelveDataAdapter(KeyedHTTPAdapter):
    """Twelve Data time series. Priority 50 — second of the keyed providers."""

    provider_name = "twelvedata"
    provider_priority = 50
    capabilities = TWELVEDATA_CAPABILITIES
    default_timeout = REQUEST_TIMEOUT
    rate_limit = RATE_LIMIT
    api_key_env_vars = ("TWELVEDATA_API_KEY", "TWELVE_DATA_API_KEY",
                        "OPTIONSPILOT_TWELVEDATA_API_KEY")
    signup_url = "https://twelvedata.com/pricing"
    # Every failure path raises; an empty `values` list is a genuine empty
    # window (the API says `status: ok` with no rows for a holiday).
    reports_empty_reliably = True
    # 8/minute is one per 7.5s. The budget in `ratelimit.py` enforces the hard
    # ceiling; this spaces bursts so the ceiling is approached smoothly rather
    # than in a clump the API sees as abuse.
    min_request_interval = 0.5

    # ── provider specifics ───────────────────────────────────────────────────

    def _build_url(self, symbol: str, spec: IntervalSpec,
                   start: datetime, end: datetime) -> str:
        params = {
            "symbol": symbol.upper(),
            "interval": spec.native,
            # The API is inclusive on both ends and interprets these in the
            # exchange's timezone; a day of slack either side costs nothing
            # (the base class trims to the requested window) and avoids losing
            # edge bars to the timezone offset.
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "outputsize": MAX_OUTPUTSIZE,
            "order": "ASC",
            "format": "JSON",
            "apikey": self.api_key or "",
        }
        return f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    def _translate(self, payload: dict) -> None:
        """Twelve Data's error dialect → typed failures.

        Dispatches on the numeric `code` rather than on message text wherever
        possible: the codes are documented and stable, the wording is neither.
        """
        if str(payload.get("status", "")).lower() != "error":
            return
        code = payload.get("code")
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 0
        message = str(payload.get("message") or "").strip()
        detail = f"twelvedata: {message}" if message else "twelvedata error"
        lowered = message.lower()

        if code in (401, 403):
            raise ProviderAuthError(detail)
        if code == 429:
            # Distinguish the daily credit allowance from the per-minute rate:
            # one clears tomorrow, the other in seconds, and telling a user to
            # "wait a moment" when their day is spent is actively misleading.
            if "run out of api credits" in lowered or "daily" in lowered:
                raise ProviderQuotaExceeded(detail)
            raise ProviderQuotaExceeded(detail, retry_after=60.0)
        if code == 404:
            raise ProviderSymbolError(detail)
        if code == 400:
            # 400 covers both "no such symbol" and "that range is not on your
            # plan". They route differently — a symbol error should try the
            # next provider, a range error should not be retried — so the
            # message is the only signal available.
            if "symbol" in lowered and "not found" in lowered:
                raise ProviderSymbolError(detail)
            if "range" in lowered or "plan" in lowered or "not available" in lowered:
                raise ProviderRangeError(detail)
            raise ProviderUnavailable(detail)
        raise ProviderUnavailable(detail)

    def _parse(self, payload: dict, spec: IntervalSpec) -> pd.DataFrame:
        values = payload.get("values")
        if not values:
            return pd.DataFrame()          # `status: ok` with no rows: a holiday
        if not isinstance(values, list):
            raise ProviderUnavailable(
                "twelvedata `values` was not a list — malformed response")

        meta = payload.get("meta") or {}
        tz_name = meta.get("exchange_timezone") or meta.get("timezone")

        stamps, columns = [], {"open": [], "high": [], "low": [],
                               "close": [], "volume": []}
        for row in values:
            if not isinstance(row, dict) or "datetime" not in row:
                continue                   # a malformed row is dropped, not fatal
            stamps.append(row["datetime"])
            for key in columns:
                columns[key].append(row.get(key))
        if not stamps:
            raise ProviderUnavailable(
                "twelvedata returned rows with no usable timestamps")

        # The NATIVE interval — see the note in finnhub_provider._parse.
        timeframe = Timeframe.from_string(_APP_TF[spec.native])
        return to_frame(columns, stamps, timeframe, tz_name)

    def _probe(self) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        self._get_json(self._build_url(
            "AAPL", self.capabilities.spec(Timeframe.D1),
            now - _dt.timedelta(days=7), now))


#: Native interval -> the app timeframe it represents. Used only to tell
#: intraday bars (which need timezone conversion) from daily ones.
_APP_TF = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
           "45min": "45m", "1h": "1h", "2h": "2h", "4h": "4h",
           "1day": "1d", "1week": "1w", "1month": "1mo"}


__all__ = ["TwelveDataAdapter", "BASE_URL", "RATE_LIMIT", "MAX_OUTPUTSIZE"]
