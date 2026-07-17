"""CachedProvider: TTL freshness, request dedup, window coverage, memos."""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from optionspilot.core.models import OptionContract, OptionRight, Quote, Timeframe
from optionspilot.data.base import MarketDataProvider
from optionspilot.data.cached import CachedProvider
import optionspilot.data.cached as cached_mod


def _frame(n=60, freq="5min", end=None):
    end = end or pd.Timestamp.now(tz="UTC").floor("5min")
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    base = pd.Series(range(n), index=idx, dtype="float64")
    return pd.DataFrame({
        "open": 100 + base, "high": 101 + base, "low": 99 + base,
        "close": 100.5 + base, "volume": 1000.0,
    }, index=idx.rename("ts"))


class CountingProvider(MarketDataProvider):
    name = "counting"

    def __init__(self):
        self.calls = {"candles": 0, "quote": 0, "expirations": 0, "chain": 0}
        self.frame = _frame()

    def get_candles(self, symbol, timeframe, start, end):
        self.calls["candles"] += 1
        return self.frame

    def get_quote(self, symbol):
        self.calls["quote"] += 1
        return Quote(symbol.upper(), datetime.now(timezone.utc),
                     bid=99.0, ask=101.0, last=100.0)

    def get_expirations(self, symbol):
        self.calls["expirations"] += 1
        return [date(2026, 8, 21)]

    def get_option_chain(self, symbol, expiration):
        self.calls["chain"] += 1
        return [OptionContract(symbol.upper(), expiration, 100.0,
                               OptionRight.CALL, bid=1.0, ask=1.1)]

    def get_market_cap(self, symbol):
        return 1e12


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock inside the cached module."""
    state = {"t": 1000.0}
    monkeypatch.setattr(cached_mod._time, "monotonic", lambda: state["t"])
    return state


@pytest.fixture
def rig(clock):
    inner = CountingProvider()
    return CachedProvider(inner), inner, clock


WINDOW = timedelta(days=10)


def _get(provider, tf=Timeframe.M5, days_back=10):
    end = datetime.now(timezone.utc)
    return provider.get_candles("spy", tf, end - timedelta(days=days_back), end)


class TestCandleTTL:
    def test_second_fetch_within_ttl_is_served_from_memory(self, rig):
        provider, inner, clock = rig
        a = _get(provider)
        b = _get(provider)
        assert inner.calls["candles"] == 1
        pd.testing.assert_frame_equal(a, b)

    def test_refetches_after_ttl_expires(self, rig):
        provider, inner, clock = rig
        _get(provider)
        clock["t"] += cached_mod.CANDLE_TTL[Timeframe.M5] + 1
        _get(provider)
        assert inner.calls["candles"] == 2

    def test_ttl_is_per_timeframe(self, rig):
        provider, inner, clock = rig
        _get(provider, Timeframe.M5)
        _get(provider, Timeframe.H4)
        assert inner.calls["candles"] == 2
        clock["t"] += cached_mod.CANDLE_TTL[Timeframe.M5] + 1
        _get(provider, Timeframe.M5)   # expired -> refetch
        _get(provider, Timeframe.H4)   # still fresh -> cached
        assert inner.calls["candles"] == 3

    def test_wider_window_than_cached_forces_refetch(self, rig):
        provider, inner, clock = rig
        _get(provider, days_back=5)
        _get(provider, days_back=30)   # cached frame doesn't cover this start
        assert inner.calls["candles"] == 2

    def test_narrower_window_is_sliced_from_cache(self, rig):
        provider, inner, clock = rig
        _get(provider, days_back=10)
        end = datetime.now(timezone.utc)
        sliced = provider.get_candles("SPY", Timeframe.M5,
                                      end - timedelta(hours=1), end)
        assert inner.calls["candles"] == 1
        assert sliced.index[0] >= pd.Timestamp(end - timedelta(hours=1))

    def test_symbol_is_case_insensitive(self, rig):
        provider, inner, clock = rig
        _get(provider)
        end = datetime.now(timezone.utc)
        provider.get_candles("SPY", Timeframe.M5, end - WINDOW, end)
        assert inner.calls["candles"] == 1


class TestMemos:
    def test_quote_memo_expires(self, rig):
        provider, inner, clock = rig
        provider.get_quote("SPY")
        provider.get_quote("spy")
        assert inner.calls["quote"] == 1
        clock["t"] += cached_mod.QUOTE_TTL + 1
        provider.get_quote("SPY")
        assert inner.calls["quote"] == 2

    def test_chain_memo_keyed_by_expiration(self, rig):
        provider, inner, clock = rig
        provider.get_option_chain("SPY", date(2026, 8, 21))
        provider.get_option_chain("SPY", date(2026, 8, 21))
        provider.get_option_chain("SPY", date(2026, 9, 18))
        assert inner.calls["chain"] == 2

    def test_expirations_memo(self, rig):
        provider, inner, clock = rig
        provider.get_expirations("SPY")
        provider.get_expirations("SPY")
        assert inner.calls["expirations"] == 1

    def test_invalidate_quotes_drops_quote_and_chain_only(self, rig):
        provider, inner, clock = rig
        provider.get_quote("SPY")
        provider.get_option_chain("SPY", date(2026, 8, 21))
        provider.get_expirations("SPY")
        provider.invalidate_quotes()
        provider.get_quote("SPY")
        provider.get_option_chain("SPY", date(2026, 8, 21))
        provider.get_expirations("SPY")
        assert inner.calls["quote"] == 2
        assert inner.calls["chain"] == 2
        assert inner.calls["expirations"] == 1

    def test_extras_pass_through(self, rig):
        provider, inner, clock = rig
        assert provider.get_market_cap("SPY") == 1e12


class TestWriteThrough:
    def test_candles_persist_and_warm_start_within_freshness(self, tmp_path, clock):
        inner = CountingProvider()
        db = tmp_path / "cache.db"
        first = CachedProvider(inner, db)
        _get(first)
        assert inner.calls["candles"] == 1
        # a "restarted app": new provider instance, same db, fresh last bar
        second = CachedProvider(CountingProvider(), db)
        df = _get(second)
        assert not df.empty            # served from disk, no network call
        assert second._inner.calls["candles"] == 0

    def test_stale_disk_cache_is_ignored(self, tmp_path, clock):
        inner = CountingProvider()
        inner.frame = _frame(end=pd.Timestamp.now(tz="UTC") - pd.Timedelta("2D"))
        db = tmp_path / "cache.db"
        CachedProvider(inner, db).get_candles(
            "SPY", Timeframe.M5,
            datetime.now(timezone.utc) - timedelta(days=10),
            datetime.now(timezone.utc))
        fresh_inner = CountingProvider()
        second = CachedProvider(fresh_inner, db)
        _get(second)
        assert fresh_inner.calls["candles"] == 1   # disk was stale -> refetched


class FailingProvider(CountingProvider):
    """First `fail_for` candle fetches return empty (yfinance failure style),
    then recover."""

    def __init__(self, fail_for=1):
        super().__init__()
        self.fail_for = fail_for

    def get_candles(self, symbol, timeframe, start, end):
        self.calls["candles"] += 1
        if self.calls["candles"] <= self.fail_for:
            return pd.DataFrame()
        return self.frame


class TestEmptyFetchNotPoisoned:
    """Root cause of blank charts (2026-07-17): a transient empty fetch was
    memoized for the full TTL, so healthy retries kept getting the failure."""

    def test_empty_result_expires_quickly_not_full_ttl(self, clock):
        inner = FailingProvider(fail_for=1)
        provider = CachedProvider(inner)
        assert _get(provider).empty                    # upstream hiccup
        clock["t"] += cached_mod.EMPTY_CANDLE_TTL + 1  # well inside M5's TTL
        assert not _get(provider).empty                # recovered on retry
        assert inner.calls["candles"] == 2

    def test_empty_result_is_briefly_cached_to_avoid_hammering(self, clock):
        inner = FailingProvider(fail_for=99)
        provider = CachedProvider(inner)
        _get(provider)
        _get(provider)                                 # inside the short TTL
        assert inner.calls["candles"] == 1

    def test_good_data_still_cached_for_full_ttl(self, rig):
        provider, inner, clock = rig
        _get(provider)
        clock["t"] += cached_mod.EMPTY_CANDLE_TTL + 1
        _get(provider)                                 # still fresh, no refetch
        assert inner.calls["candles"] == 1


class TestStaleOkFallback:
    """Charts-tab fallback: clearly-flagged stale disk data beats a blank
    canvas. The strict get_candles path (the engine's) is unchanged."""

    def _end(self):
        return datetime.now(timezone.utc)

    def test_live_data_reported_not_stale(self, tmp_path, clock):
        provider = CachedProvider(CountingProvider(), tmp_path / "c.db")
        df, stale = provider.get_candles_stale_ok(
            "SPY", Timeframe.M5, self._end() - WINDOW, self._end())
        assert not df.empty and stale is False

    def test_dead_network_with_disk_history_serves_stale(self, tmp_path, clock):
        db = tmp_path / "c.db"
        old = CountingProvider()
        old.frame = _frame(end=pd.Timestamp.now(tz="UTC") - pd.Timedelta("2D"))
        CachedProvider(old, db).get_candles(          # seed the disk cache
            "SPY", Timeframe.M5, self._end() - WINDOW, self._end())
        dead = FailingProvider(fail_for=99)
        provider = CachedProvider(dead, db)
        df, stale = provider.get_candles_stale_ok(
            "SPY", Timeframe.M5, self._end() - WINDOW, self._end())
        assert not df.empty and stale is True
        # and the STRICT path still fails closed for the same state
        clock["t"] += cached_mod.EMPTY_CANDLE_TTL + 1
        assert _get(provider).empty

    def test_dead_network_no_disk_returns_empty_not_stale(self, clock):
        provider = CachedProvider(FailingProvider(fail_for=99))
        df, stale = provider.get_candles_stale_ok(
            "SPY", Timeframe.M5, self._end() - WINDOW, self._end())
        assert df.empty and stale is False
