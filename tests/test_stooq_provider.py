"""The Stooq CSV adapter — the only source in the chain independent of Yahoo.

Its value is entirely in that independence, so the tests focus on the two ways
that value could be lost: silently parsing something that is not price data
(an anti-bot HTML page, which some networks serve instead of CSV), and quietly
claiming to serve intervals it cannot.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryRequest, ProviderRangeError, ProviderRateLimited,
    ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.stooq_provider import StooqAdapter, stooq_symbol
from tests.marketdata_helpers import fake_opener, stooq_csv

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def req(tf=Timeframe.D1, days=30, symbol="SPY"):
    return HistoryRequest(symbol, tf, NOW - timedelta(days=days), NOW)


def adapter(body, *, status=200, record=None):
    return StooqAdapter(opener=fake_opener(body, status=status, record=record))


class TestSymbolMapping:
    def test_us_equities_get_the_us_suffix(self):
        assert stooq_symbol("SPY") == "spy.us"
        assert stooq_symbol("aapl") == "aapl.us"

    def test_dotted_class_shares_use_a_hyphen(self):
        assert stooq_symbol("BRK.B") == "brk-b.us"


class TestScope:
    def test_intraday_intervals_are_refused_before_any_request(self):
        """Declaring an interval it cannot serve would make the registry route
        intraday traffic into a dead end."""
        record: list = []
        a = adapter(stooq_csv(), record=record)
        with pytest.raises(ProviderRangeError):
            a.fetch_history(req(Timeframe.M5), now=NOW)
        assert record == []

    def test_daily_weekly_and_monthly_are_served(self):
        for tf in (Timeframe.D1, Timeframe.W1, Timeframe.MN1):
            assert StooqAdapter().supports_interval(tf)

    def test_extended_hours_is_never_claimed(self):
        assert not StooqAdapter().supports_extended_hours(Timeframe.D1)


class TestParsing:
    def test_csv_becomes_canonical_candles(self):
        out = adapter(stooq_csv(5)).fetch_history(req(), now=NOW)
        assert len(out) == 5
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]
        assert out.index.tz is not None
        assert out["close"].iloc[0] == 100.5

    def test_daily_bars_land_at_midnight_utc_like_every_other_source(self):
        """The cache is keyed on the bar's timestamp, so a daily bar written by
        Stooq and one written by Yahoo must land on the same key or the two
        would double-store and disagree."""
        out = adapter(stooq_csv(3)).fetch_history(req(), now=NOW)
        assert (out.index.hour == 0).all()
        assert (out.index.minute == 0).all()

    def test_a_malformed_row_is_skipped_not_fatal(self):
        lines = stooq_csv(4).strip().splitlines()
        lines[2] = "2026-07-21,n/a,102,100,101.5,1001"
        out = adapter("\n".join(lines)).fetch_history(req(), now=NOW)
        assert len(out) == 3

    def test_a_no_data_body_is_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            adapter("No data\n").fetch_history(req(symbol="NOTREAL"), now=NOW)

    def test_an_empty_body_is_a_transport_failure(self):
        with pytest.raises(ProviderUnavailable):
            adapter("   ").fetch_history(req(), now=NOW)


class TestAntiBotPage:
    def test_an_html_challenge_page_is_refused_not_parsed(self):
        """MEASURED behaviour: from some networks Stooq answers a JS challenge
        page instead of CSV. Salvaging prices from HTML would be far worse than
        an outage, so the shape is checked before anything is believed."""
        html = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
                '<body><noscript>Turn on JavaScript</noscript></body></html>')
        with pytest.raises(ProviderUnavailable, match="HTML"):
            adapter(html).fetch_history(req(), now=NOW)

    def test_a_changed_header_is_refused(self):
        """If Stooq reorders or renames its columns, mapping them positionally
        would put highs into the low column. Refuse instead."""
        body = "Date,Close,Open,High,Low,Volume\n2026-07-20,1,2,3,4,5\n"
        with pytest.raises(ProviderUnavailable, match="header changed"):
            adapter(body).fetch_history(req(), now=NOW)

    def test_the_blocked_case_marks_the_provider_unhealthy(self):
        a = adapter("<html>blocked</html>")
        with pytest.raises(ProviderUnavailable):
            a.fetch_history(req(), now=NOW)
        assert a.health().consecutive_failures == 1
        assert "HTML" in a.last_error


class TestErrorDialect:
    def test_429_becomes_a_rate_limit(self):
        with pytest.raises(ProviderRateLimited):
            adapter("", status=429).fetch_history(req(), now=NOW)

    def test_404_becomes_a_symbol_error(self):
        with pytest.raises(ProviderSymbolError):
            adapter("", status=404).fetch_history(req(), now=NOW)

    def test_5xx_becomes_unavailable(self):
        with pytest.raises(ProviderUnavailable):
            adapter("", status=503).fetch_history(req(), now=NOW)


def test_request_carries_the_date_window():
    record: list = []
    adapter(stooq_csv(), record=record).fetch_history(req(days=10), now=NOW)
    assert "d1=" in record[0] and "d2=" in record[0]
    assert "s=spy.us" in record[0]


def test_priority_places_stooq_last():
    from optionspilot.data.yfinance_adapter import YFinanceAdapter
    assert StooqAdapter().provider_priority > YFinanceAdapter().provider_priority
