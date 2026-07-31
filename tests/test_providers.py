"""The three keyed providers — Finnhub, Twelve Data, Alpha Vantage.

Entirely offline: every test drives a canned payload through an injected
opener, so the suite needs no API key, no network and no rate-limit budget.

The tests are organised around the four things a provider adapter is allowed
to have that is its own — URL, auth, parsing, error translation — plus the two
properties the framework guarantees for every provider (typed failures reach
higher layers, and a missing key disables rather than crashes).

The timezone block is the one to read first if you are changing an adapter.
Twelve Data and Alpha Vantage both send NAIVE LOCAL time in the exchange's
zone; reading those as UTC shifts every intraday bar by 4-5 hours and by a
*different* amount either side of a DST boundary. The result is not a visibly
broken chart, it is a subtly wrong one that also poisons the shared cache,
because bars are keyed by timestamp.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.base import SESSION_TZ
from optionspilot.data.adapter import (
    HistoryRequest, ProviderAuthError, ProviderEntitlementError,
    ProviderQuotaExceeded, ProviderRangeError, ProviderRateLimited,
    ProviderSymbolError, ProviderTimeout, ProviderUnavailable,
)
from optionspilot.data.alphavantage_provider import AlphaVantageAdapter
from optionspilot.data.config import ProviderConfig
from optionspilot.data.finnhub_provider import FinnhubAdapter
from optionspilot.data.health import (
    STATUS_AUTH_FAILED, STATUS_NO_API_KEY, STATUS_OK,
    STATUS_PREMIUM_REQUIRED,
)
from optionspilot.data.twelvedata_provider import TwelveDataAdapter
from tests.marketdata_helpers import (
    alphavantage_payload, fake_opener, finnhub_payload, twelvedata_payload,
)

NOW = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
KEYED = ProviderConfig(api_key="test-key")

#: (class, a payload its parser accepts) for the shared/parametrised tests.
ADAPTERS = [
    (FinnhubAdapter, finnhub_payload),
    (TwelveDataAdapter, twelvedata_payload),
    (AlphaVantageAdapter, alphavantage_payload),
]
ADAPTER_IDS = ["finnhub", "twelvedata", "alphavantage"]


def build(cls, body, *, status: int = 200, config=KEYED, record=None):
    return cls(config, opener=fake_opener(body, status=status, record=record),
               environ={})


def request(tf=Timeframe.M5, *, days: int = 1) -> HistoryRequest:
    return HistoryRequest("AAPL", tf, NOW - timedelta(days=days), NOW)


def fetch(adapter, tf=Timeframe.M5, *, days: int = 1) -> pd.DataFrame:
    return adapter.fetch_history(request(tf, days=days), now=NOW)


# ── credentials ──────────────────────────────────────────────────────────────

class TestMissingCredentials:
    """A missing key must be a quiet, explained absence — never a crash, and
    never a stream of doomed requests. The app ships with zero keys."""

    @pytest.mark.parametrize("cls,_payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_keyless_adapter_constructs_and_disables_itself(self, cls, _payload):
        adapter = cls(ProviderConfig(), environ={})
        status, detail = adapter.monitor.status()
        assert status == STATUS_NO_API_KEY
        assert adapter.monitor.available() is False
        assert "API key" in detail

    @pytest.mark.parametrize("cls,_payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_it_never_spends_a_request_without_a_key(self, cls, _payload):
        """The registry filters it out; this guards the adapter itself, which
        replay and the benchmark reach directly."""
        calls = []
        adapter = cls(ProviderConfig(),
                      opener=fake_opener({}, record=calls), environ={})
        assert adapter.monitor.available() is False
        assert calls == []

    @pytest.mark.parametrize("cls,_payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_every_adapter_points_at_where_to_get_a_key(self, cls, _payload):
        assert cls(ProviderConfig(), environ={}).signup_url.startswith("https://")

    @pytest.mark.parametrize("cls,_payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_configured_key_makes_it_available(self, cls, _payload):
        adapter = cls(KEYED, environ={})
        assert adapter.monitor.status()[0] == STATUS_OK
        assert adapter.monitor.api_key_configured is True


class TestKeyResolution:
    def test_the_environment_beats_the_config_file(self):
        """Environment first: it is the safer place for a secret and the one an
        operator reaches for to override a shipped config."""
        adapter = FinnhubAdapter(ProviderConfig(api_key="from-file"),
                                 environ={"FINNHUB_API_KEY": "from-env"})
        assert adapter.api_key == "from-env"

    def test_an_explicit_env_name_wins_over_the_conventional_one(self):
        adapter = FinnhubAdapter(
            ProviderConfig(api_key_env="MY_KEY"),
            environ={"MY_KEY": "explicit", "FINNHUB_API_KEY": "conventional"})
        assert adapter.api_key == "explicit"

    def test_the_config_file_is_used_when_no_variable_is_set(self):
        assert FinnhubAdapter(ProviderConfig(api_key="from-file"),
                              environ={}).api_key == "from-file"

    def test_a_blank_variable_counts_as_absent(self):
        """`FINNHUB_API_KEY=` left in a shell profile is a MISSING key. Treating
        it as present produces a confusing auth failure instead of the accurate
        'no API key configured'."""
        adapter = FinnhubAdapter(ProviderConfig(),
                                 environ={"FINNHUB_API_KEY": "   "})
        assert adapter.api_key is None
        assert adapter.monitor.status()[0] == STATUS_NO_API_KEY

    def test_each_provider_reads_its_own_conventional_variables(self):
        assert TwelveDataAdapter(
            ProviderConfig(), environ={"TWELVEDATA_API_KEY": "a"}).api_key == "a"
        assert AlphaVantageAdapter(
            ProviderConfig(), environ={"ALPHAVANTAGE_API_KEY": "b"}).api_key == "b"
        # A Finnhub variable must not leak into another provider.
        assert TwelveDataAdapter(
            ProviderConfig(), environ={"FINNHUB_API_KEY": "x"}).api_key is None


class TestKeysAreNeverLeaked:
    """The diagnostics payload is something users are explicitly invited to
    attach to public bug reports."""

    def test_as_dict_redacts_the_key_by_default(self):
        data = ProviderConfig(api_key="super-secret").as_dict()
        assert data["api_key"] == "***"
        assert "super-secret" not in json.dumps(data)

    def test_the_health_report_carries_no_key(self):
        from optionspilot.data.registry import default_registry

        registry = default_registry(environ={"FINNHUB_API_KEY": "super-secret"})
        assert "super-secret" not in json.dumps(registry.health_report())

    def test_the_whole_config_redacts(self):
        from optionspilot.data.config import MarketDataConfig

        cfg = MarketDataConfig.from_mapping(
            {"providers": {"finnhub": {"api_key": "super-secret"}}})
        assert "super-secret" not in json.dumps(cfg.as_dict())
        # ...but the adapter still receives the real one.
        assert cfg.for_provider("finnhub").resolve_api_key(environ={}) \
            == "super-secret"


# ── parsing ──────────────────────────────────────────────────────────────────

class TestFinnhubParsing:
    def test_it_parses_a_candle_body(self):
        frame = fetch(build(FinnhubAdapter, finnhub_payload(5)))
        assert len(frame) == 5
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert frame.index.tz is not None
        assert frame.index.is_monotonic_increasing

    def test_no_data_is_an_empty_frame_not_an_error(self):
        """A weekend is not an outage. Because every genuine failure path
        raises, this empty answer is authoritative."""
        frame = fetch(build(FinnhubAdapter, {"s": "no_data"}))
        assert frame.empty

    def test_unix_timestamps_land_on_utc(self):
        start = datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
        frame = fetch(build(FinnhubAdapter, finnhub_payload(3, start=start)))
        assert frame.index[0] == pd.Timestamp(start)

    def test_a_truncated_column_is_refused_rather_than_zipped(self):
        """Silently zipping mismatched arrays would pair one bar's high with
        another bar's low and produce prices that never traded."""
        payload = finnhub_payload(5)
        payload["h"] = payload["h"][:3]
        with pytest.raises(ProviderUnavailable, match="truncated"):
            fetch(build(FinnhubAdapter, payload))

    def test_a_missing_status_field_is_refused(self):
        with pytest.raises(ProviderUnavailable, match="status"):
            fetch(build(FinnhubAdapter, {"t": [1], "o": [1], "h": [1],
                                         "l": [1], "c": [1], "v": [1]}))

    def test_the_api_key_is_sent_as_a_token_parameter(self):
        calls = []
        fetch(build(FinnhubAdapter, finnhub_payload(2), record=calls))
        assert "token=test-key" in calls[0]


class TestTwelveDataParsing:
    def test_it_parses_a_values_body(self):
        frame = fetch(build(TwelveDataAdapter, twelvedata_payload(5)))
        assert len(frame) == 5
        assert frame["close"].dtype.kind == "f"      # strings coerced to floats

    def test_newest_first_rows_come_back_ascending(self):
        frame = fetch(build(TwelveDataAdapter,
                            twelvedata_payload(5, newest_first=True)))
        assert frame.index.is_monotonic_increasing

    def test_an_empty_values_list_is_an_empty_frame(self):
        frame = fetch(build(TwelveDataAdapter,
                            {"meta": {}, "values": [], "status": "ok"}))
        assert frame.empty

    def test_a_malformed_row_is_dropped_not_fatal(self):
        payload = twelvedata_payload(4)
        payload["values"].insert(1, {"open": "1"})    # no datetime
        assert len(fetch(build(TwelveDataAdapter, payload))) == 4

    def test_a_non_list_values_is_refused(self):
        with pytest.raises(ProviderUnavailable, match="malformed"):
            fetch(build(TwelveDataAdapter,
                        {"meta": {}, "values": "nonsense", "status": "ok"}))

    def test_the_api_key_is_sent(self):
        calls = []
        fetch(build(TwelveDataAdapter, twelvedata_payload(2), record=calls))
        assert "apikey=test-key" in calls[0]


class TestAlphaVantageParsing:
    def test_it_parses_an_intraday_body(self):
        frame = fetch(build(AlphaVantageAdapter, alphavantage_payload(5)))
        assert len(frame) == 5
        assert frame["volume"].iloc[0] == 1000

    def test_it_finds_the_series_key_structurally(self):
        """The key varies by function (`Time Series (5min)`, `Weekly Time
        Series`, ...), so matching on shape rather than spelling is what stops
        a future interval quietly returning zero bars."""
        payload = alphavantage_payload(3, key="Weekly Time Series")
        assert len(fetch(build(AlphaVantageAdapter, payload))) == 3

    def test_a_response_with_no_series_is_refused_not_reported_empty(self):
        """An unexpected shape must not masquerade as 'no bars in this window'
        — that would tell the chart it had reached the start of history."""
        with pytest.raises(ProviderUnavailable, match="no time series"):
            fetch(build(AlphaVantageAdapter, {"Meta Data": {}}))

    def test_an_empty_series_is_an_empty_frame(self):
        frame = fetch(build(AlphaVantageAdapter,
                            {"Meta Data": {}, "Time Series (5min)": {}}))
        assert frame.empty

    def test_daily_uses_the_daily_function_not_intraday(self):
        calls = []
        build(AlphaVantageAdapter,
              alphavantage_payload(3, interval="daily",
                                   start="2026-07-01"), record=calls)
        adapter = build(AlphaVantageAdapter,
                        alphavantage_payload(3, interval="daily",
                                             start="2026-07-01"), record=calls)
        fetch(adapter, Timeframe.D1, days=30)
        assert "function=TIME_SERIES_DAILY" in calls[-1]
        assert "interval=" not in calls[-1]


# ── the timezone contract ────────────────────────────────────────────────────

class TestTimezoneNormalisation:
    """The highest-consequence correctness property in these adapters.

    A wrong timezone does not produce a visibly broken chart. It produces a
    subtly wrong one, and because the disk cache is keyed `(symbol, timeframe,
    ts)` it also writes bars that collide or duplicate against the ones Yahoo
    and Stooq already wrote.
    """

    def test_twelvedata_intraday_is_converted_from_exchange_local_time(self):
        # 09:30 New York on 2026-07-24 (EDT, UTC-4) == 13:30 UTC.
        frame = fetch(build(TwelveDataAdapter, twelvedata_payload(
            1, start="2026-07-24 09:30:00",
            timezone_name="America/New_York")))
        assert frame.index[0] == pd.Timestamp("2026-07-24 13:30:00", tz="UTC")

    def test_alphavantage_intraday_is_converted_from_us_eastern(self):
        frame = fetch(build(AlphaVantageAdapter, alphavantage_payload(
            1, start="2026-07-24 09:30:00", timezone_name="US/Eastern")))
        assert frame.index[0] == pd.Timestamp("2026-07-24 13:30:00", tz="UTC")

    def test_the_offset_differs_across_a_dst_boundary(self):
        """The reason a fixed -5 offset is not good enough: the same wall-clock
        time is 14:30 UTC in January and 13:30 UTC in July.

        Tested against `localize` directly rather than through a fetch, because
        a January window is outside every keyed provider's intraday depth and
        would be (correctly) clamped away before it reached the parser."""
        from optionspilot.data.http_adapter import localize

        winter = localize(["2026-01-14 09:30:00"], Timeframe.M5,
                          "America/New_York")
        summer = localize(["2026-07-14 09:30:00"], Timeframe.M5,
                          "America/New_York")
        assert winter[0].hour == 14      # EST, UTC-5
        assert summer[0].hour == 13      # EDT, UTC-4

    def test_a_nonexistent_dst_hour_does_not_discard_the_response(self):
        """02:30 on a spring-forward morning does not exist locally. Shifting
        it forward loses one bar's precision; raising would throw away a whole
        response over a once-a-year edge."""
        from optionspilot.data.http_adapter import localize

        index = localize(["2026-03-08 02:30:00", "2026-03-08 04:00:00"],
                         Timeframe.M5, "America/New_York")
        assert len(index) == 2
        assert index.notna().all()

    @pytest.mark.parametrize("cls,payload", [
        (TwelveDataAdapter, lambda: twelvedata_payload(
            3, interval="1day", start="2026-07-20")),
        (AlphaVantageAdapter, lambda: alphavantage_payload(
            3, interval="daily", start="2026-07-20")),
    ], ids=["twelvedata", "alphavantage"])
    def test_daily_bars_are_stamped_at_exchange_midnight(self, cls, payload):
        """The ONE daily convention: 00:00 America/New_York (`base.session_index`).

        This test previously asserted 00:00 **UTC**, on the stated belief that
        it matched "Yahoo and Stooq". It did not. Yahoo stamps the daily bar at
        the 09:30 ET session open (13:30 UTC) and yfinance at 00:00 ET (04:00
        UTC), so a UTC-midnight bar was a THIRD instant for the same session —
        and since the cache is keyed `(symbol, timeframe, ts)`, every trading
        day accumulated a row per convention. Measured on a real cache: 6,517
        SPY daily rows for ~3,258 trading days, whose 0.40-day tightest spacing
        made `validate_history` reject the frame as "wrong interval served" and
        left every symbol on 1D stuck behind a validation screen.

        Exchange midnight (not UTC midnight) is also the only choice that reads
        correctly: the chart labels every timestamp through an America/New_York
        formatter, where 00:00 UTC is 19:00/20:00 on the PREVIOUS day.
        """
        frame = fetch(build(cls, payload()), Timeframe.D1, days=30)
        assert len(frame) == 3
        assert str(frame.index.tz) == "UTC"
        local = frame.index.tz_convert(SESSION_TZ)
        assert all(ts.hour == 0 and ts.minute == 0 for ts in local)
        # the calendar date must survive the conversion — an off-by-one here is
        # exactly what a UTC-midnight stamp produced on screen
        assert [str(ts.date()) for ts in local] == [
            "2026-07-20", "2026-07-21", "2026-07-22"]

    def test_an_unknown_timezone_falls_back_to_utc_rather_than_failing(self):
        """A chart a few hours out is recoverable and visible; a provider that
        refuses to parse at all is not."""
        frame = fetch(build(TwelveDataAdapter, twelvedata_payload(
            2, timezone_name="Mars/Olympus_Mons")))
        assert len(frame) == 2
        assert str(frame.index.tz) == "UTC"


# ── error translation ────────────────────────────────────────────────────────

class TestErrorTranslation:
    """Provider wording must never reach a higher layer. Everything above the
    adapter reacts to the typed failures and nothing else."""

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_http_401_is_an_auth_error(self, cls, _p):
        with pytest.raises(ProviderAuthError):
            fetch(build(cls, {"error": "nope"}, status=401))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_http_403_is_an_entitlement_error_not_an_auth_error(self, cls, _p):
        """403 means "I know who you are and you may not have this".

        These used to be conflated, and the conflation told users with a
        perfectly valid Finnhub key that it had been rejected. See
        `TestFinnhubEntitlement` below for the full incident.
        """
        with pytest.raises(ProviderEntitlementError):
            fetch(build(cls, {"error": "nope"}, status=403))
        # Specifically NOT an auth error — the two must stay distinguishable by
        # `except`, which is how the service and the monitor tell them apart.
        assert not issubclass(ProviderEntitlementError, ProviderAuthError)

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_http_429_backs_off(self, cls, _p):
        with pytest.raises(ProviderRateLimited):
            fetch(build(cls, {"error": "slow down"}, status=429))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_errors_are_unavailable(self, cls, _p, status):
        with pytest.raises(ProviderUnavailable):
            fetch(build(cls, {"error": "boom"}, status=status))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_non_json_body_is_refused_not_parsed(self, cls, _p):
        """A captive portal or proxy error page must never be read as prices."""
        with pytest.raises(ProviderUnavailable, match="malformed"):
            fetch(build(cls, "<html>Access Denied</html>"))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_an_empty_body_is_refused(self, cls, _p):
        with pytest.raises(ProviderUnavailable):
            fetch(build(cls, ""))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_json_array_body_is_refused(self, cls, _p):
        with pytest.raises(ProviderUnavailable, match="unexpected payload"):
            fetch(build(cls, "[1,2,3]"))

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_timeout_is_reported_as_a_timeout(self, cls, _p):
        def timing_out(request, timeout=None):
            raise TimeoutError("timed out")

        adapter = cls(KEYED, opener=timing_out, environ={})
        with pytest.raises(ProviderTimeout):
            fetch(adapter)
        assert adapter.monitor.snapshot()["timeouts"] == 1

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_no_raw_provider_wording_escapes_untyped(self, cls, _p):
        """Whatever the provider says, the exception is one of ours."""
        from optionspilot.data.adapter import ProviderError

        with pytest.raises(ProviderError):
            fetch(build(cls, {"error": "Ünexpected provider prose",
                              "status": "error", "code": 400,
                              "message": "Ünexpected provider prose",
                              "Error Message": "Ünexpected provider prose"}))


class TestFinnhubErrors:
    def test_an_access_message_is_an_entitlement_error(self):
        """"You don't have access to this resource" is about the PLAN.

        It mentions neither a key nor a plan, which is why it used to fall
        through a broad `"api key"` marker and be reported as a rejected key.
        """
        with pytest.raises(ProviderEntitlementError):
            fetch(build(FinnhubAdapter,
                        {"error": "You don't have access to this resource."}))

    def test_an_invalid_key_message_is_still_an_auth_error(self):
        """The other half of the split: authentication must not be weakened.

        This is Finnhub's verbatim 401 body, measured live.
        """
        with pytest.raises(ProviderAuthError):
            fetch(build(FinnhubAdapter, {"error": "Invalid API key."}))

    def test_a_missing_key_message_is_an_auth_error(self):
        with pytest.raises(ProviderAuthError):
            fetch(build(FinnhubAdapter, {"error": "Please use an API key."}))

    def test_a_limit_message_backs_off(self):
        with pytest.raises(ProviderRateLimited):
            fetch(build(FinnhubAdapter, {"error": "API limit reached."}))

    def test_an_unknown_status_is_unavailable(self):
        with pytest.raises(ProviderUnavailable):
            fetch(build(FinnhubAdapter, {"s": "error"}))


class TestTwelveDataErrors:
    """Twelve Data reports errors with HTTP 200. Any integration that trusts
    the status line silently treats an error document as data."""

    def _error(self, code, message):
        return {"code": code, "message": message, "status": "error"}

    def test_a_200_error_document_is_still_an_error(self):
        with pytest.raises(ProviderQuotaExceeded):
            fetch(build(TwelveDataAdapter,
                        self._error(429, "You have run out of API credits")))

    def test_credits_exhausted_is_a_quota_error_not_a_short_backoff(self):
        """Telling a user to 'wait a moment' when their day is spent is
        actively misleading, so the two are distinguished."""
        with pytest.raises(ProviderQuotaExceeded) as caught:
            fetch(build(TwelveDataAdapter,
                        self._error(429, "You have run out of API credits")))
        assert caught.value.retry_after > 60.0

    def test_a_plain_minute_limit_retries_sooner(self):
        with pytest.raises(ProviderQuotaExceeded) as caught:
            fetch(build(TwelveDataAdapter,
                        self._error(429, "API rate limit exceeded")))
        assert caught.value.retry_after == 60.0

    def test_401_is_an_auth_error(self):
        with pytest.raises(ProviderAuthError):
            fetch(build(TwelveDataAdapter,
                        self._error(401, "Invalid API key")))

    def test_404_is_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            fetch(build(TwelveDataAdapter, self._error(404, "Not found")))

    def test_a_400_symbol_message_is_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            fetch(build(TwelveDataAdapter,
                        self._error(400, "**symbol** not found: ZZZZ")))

    def test_a_400_plan_message_is_a_range_error(self):
        """Not retryable and not the provider's fault — a deeper provider may
        still help."""
        with pytest.raises(ProviderRangeError):
            fetch(build(TwelveDataAdapter,
                        self._error(400, "This range is not available on your plan")))


class TestAlphaVantageErrors:
    def test_the_note_key_is_a_rate_limit(self):
        with pytest.raises(ProviderQuotaExceeded):
            fetch(build(AlphaVantageAdapter,
                        {"Note": "Thank you for using Alpha Vantage! Our "
                                 "standard API call frequency is 5 calls per "
                                 "minute"}))

    def test_the_information_key_carrying_the_daily_cap_is_a_quota_error(self):
        with pytest.raises(ProviderQuotaExceeded) as caught:
            fetch(build(AlphaVantageAdapter,
                        {"Information": "We have detected your API key and our "
                                        "standard API rate limit is 25 requests "
                                        "per day."}))
        assert caught.value.retry_after > 60.0

    def test_the_information_key_carrying_an_invalid_key_is_an_auth_error(self):
        """Alpha Vantage uses ONE key for both, so the text is the only
        discriminator available."""
        with pytest.raises(ProviderAuthError):
            fetch(build(AlphaVantageAdapter,
                        {"Information": "Invalid API call. Please retry or "
                                        "visit the documentation."}))

    def test_an_error_message_about_the_symbol_is_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            fetch(build(AlphaVantageAdapter,
                        {"Error Message": "Invalid API call. Please check the "
                                          "symbol parameter."}))

    def test_an_error_message_about_the_key_is_an_auth_error(self):
        with pytest.raises(ProviderAuthError):
            fetch(build(AlphaVantageAdapter,
                        {"Error Message": "the parameter apikey is invalid"}))


# ── entitlement vs authentication (V0.5.7) ───────────────────────────────────

class TestFinnhubEntitlement:
    """Finnhub moved `/stock/candle` behind a paid plan; free keys get 403.

    The incident this suite pins down, in full, because the code alone does not
    convey why the distinction is worth a whole exception class:

      A user created a brand-new Finnhub account, verified the email, copied
      the key straight from the dashboard, and pasted it in. Every attempt
      returned HTTP 403. Twelve Data and Alpha Vantage authenticated fine with
      their own keys, so nothing was wrong with the machine, the network or the
      credential store. The app reported **"the API key was rejected"** — so the
      user regenerated the key. Repeatedly. It could never have worked: the key
      was valid the entire time, and the plan simply does not include candles.

    Measured live against the API on 2026-07-27:

        invalid key    ->  401  {"error":"Invalid API key."}
        no key at all  ->  401  {"error":"Please use an API key."}
        valid free key ->  403  {"error":"You don't have access to this resource."}

    401 is therefore the ONLY status Finnhub uses for a key problem, which makes
    a 403 positive evidence that the key is good. Every test below asserts one
    consequence of taking that seriously.
    """

    def _entitled(self, status=403, body=None):
        return build(FinnhubAdapter,
                     body if body is not None
                     else {"error": "You don't have access to this resource."},
                     status=status)

    # ── classification ───────────────────────────────────────────────────────

    def test_a_403_does_not_mark_the_key_as_rejected(self):
        """The headline regression. `auth_failed` drives the message the user
        reads, and setting it here is what sent them to regenerate a good key."""
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        assert adapter.monitor.auth_failed is False
        assert adapter.monitor.entitlement_failed is True

    def test_a_403_reports_premium_required_not_auth_failure(self):
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        status, detail = adapter.monitor.status()
        assert status == STATUS_PREMIUM_REQUIRED
        assert "valid" in detail and "plan" in detail
        # And the one-word state a human reads.
        assert adapter.monitor.health_state()[0] == "premium_required"

    def test_the_explanation_tells_the_user_not_to_fix_anything(self):
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        explanation = adapter.monitor.health_state()[1]
        assert "works" in explanation
        assert "nothing to fix" in explanation

    def test_a_401_still_marks_the_key_as_rejected(self):
        """Authentication must not be weakened by any of this."""
        adapter = build(FinnhubAdapter, {"error": "Invalid API key."}, status=401)
        with pytest.raises(ProviderAuthError):
            fetch(adapter)
        assert adapter.monitor.auth_failed is True
        assert adapter.monitor.entitlement_failed is False
        assert adapter.monitor.status()[0] == STATUS_AUTH_FAILED

    def test_the_failure_is_counted_under_its_own_kind(self):
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        snap = adapter.monitor.snapshot()
        assert snap["entitlement_failures"] == 1
        assert snap["auth_failures"] == 0
        assert snap["premium_required"] is True

    # ── behaviour in the chain ───────────────────────────────────────────────

    def test_it_leaves_rotation_and_is_not_retried(self):
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        assert adapter.monitor.available() is False
        spendable, why = adapter.can_spend_request()
        assert spendable is False
        assert "plan" in why

    def test_it_is_sticky_so_the_403_is_learned_once(self):
        """No number of attempts turns a free plan into a paid one, and every
        attempt is a real request spent proving that again."""
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        calls_after_first = len(adapter.monitor.by_kind)
        assert adapter.can_spend_request()[0] is False
        assert calls_after_first  # the verdict persists without another request

    def test_a_new_key_clears_the_verdict(self):
        """A different key may be on a different plan — the single most likely
        reason someone pastes one after seeing this."""
        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        adapter.set_api_key("a-brand-new-key", environ={})
        assert adapter.monitor.entitlement_failed is False
        assert adapter.monitor.available() is True

    def test_it_contributes_no_history_floor(self):
        """Finnhub declares 180 days of 5-minute history and, on a free plan,
        can serve none of it. Counting that floor would tell the chart history
        reaches three times further than anything reachable — the retry-forever
        bug class V0.5.2 was built to eliminate."""
        from optionspilot.data.registry import ProviderRegistry

        adapter = self._entitled()
        with pytest.raises(ProviderEntitlementError):
            fetch(adapter)
        registry = ProviderRegistry([adapter])
        assert registry.deepest_earliest("AAPL", Timeframe.M5, NOW) is None
        assert adapter.monitor.permanently_unusable is True

    def test_a_healthy_provider_is_not_permanently_unusable(self):
        adapter = build(FinnhubAdapter, finnhub_payload(3))
        fetch(adapter)
        assert adapter.monitor.permanently_unusable is False

    def test_a_temporarily_benched_provider_still_contributes_a_floor(self):
        """The other side of the distinction: a breaker opens and closes, and
        the reported start of history must not lurch about with it."""
        from optionspilot.data.registry import ProviderRegistry

        adapter = build(FinnhubAdapter, finnhub_payload(3))
        adapter.monitor.force_open(300)
        assert adapter.monitor.available() is False
        assert adapter.monitor.permanently_unusable is False
        registry = ProviderRegistry([adapter])
        assert registry.deepest_earliest("AAPL", Timeframe.M5, NOW) is not None

    def test_the_chain_fails_over_past_it(self):
        """A premium-gated Finnhub must cost the user nothing — the keyless
        providers in front of it are unaffected."""
        from optionspilot.data.registry import ProviderRegistry
        from optionspilot.data.service import MarketDataService
        from tests.marketdata_helpers import ScriptedAdapter, frame

        finnhub = self._entitled()
        finnhub.provider_priority = 40
        finnhub.monitor.priority = 40
        keyless = ScriptedAdapter("yahoo", [frame(20, Timeframe.M5)], priority=10)
        service = MarketDataService(ProviderRegistry([keyless, finnhub]))
        result = service.get_history("AAPL", Timeframe.M5,
                                     NOW - timedelta(days=1), NOW)
        assert result.ok and result.provider == "yahoo"

    # ── credential verification on a free endpoint ───────────────────────────

    def test_finnhub_can_check_its_key_without_asking_for_history(self):
        assert FinnhubAdapter.can_verify_credentials is True

    def test_the_quote_endpoint_confirms_a_good_key(self):
        """`/quote` is what the free tier DOES include, so it isolates the
        variable: quote 200 + candle 403 == key good, plan too small."""
        adapter = build(FinnhubAdapter, {"c": 213.5, "o": 212.0, "h": 214.0,
                                         "l": 211.0, "pc": 212.5})
        accepted, note = adapter.verify_credentials()
        assert accepted is True
        assert "accepted" in note

    def test_the_quote_endpoint_reports_a_genuinely_bad_key(self):
        adapter = build(FinnhubAdapter, {"error": "Invalid API key."}, status=401)
        accepted, note = adapter.verify_credentials()
        assert accepted is False
        assert "Invalid API key" in note

    def test_the_verification_request_goes_to_the_quote_endpoint(self):
        """Asking `/stock/candle` again would answer the question with the
        thing that already failed."""
        from optionspilot.data.finnhub_provider import QUOTE_URL

        seen: list[str] = []
        adapter = build(FinnhubAdapter, {"c": 1.0}, record=seen)
        adapter.verify_credentials()
        assert len(seen) == 1
        assert seen[0].startswith(QUOTE_URL)
        assert "resolution=" not in seen[0]

    def test_an_unrecognisable_quote_shape_is_not_treated_as_success(self):
        adapter = build(FinnhubAdapter, {"unexpected": True})
        assert adapter.verify_credentials()[0] is False

    def test_other_providers_do_not_claim_a_credential_check(self):
        """The default is honest about having no cheaper endpoint to ask,
        rather than guessing."""
        for cls in (TwelveDataAdapter, AlphaVantageAdapter):
            adapter = cls(KEYED, environ={})
            assert adapter.can_verify_credentials is False
            assert adapter.verify_credentials()[0] is False


# ── health integration ───────────────────────────────────────────────────────

class TestHealthIntegration:
    """The framework guarantees these without any provider-specific code."""

    @pytest.mark.parametrize("cls,payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_successful_fetch_is_recorded(self, cls, payload):
        adapter = build(cls, payload(3))
        fetch(adapter)
        snap = adapter.monitor.snapshot()
        assert snap["successes"] == 1 and snap["requests"] == 1
        assert snap["status"] == STATUS_OK

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_an_auth_failure_takes_the_provider_out_of_rotation(self, cls, _p):
        """Sticky: re-testing a rejected key on every chart load spends
        requests to learn something already known."""
        adapter = build(cls, {"error": "bad key"}, status=401)
        with pytest.raises(ProviderAuthError):
            fetch(adapter)
        assert adapter.monitor.status()[0] == STATUS_AUTH_FAILED
        assert adapter.monitor.available() is False
        assert adapter.monitor.snapshot()["auth_failures"] == 1

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_an_auth_failure_clears_when_the_key_changes(self, cls, _p):
        adapter = build(cls, {"error": "bad key"}, status=401)
        with pytest.raises(ProviderAuthError):
            fetch(adapter)
        adapter.monitor.clear_auth_failure()
        assert adapter.monitor.available() is True

    def test_a_reported_quota_error_exhausts_the_local_budget(self):
        """A live quota error is authoritative over our own count, which can
        drift low when the same key is used by another install."""
        adapter = build(AlphaVantageAdapter,
                        {"Information": "our standard API rate limit is 25 "
                                        "requests per day"})
        with pytest.raises(ProviderQuotaExceeded):
            fetch(adapter)
        assert adapter.quota.state()["remaining_today"] == 0
        assert adapter.monitor.status()[0] == "quota_exceeded"

    @pytest.mark.parametrize("cls,payload", ADAPTERS, ids=ADAPTER_IDS)
    def test_every_request_is_counted_against_the_budget(self, cls, payload):
        adapter = build(cls, payload(2))
        before = adapter.quota.state()["used_today"]
        fetch(adapter)
        assert adapter.quota.state()["used_today"] == before + 1

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_a_failed_request_still_costs_budget(self, cls, _p):
        """It consumed quota upstream whether or not it succeeded."""
        adapter = build(cls, {"error": "boom"}, status=500)
        with pytest.raises(ProviderUnavailable):
            fetch(adapter)
        assert adapter.quota.state()["used_today"] == 1


class TestCapabilities:
    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_every_app_timeframe_is_served(self, cls, _p):
        """Either natively or by resampling — a gap would silently drop that
        timeframe from the provider's candidate list."""
        adapter = cls(KEYED, environ={})
        missing = [tf for tf in Timeframe if not adapter.supports_interval(tf)]
        assert missing == []

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_an_out_of_depth_request_costs_no_network_call(self, cls, _p):
        """The capability table answers it; this is what stops a scroll into
        old intraday data burning a metered request per scroll."""
        calls = []
        adapter = cls(KEYED, opener=fake_opener({}, record=calls), environ={})
        with pytest.raises(ProviderRangeError):
            adapter.fetch_history(
                HistoryRequest("AAPL", Timeframe.M5,
                               NOW - timedelta(days=3650),
                               NOW - timedelta(days=3600)), now=NOW)
        assert calls == []

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_capabilities_describe_themselves_for_diagnostics(self, cls, _p):
        caps = cls(KEYED, environ={}).capabilities
        assert caps.description
        assert caps.asset_classes
        assert caps.requests_per_minute

    @pytest.mark.parametrize("cls,_p", ADAPTERS, ids=ADAPTER_IDS)
    def test_none_advertise_extended_hours(self, cls, _p):
        """None of the three serves pre/post-market on a free plan, and
        claiming otherwise would route extended-hours requests to a provider
        that answers with regular-hours bars."""
        assert cls(KEYED, environ={}).capabilities.extended_hours is False


class TestResampling:
    """Timeframes no provider serves natively come from the framework, not
    from provider code."""

    def test_finnhub_resamples_10m_from_5m(self):
        calls = []
        adapter = build(FinnhubAdapter,
                        finnhub_payload(12, interval_seconds=300), record=calls)
        frame = fetch(adapter, Timeframe.M10)
        assert "resolution=5" in calls[0]
        assert len(frame) < 12          # aggregated up
        assert not frame.empty

    def test_twelvedata_serves_4h_natively(self):
        """Twelve Data has native 2h/4h where Yahoo does not, so fewer app
        timeframes need resampling at all."""
        calls = []
        adapter = build(TwelveDataAdapter,
                        twelvedata_payload(6, interval="4h",
                                           start="2026-07-20 09:30:00"),
                        record=calls)
        fetch(adapter, Timeframe.H4, days=20)
        assert "interval=4h" in calls[0]


class TestNoPathSpendsADoomedRequest:
    """Every code path that spends an upstream request must consult
    `adapter.can_spend_request()`.

    Two originally checked only the circuit breaker and so fired real requests
    at providers with no API key — collecting 401s, marking them auth-failed
    (which is sticky) and poisoning the health of providers the user had never
    configured. These tests exist so a fifth path cannot repeat it.
    """

    def _wired(self, calls):
        """The shipped registry with every keyed adapter's transport
        instrumented, so any outbound attempt is visible."""
        from optionspilot.data.registry import default_registry

        registry = default_registry(environ={})
        for name in ("finnhub", "twelvedata", "alphavantage"):
            def opener(request, timeout=None, _n=name):
                calls.append(_n)
                raise RuntimeError("a network call was made")
            registry.get(name)._opener = opener
        return registry

    def test_the_gate_refuses_an_unconfigured_provider(self):
        adapter = FinnhubAdapter(ProviderConfig(), environ={})
        spendable, reason = adapter.can_spend_request()
        assert spendable is False
        assert "API key" in reason

    def test_the_gate_refuses_an_exhausted_budget(self):
        adapter = AlphaVantageAdapter(KEYED, environ={})
        adapter.quota.exhaust_day()
        assert adapter.can_spend_request()[0] is False

    def test_the_gate_still_permits_an_open_breaker(self):
        """Narrower than `available()` on purpose: a debugging tool may want
        the answer behind a temporary benching."""
        adapter = FinnhubAdapter(KEYED, environ={})
        adapter.monitor.force_open(300.0)
        assert adapter.monitor.available() is False
        assert adapter.can_spend_request()[0] is True

    def test_replay_does_not_poll_unconfigured_providers(self):
        from optionspilot.data.replay import compare_providers

        calls = []
        registry = self._wired(calls)
        result = compare_providers(
            registry,
            HistoryRequest("SPY", Timeframe.D1, NOW - timedelta(days=5), NOW),
            now=NOW)

        assert calls == []
        keyed = {a.provider: a for a in result.answers
                 if a.provider in ("finnhub", "twelvedata", "alphavantage")}
        assert len(keyed) == 3
        for answer in keyed.values():
            assert "API key" in answer.skipped
            assert answer.error == ""      # reported as absent, not as broken

    def test_replay_leaves_unconfigured_providers_healthy(self):
        """A 401 from a doomed probe would mark them auth-failed, which is
        sticky and would survive the user later adding a real key."""
        from optionspilot.data.replay import compare_providers

        registry = self._wired([])
        compare_providers(
            registry,
            HistoryRequest("SPY", Timeframe.D1, NOW - timedelta(days=5), NOW),
            now=NOW)
        for name in ("finnhub", "twelvedata", "alphavantage"):
            monitor = registry.get(name).monitor
            assert monitor.auth_failed is False
            assert monitor.requests == 0

    def test_discovery_does_not_probe_unconfigured_providers(self, tmp_path):
        """Probing costs ~a dozen doomed requests PER INTERVAL and then reports
        'served nothing at any depth' for all of them — which reads as an
        outage rather than the missing credential it is."""
        from optionspilot.data.discovery import CapabilityStore, refresh_if_stale

        calls = []
        registry = self._wired(calls)
        store = CapabilityStore(tmp_path / "caps.json")
        result = refresh_if_stale(registry.get("alphavantage"), store,
                                  refresh_days=30, now=NOW, pause=0)

        assert calls == []
        assert result is not None and result.intervals == {}

    def test_discovery_still_probes_a_configured_provider(self, tmp_path):
        from optionspilot.data.discovery import discover

        adapter = build(FinnhubAdapter, finnhub_payload(3))
        result = discover(adapter, "AAPL", timeframes=[Timeframe.D1],
                          now=NOW, pause=0)
        assert result.intervals["1d"].probes > 0
