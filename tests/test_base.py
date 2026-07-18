"""validate_candles: the canonical data boundary must remove malformed bars.

yfinance intermittently emits NaN volume on the in-progress bar, and
occasionally NaN/inf/zero OHLC rows. Before these guards, a NaN volume
reached `int(r.volume)` in the candles payload and 500'd the whole chart
endpoint ("some tickers randomly fail"), and a non-finite OHLC value would
have done the same during JSON serialization (Starlette allow_nan=False).
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from optionspilot.data.base import validate_candles


def frame(rows: int = 10, start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex(
        [start + timedelta(minutes=5 * i) for i in range(rows)], tz="UTC")
    base = 100.0 + np.arange(rows) * 0.1
    return pd.DataFrame({
        "open": base, "high": base + 0.5, "low": base - 0.5,
        "close": base + 0.2, "volume": np.full(rows, 1000.0),
    }, index=idx)


class TestValidateCandlesSanitization:
    def test_clean_frame_passes_through_unchanged(self):
        df = frame()
        out = validate_candles(df)
        assert len(out) == len(df)
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]

    def test_nan_volume_is_zeroed_not_dropped(self):
        df = frame()
        df.iloc[-1, df.columns.get_loc("volume")] = np.nan
        out = validate_candles(df)
        assert len(out) == len(df)          # the priced bar survives
        assert out["volume"].iloc[-1] == 0.0
        assert np.isfinite(out["volume"]).all()

    def test_inf_volume_is_zeroed(self):
        df = frame()
        df.iloc[3, df.columns.get_loc("volume")] = np.inf
        out = validate_candles(df)
        assert out["volume"].iloc[3] == 0.0

    def test_nan_ohlc_rows_are_dropped(self):
        df = frame()
        df.iloc[2, df.columns.get_loc("close")] = np.nan
        out = validate_candles(df)
        assert len(out) == len(df) - 1

    def test_inf_ohlc_rows_are_dropped(self):
        df = frame()
        df.iloc[4, df.columns.get_loc("high")] = np.inf
        out = validate_candles(df)
        assert len(out) == len(df) - 1
        assert np.isfinite(out[["open", "high", "low", "close"]]).all().all()

    def test_nonpositive_ohlc_rows_are_dropped(self):
        df = frame()
        df.iloc[1, df.columns.get_loc("low")] = 0.0
        df.iloc[5, df.columns.get_loc("open")] = -3.0
        out = validate_candles(df)
        assert len(out) == len(df) - 2

    def test_duplicate_timestamps_keep_last_and_sort(self):
        df = frame()
        dup = df.iloc[[3]].copy()
        dup["close"] = 999.0
        shuffled = pd.concat([df.iloc[::-1], dup])   # descending + a duplicate
        out = validate_candles(shuffled)
        assert len(out) == len(df)
        assert out.index.is_monotonic_increasing
        assert out["close"].iloc[3] == 999.0         # keep="last" won

    def test_empty_frame_yields_canonical_empty(self):
        out = validate_candles(pd.DataFrame())
        assert out.empty
        assert out.index.tz is not None

    def test_all_bad_rows_yield_empty_not_error(self):
        df = frame(3)
        df[["open", "high", "low", "close"]] = np.nan
        out = validate_candles(df)
        assert out.empty
