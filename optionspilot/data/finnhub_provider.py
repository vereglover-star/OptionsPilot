"""Finnhub candle adapter.

    GET /api/v1/stock/candle?symbol=AAPL&resolution=5&from=<unix>&to=<unix>&token=<key>

Response is column-oriented and compact:

    {"s": "ok", "t": [unix...], "o": [...], "h": [...], "l": [...],
     "c": [...], "v": [...]}

## READ THIS FIRST: `/stock/candle` is a PAID endpoint (verified 2026-07-27)

**Finnhub's free tier no longer includes historical stock candles.** A brand-new,
email-verified, correctly-pasted free key gets HTTP 403 from this endpoint,
every time. Nothing in this adapter, and nothing the user can do to their key,
changes that — it is a plan entitlement.

The evidence, measured live against the API rather than inferred:

    invalid key   ->  401  {"error":"Invalid API key."}
    no key at all ->  401  {"error":"Please use an API key."}
    valid free key->  403  {"error":"You don't have access to this resource."}

**401 is the ONLY status Finnhub uses for a key problem.** So a 403 from this
endpoint is positive evidence that the key is *good* — the server authenticated
it and then declined to serve the data. The free tier still covers `/quote`,
`/stock/profile2`, company news and symbol search; it is specifically the
historical OHLC endpoints (and intraday resolutions) that moved to paid.

This adapter previously treated 401 and 403 alike and reported both as "the API
key was rejected", which told users with a perfectly valid key to go and
regenerate it. `_from_status` in `http_adapter.py` now splits them, this adapter
raises `ProviderEntitlementError` for the entitlement case, and
`verify_credentials()` below proves the key on the free `/quote` endpoint so the
control centre can say the true and useful thing:

    "Your API key is valid. Your Finnhub plan does not include historical
     price data, so this provider cannot be used for charts."

**Consequence for the provider chain.** On a free key, Finnhub can never serve
this application's only requirement. It is therefore benched *permanently*
(`monitor.entitlement_failed`), contributes **no `deepest_earliest` history
floor** — telling the chart that 5-minute bars reach back 180 days when nothing
reachable goes past 59 is exactly the retry-forever bug class V0.5.2 was built
to eliminate — and it is never retried until the key changes. It remains listed
and self-explaining, like any other benched provider.

Two further things worth knowing before changing anything here:

**`s` is the real status, not the HTTP code.** A window with no bars returns
HTTP 200 with `{"s": "no_data"}`. That is a legitimate empty answer (a weekend,
a holiday, a pre-listing range), not a failure — and because every genuine
failure path in this adapter raises, `reports_empty_reliably` is True and the
service can stop the provider chain on it.

**Timestamps are unix seconds, UTC.** Unlike Twelve Data and Alpha Vantage,
there is no exchange-local naive time to convert, which removes the entire DST
class of bug for this provider.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    ProviderAuthError, ProviderEntitlementError, ProviderError,
    ProviderQuotaExceeded, ProviderRateLimited, ProviderSymbolError,
    ProviderUnavailable,
)
from optionspilot.data.capabilities import FINNHUB_CAPABILITIES, IntervalSpec
from optionspilot.data.http_adapter import KeyedHTTPAdapter, to_frame
from optionspilot.data.ratelimit import RateLimitPolicy

log = get_logger("data")

BASE_URL = "https://finnhub.io/api/v1/stock/candle"
#: The cheapest endpoint Finnhub's FREE tier definitely includes. Used only to
#: answer "is this key valid?" when `/stock/candle` has refused — see
#: `verify_credentials`. It costs one request and returns a tiny body.
QUOTE_URL = "https://finnhub.io/api/v1/quote"
REQUEST_TIMEOUT = 10.0

#: Free tier: 60 calls/minute, no published daily ceiling.
RATE_LIMIT = RateLimitPolicy(per_minute=60, per_day=None)

#: Body fragments that mean **the key itself** is wrong. Deliberately narrow:
#: only the wordings Finnhub actually uses for an unusable key. A broad `"api
#: key"` substring used to live here and matched the entitlement message too,
#: which is half of how a valid key came to be reported as rejected.
_AUTH_MARKERS = ("invalid api key", "please use an api key", "unauthorized")

#: Body fragments that mean the key is FINE and the plan is not. Checked before
#: the auth markers, because Finnhub's entitlement wording mentions neither a
#: key nor a plan and would otherwise fall through to "unavailable".
_ENTITLEMENT_MARKERS = ("don't have access", "do not have access",
                        "premium", "upgrade", "not available under your plan")


class FinnhubAdapter(KeyedHTTPAdapter):
    """Finnhub stock candles. Priority 40 — first of the keyed providers."""

    provider_name = "finnhub"
    provider_priority = 40
    capabilities = FINNHUB_CAPABILITIES
    default_timeout = REQUEST_TIMEOUT
    rate_limit = RATE_LIMIT
    api_key_env_vars = ("FINNHUB_API_KEY", "OPTIONSPILOT_FINNHUB_API_KEY")
    signup_url = "https://finnhub.io/register"
    # Measured 2026-07-27: a free key authenticates and is then refused
    # `/stock/candle` with HTTP 403. So Finnhub must NOT be the provider the
    # app suggests to someone who needs a second source — see
    # `docs/MARKET_DATA.md` §41. Nothing is gated by this; it only affects
    # advice, and a paid key still works normally.
    free_tier_serves_history = False
    # Every failure path below raises, so an empty frame can only mean the
    # window genuinely holds no bars (`"s": "no_data"`).
    reports_empty_reliably = True
    # 60/min is one per second; pace slightly under it so a burst of chart
    # switches cannot trip the published limit.
    min_request_interval = 0.02

    # ── provider specifics ───────────────────────────────────────────────────

    def _build_url(self, symbol: str, spec: IntervalSpec,
                   start: datetime, end: datetime) -> str:
        params = {
            "symbol": symbol.upper(),
            "resolution": spec.native,
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "token": self.api_key or "",
        }
        return f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    def _translate_error(self, payload: dict) -> None:
        """Finnhub's `error` field → typed failures. Applies to EVERY endpoint.

        Split out from `_translate` because it is the only part that is not
        specific to the candle response: `verify_credentials` hits `/quote`,
        which has no `s` field, and running the full candle validator over it
        would reject a perfectly good quote for not looking like a candle.

        Order matters and is not arbitrary: entitlement is checked BEFORE the
        key markers, because "You don't have access to this resource" is about
        the plan and mentions neither a key nor a plan by name. Getting this
        order wrong is how a valid key gets reported as rejected.
        """
        error = str(payload.get("error") or "").strip()
        if not error:
            return
        lowered = error.lower()
        if "limit" in lowered or "quota" in lowered:
            raise ProviderRateLimited(f"finnhub: {error}", retry_after=60.0)
        if any(marker in lowered for marker in _ENTITLEMENT_MARKERS):
            raise ProviderEntitlementError(f"finnhub: {error}")
        if any(marker in lowered for marker in _AUTH_MARKERS):
            raise ProviderAuthError(f"finnhub: {error}")
        raise ProviderUnavailable(f"finnhub: {error}")

    def _translate(self, payload: dict) -> None:
        """The candle response's own validation, on top of the shared errors."""
        self._translate_error(payload)

        status = str(payload.get("s") or "").strip().lower()
        if status == "no_data":
            return                      # legitimate empty window; `_parse` yields 0 bars
        if status and status != "ok":
            raise ProviderUnavailable(f"finnhub returned status {status!r}")
        if not status:
            raise ProviderUnavailable(
                "finnhub response carried no status field")

    def _parse(self, payload: dict, spec: IntervalSpec) -> pd.DataFrame:
        if str(payload.get("s", "")).lower() == "no_data":
            return pd.DataFrame()
        stamps = payload.get("t") or []
        if not stamps:
            return pd.DataFrame()
        columns = {"open": payload.get("o"), "high": payload.get("h"),
                   "low": payload.get("l"), "close": payload.get("c"),
                   "volume": payload.get("v")}
        # A short column means a truncated response. Refusing is the right
        # answer: silently zipping mismatched arrays would pair one bar's high
        # with another bar's low and produce prices that never traded.
        for name, values in columns.items():
            if values is None or len(values) != len(stamps):
                raise ProviderUnavailable(
                    f"finnhub sent {len(stamps)} timestamps but "
                    f"{0 if values is None else len(values)} {name} values "
                    "— truncated response")
        # The NATIVE interval, not `spec.resample`: these are the raw bars,
        # and the base class aggregates them afterwards. `localize` only
        # needs to know whether they are intraday or daily.
        timeframe = Timeframe.from_string(_APP_TF[spec.native])
        # Unix seconds, already UTC — no exchange-local conversion needed.
        index = pd.to_datetime(pd.Series(stamps), unit="s", utc=True)
        return to_frame(columns, index, timeframe, "UTC")

    def _probe(self) -> None:
        """A minimal request proving the key works and the endpoint answers."""
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        payload = self._get_json(self._build_url(
            "AAPL", self.capabilities.spec(Timeframe.D1),
            now - _dt.timedelta(days=7), now))
        if not payload:
            raise ProviderUnavailable("finnhub probe returned nothing")

    # ── credential verification (V0.5.7) ─────────────────────────────────────

    can_verify_credentials = True

    def verify_credentials(self) -> tuple[bool, str]:
        """Prove the key on `/quote`, which the FREE tier includes.

        This exists because `/stock/candle` cannot answer the question. A 403
        from it is ambiguous to a user — "is my key wrong, or is my plan too
        small?" — and the two have opposite remedies. `/quote` is the cheapest
        endpoint Finnhub grants every plan, so its answer isolates the variable:

            /quote 200  +  /stock/candle 403   ->  key good, plan too small
            /quote 401                         ->  key genuinely bad

        Returns (accepted, detail) rather than raising: the caller is a
        diagnostic that has already handled one failure and wants a second
        data point, not another exception to catch.

        The empty-body check matters. Finnhub answers `/quote` for an unknown
        symbol with all-zero fields rather than an error, so "we got JSON back"
        is not on its own proof of anything — but `c` (current price) being
        present at all IS proof the request was authorised, which is the only
        thing being asked here.
        """
        url = f"{QUOTE_URL}?{urllib.parse.urlencode({'symbol': 'AAPL', 'token': self.api_key or ''})}"
        try:
            # `_translate_error`, not the full `_translate`: a quote document
            # has no `s` field and the candle validator would refuse it.
            payload = self._get_json(url, translate=self._translate_error)
        except ProviderAuthError as exc:
            return False, str(exc)
        except ProviderEntitlementError as exc:
            # Would mean even /quote is now paid — worth reporting verbatim
            # rather than translating into a guess.
            return False, str(exc)
        except ProviderError as exc:
            return False, f"could not check the key: {exc}"
        if "c" not in payload:
            return False, "finnhub's quote endpoint returned an unexpected shape"
        return True, "the API key was accepted by Finnhub's quote endpoint"


#: Native resolution -> the app timeframe it represents, for frames that are
#: NOT resampled. (`spec.resample` names the target for the ones that are.)
_APP_TF = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h",
           "D": "1d", "W": "1w", "M": "1mo"}


__all__ = ["FinnhubAdapter", "BASE_URL", "QUOTE_URL", "RATE_LIMIT"]
