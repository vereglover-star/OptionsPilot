"""The Yahoo chart-JSON adapter — transport, parsing and error dialect.

The reason this adapter exists at all is that it can tell the difference
between "I refuse this range", "I don't know that symbol", "slow down", and
"something broke". Those distinctions are what the service's fallback ladder is
built on, so each one is asserted against a real Yahoo error body here.

Everything runs offline against canned payloads via the injected opener.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderRangeError, ProviderRateLimited,
    ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.yahoo_provider import YahooChartAdapter, yahoo_symbols
from tests.marketdata_helpers import (
    fake_opener, sequence_opener, yahoo_error, yahoo_payload,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def req(tf=Timeframe.M5, days=5, ext=False, symbol="SPY"):
    return HistoryRequest(symbol, tf, NOW - timedelta(days=days), NOW, ext)


def adapter(body, *, status=200, record=None):
    return YahooChartAdapter(opener=fake_opener(body, status=status,
                                                record=record))


class TestSymbolSpelling:
    def test_dotted_class_shares_try_yahoos_hyphen_form(self):
        assert yahoo_symbols("BRK.B") == ["BRK.B", "BRK-B"]

    def test_ordinary_tickers_stay_on_the_single_candidate_fast_path(self):
        assert yahoo_symbols("spy") == ["SPY"]

    def test_a_404_on_the_first_spelling_falls_through_to_the_second(self):
        record: list = []
        a = YahooChartAdapter(opener=sequence_opener(
            [(404, yahoo_error("No data found, symbol may be delisted",
                               code="Not Found")),
             yahoo_payload(10, interval="5m")], record=record))
        out = a.fetch_history(req(symbol="BRK.B"), now=NOW)
        assert len(out) == 10
        assert "BRK.B" in record[0] and "BRK-B" in record[1]


class TestParsing:
    def test_column_oriented_payload_becomes_canonical_candles(self):
        out = adapter(yahoo_payload(6, interval="5m")).fetch_history(req(), now=NOW)
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]
        assert len(out) == 6
        assert out.index.tz is not None
        assert (out.index[1] - out.index[0]) == pd.Timedelta(minutes=5)

    def test_null_holes_are_dropped_not_zero_filled(self):
        """Yahoo emits null for halts and thin pre-market bars. Turning those
        into zero-priced candles would render a spike to the x-axis."""
        out = adapter(yahoo_payload(8, interval="5m", holes=(2, 5))
                      ).fetch_history(req(), now=NOW)
        assert len(out) == 6
        assert (out[["open", "high", "low", "close"]] > 0).all().all()

    def test_a_truncated_ohlc_list_is_padded_not_misaligned(self):
        """A short column would otherwise shift every later bar's price onto
        the wrong timestamp — silent, and far worse than a missing bar."""
        payload = yahoo_payload(10, interval="5m")
        quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
        quote["close"] = quote["close"][:6]
        out = adapter(payload).fetch_history(req(), now=NOW)
        assert len(out) == 6
        assert out["open"].iloc[0] == 100.0        # still aligned to bar 0

    def test_an_empty_timestamp_list_is_an_empty_frame_not_an_error(self):
        """A genuinely empty window (a market holiday) is not a failure."""
        payload = yahoo_payload(0, interval="5m")
        payload["chart"]["result"][0]["timestamp"] = []
        assert adapter(payload).fetch_history(req(), now=NOW).empty

    def test_a_downgraded_granularity_is_refused(self):
        """Yahoo answers a 1m request with daily bars under load. Rendering
        them would mislabel the whole axis."""
        payload = yahoo_payload(10, interval="5m", granularity="1d")
        with pytest.raises(ProviderRangeError, match="1d bars"):
            adapter(payload).fetch_history(req(), now=NOW)

    def test_a_non_json_body_is_a_transport_failure(self):
        with pytest.raises(ProviderUnavailable, match="non-JSON"):
            adapter(b"<html>maintenance</html>").fetch_history(req(), now=NOW)

    def test_a_json_body_without_a_chart_object_is_rejected(self):
        with pytest.raises(ProviderUnavailable):
            adapter({"unexpected": True}).fetch_history(req(), now=NOW)


class TestErrorDialect:
    def test_422_becomes_a_typed_range_error_carrying_yahoos_words(self):
        """The message that ends the ambiguity. yfinance turns this into an
        empty frame — indistinguishable from a network hiccup — which is the
        root of years of blank-chart bugs."""
        detail = ("5m data not available for startTime=1 and endTime=2. "
                  "The requested range must be within the last 60 days.")
        with pytest.raises(ProviderRangeError) as exc:
            adapter(yahoo_error(detail), status=422).fetch_history(req(), now=NOW)
        assert "within the last 60 days" in str(exc.value)

    def test_429_becomes_a_rate_limit_with_a_cooldown(self):
        with pytest.raises(ProviderRateLimited) as exc:
            adapter(yahoo_error("Too Many Requests"), status=429
                    ).fetch_history(req(), now=NOW)
        assert exc.value.retry_after > 0

    def test_404_on_every_spelling_becomes_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            adapter(yahoo_error("Not Found", code="Not Found"), status=404
                    ).fetch_history(req(symbol="NOTREAL"), now=NOW)

    def test_403_is_a_transport_failure_not_a_symbol_problem(self):
        with pytest.raises(ProviderUnavailable, match="403"):
            adapter(yahoo_error("Forbidden"), status=403).fetch_history(req(), now=NOW)

    def test_an_in_body_error_object_is_honoured_on_a_200(self):
        with pytest.raises(ProviderRangeError):
            adapter(yahoo_error("range too long")).fetch_history(req(), now=NOW)

    def test_rate_limit_and_range_errors_are_not_retried_by_the_adapter(self):
        """Retry policy belongs to the service; the adapter makes exactly one
        logical attempt so retries are never accidentally multiplied."""
        record: list = []
        a = YahooChartAdapter(opener=fake_opener(
            yahoo_error("nope"), status=422, record=record))
        with pytest.raises(ProviderRangeError):
            a.fetch_history(req(), now=NOW)
        assert len(record) == 1


class TestHostFailover:
    def test_a_transport_failure_on_query1_retries_query2(self):
        record: list = []
        a = YahooChartAdapter(opener=sequence_opener(
            [TimeoutError("query1 hung"), yahoo_payload(5, interval="5m")],
            record=record))
        out = a.fetch_history(req(), now=NOW)
        assert len(out) == 5
        assert "query1" in record[0] and "query2" in record[1]

    def test_both_hosts_failing_raises_unavailable(self):
        a = YahooChartAdapter(opener=sequence_opener([TimeoutError("dead")]))
        with pytest.raises(ProviderUnavailable, match="unreachable"):
            a.fetch_history(req(), now=NOW)

    def test_a_4xx_is_not_retried_on_the_other_host(self):
        """A refusal is an answer; asking the sibling host repeats it and
        doubles the latency of every impossible request."""
        record: list = []
        a = YahooChartAdapter(opener=fake_opener(
            yahoo_error("bad range"), status=422, record=record))
        with pytest.raises(ProviderRangeError):
            a.fetch_history(req(), now=NOW)
        assert len(record) == 1


class TestRequestShape:
    def test_epoch_period_parameters_are_used_not_range_shorthand(self):
        """`range=` cannot express an arbitrary history-paging window; only
        period1/period2 can."""
        record: list = []
        adapter(yahoo_payload(5, interval="5m"), record=record
                ).fetch_history(req(), now=NOW)
        assert "period1=" in record[0] and "period2=" in record[0]
        assert "range=" not in record[0]

    def test_extended_hours_sets_includePrePost(self):
        record: list = []
        adapter(yahoo_payload(5, interval="5m"), record=record
                ).fetch_history(req(ext=True), now=NOW)
        assert "includePrePost=true" in record[0]

    def test_rth_requests_explicitly_disable_prepost(self):
        record: list = []
        adapter(yahoo_payload(5, interval="5m"), record=record
                ).fetch_history(req(), now=NOW)
        assert "includePrePost=false" in record[0]

    def test_a_caret_index_symbol_is_not_over_escaped(self):
        record: list = []
        adapter(yahoo_payload(5, interval="1d"), record=record).fetch_history(
            HistoryRequest("^VIX", Timeframe.D1, NOW - timedelta(days=5), NOW),
            now=NOW)
        assert "/^VIX?" in record[0]


class TestSnapshot:
    def test_snapshot_reads_the_meta_block(self):
        snap = adapter(yahoo_payload(3, interval="1d")).fetch_snapshot("spy")
        assert snap.symbol == "SPY"
        assert snap.last == 123.45
        assert snap.previous_close == 122.0
        assert snap.market_state == "REGULAR"

    def test_snapshot_without_a_price_is_a_failure_not_a_zero(self):
        payload = yahoo_payload(3, interval="1d")
        payload["chart"]["result"][0]["meta"].pop("regularMarketPrice")
        with pytest.raises(ProviderUnavailable):
            adapter(payload).fetch_snapshot("SPY")


class TestProbe:
    def test_probe_succeeds_against_a_healthy_endpoint(self):
        assert adapter(yahoo_payload(2, interval="1d")).connect() is True

    def test_probe_failure_is_recorded_not_raised(self):
        a = adapter(b"nonsense")
        assert a.connect() is False
        assert a.last_error


def test_priority_places_yahoo_ahead_of_yfinance():
    from optionspilot.data.yfinance_adapter import YFinanceAdapter
    assert YahooChartAdapter().provider_priority < YFinanceAdapter().provider_priority


class TestWindowErrorsDoNotCountAsOutages:
    """A statement about the WINDOW must not be counted as a statement about
    the PROVIDER. Yahoo answers an impossible range with HTTP 400 "Data doesn't
    exist for startDate=... endDate=..."; treating that as an outage let one
    bad request trip the circuit breaker and take a perfectly healthy provider
    out of rotation (V0.5.2 self-audit)."""

    def test_data_doesnt_exist_is_a_range_error_not_an_outage(self):
        detail = ("Data doesn't exist for startDate = 1785959105, "
                  "endDate = 1786823105")
        a = adapter(yahoo_error(detail, code="Bad Request"), status=400)
        with pytest.raises(ProviderRangeError):
            a.fetch_history(req(), now=NOW)
        assert a.health().consecutive_failures == 0
        assert a.health().total_failures == 0

    def test_other_400s_are_still_treated_as_outages(self):
        a = adapter(yahoo_error("Invalid request parameters"), status=400)
        with pytest.raises(ProviderUnavailable):
            a.fetch_history(req(), now=NOW)
        assert a.health().consecutive_failures == 1
