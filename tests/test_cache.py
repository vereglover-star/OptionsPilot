from datetime import datetime, timezone

from optionspilot.core.models import Timeframe
from optionspilot.data.cache import CandleCache
from tests.conftest import make_candles


def dt(h, m=0):
    return datetime(2026, 1, 5, h, m, tzinfo=timezone.utc)


def test_store_load_roundtrip(tmp_path):
    df = make_candles([100, 101, 102, 101.5], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        assert cache.store("spy", Timeframe.M5, df) == 4
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert len(out) == 4
        assert out["close"].tolist() == [100, 101, 102, 101.5]
        assert out.index.tz is not None


def test_upsert_deduplicates(tmp_path):
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.store("SPY", Timeframe.M5, df)  # same bars again
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert len(out) == 2


def test_timeframes_are_isolated(tmp_path):
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        assert cache.load("SPY", Timeframe.M15, dt(0), dt(23)).empty


def test_range_query_is_half_open(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30", freq="5min")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        out = cache.load("SPY", Timeframe.M5, dt(14, 30), dt(14, 40))
        assert len(out) == 2  # 14:30 and 14:35; 14:40 excluded


def test_coverage(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        assert cache.coverage("SPY", Timeframe.M5) is None
        cache.store("SPY", Timeframe.M5, df)
        lo, hi = cache.coverage("SPY", Timeframe.M5)
        assert lo == dt(14, 30) and hi == dt(14, 40)
