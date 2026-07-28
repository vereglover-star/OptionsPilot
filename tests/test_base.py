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
from optionspilot.data.base import SESSION_TZ, session_index


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

    def test_unparseable_timestamps_are_dropped_not_carried(self):
        """A NaT index entry survives every later step and only detonates at
        the end of /api/candles, where `int(ts.timestamp())` raises and 500s
        the whole response — one malformed bar taking the entire chart down.
        Two real sources: `pd.to_datetime(errors="coerce")` in the HTTP
        adapters, and http_adapter.localize mapping a DST fall-back ambiguity
        to NaT by design."""
        df = frame(4)
        idx = df.index.to_list()
        idx[2] = pd.NaT
        df.index = pd.DatetimeIndex(idx, tz="UTC", name="ts")
        out = validate_candles(df)
        assert len(out) == 3
        assert not out.index.isna().any()
        # the payload builder's actual failure mode is now impossible
        assert all(isinstance(int(ts.timestamp()), int) for ts in out.index)

    def test_an_all_nat_frame_yields_empty_not_error(self):
        df = frame(3)
        df.index = pd.DatetimeIndex([pd.NaT] * 3, tz="UTC", name="ts")
        out = validate_candles(df)
        assert out.empty


class TestSessionIndex:
    """The ONE daily-bar convention. See `base.session_index`.

    Before this existed each adapter stamped a daily bar wherever its upstream
    put it — Yahoo at the 09:30 ET session open (13:30 UTC), yfinance at 00:00
    ET (04:00 UTC), Stooq and the keyed HTTP providers at 00:00 UTC. The cache
    is keyed `(symbol, timeframe, ts)`, so those are three different rows for
    one trading day, and a frame holding them has a tightest spacing of ~0.40
    days instead of 1.0. `validate_history` then correctly rejected it as
    "wrong interval served" and EVERY symbol on 1D showed a validation screen
    it could not recover from. Measured on a real cache: 6,517 SPY daily rows
    for ~3,258 trading days.
    """

    def test_the_yahoo_and_yfinance_conventions_converge(self):
        """The exact collision that broke 1D, as the two adapters produce it."""
        yahoo = pd.DatetimeIndex(["2026-07-24T13:30:00Z"])      # 09:30 ET open
        yfinance = pd.DatetimeIndex(["2026-07-24T04:00:00Z"])   # 00:00 ET
        assert session_index(yahoo)[0] == session_index(yfinance)[0]

    def test_it_lands_on_exchange_midnight_not_utc_midnight(self):
        """UTC midnight is 19:00/20:00 ET on the PREVIOUS day, and the chart
        labels every timestamp through an ET formatter — so a UTC-midnight
        daily bar renders one day early."""
        out = session_index(pd.DatetimeIndex(["2026-07-24T13:30:00Z"]))
        local = out.tz_convert(SESSION_TZ)
        assert local[0].hour == 0 and local[0].minute == 0
        assert str(local[0].date()) == "2026-07-24"

    def test_it_is_idempotent(self):
        once = session_index(pd.DatetimeIndex(["2026-07-24T13:30:00Z"]))
        assert list(session_index(once)) == list(once)

    def test_it_holds_across_a_dst_boundary(self):
        """EST and EDT put exchange midnight at 05:00 and 04:00 UTC. Both must
        still be the correct calendar date — an offset assumption here shifts
        half the year's bars onto the wrong day."""
        winter = session_index(pd.DatetimeIndex(["2026-01-15T14:30:00Z"]))
        summer = session_index(pd.DatetimeIndex(["2026-07-15T13:30:00Z"]))
        assert winter[0].hour == 5 and summer[0].hour == 4
        assert str(winter.tz_convert(SESSION_TZ)[0].date()) == "2026-01-15"
        assert str(summer.tz_convert(SESSION_TZ)[0].date()) == "2026-07-15"

    def test_every_within_session_instant_collapses_to_one_bar(self):
        """The end-to-end property: whatever instant during the trading day a
        provider chose, one session must produce one cache key."""
        mixed = pd.DatetimeIndex([
            "2026-07-24T04:00:00Z",     # yfinance — 00:00 ET
            "2026-07-24T13:30:00Z",     # yahoo — 09:30 ET session open
            "2026-07-24T20:00:00Z",     # a close-stamped provider — 16:00 ET
        ])
        assert len(set(session_index(mixed))) == 1

    def test_a_utc_midnight_stamp_is_the_previous_session_and_stays_that_way(self):
        """Why the date-only sources had to be fixed AT SOURCE.

        00:00 UTC is 19:00 ET the day before, so `session_index` maps it to the
        previous session — correctly, because that is genuinely the instant it
        names. Nothing downstream can recover a calendar date that was thrown
        away upstream, which is why `stooq_provider` and `http_adapter.localize`
        now localize the provider's DATE into the exchange zone rather than
        stamping it at UTC midnight and hoping.
        """
        out = session_index(pd.DatetimeIndex(["2026-07-24T00:00:00Z"]))
        assert str(out.tz_convert(SESSION_TZ)[0].date()) == "2026-07-23"

    def test_an_empty_index_is_returned_unchanged(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        assert len(session_index(empty)) == 0
