from optionspilot.analysis import structure as st
from optionspilot.core.models import Direction
from tests.conftest import make_candles, zigzag

import numpy as np


class TestSwings:
    def test_finds_and_labels_uptrend_swings(self):
        # 100 -> 105 -> 102 -> 108 -> 104 -> 112
        df = zigzag([100, 105, 102, 108, 104, 112])
        swings = st.find_swings(df, strength=2)
        highs = [s for s in swings if s.is_high]
        lows = [s for s in swings if not s.is_high]
        assert [round(s.price) for s in highs][:2] == [105, 108]
        assert [round(s.price) for s in lows][:2] == [102, 104]
        assert highs[1].label == "HH"
        assert lows[1].label == "HL"

    def test_labels_downtrend_swings(self):
        df = zigzag([112, 104, 108, 102, 105, 98])
        swings = st.find_swings(df, strength=2)
        assert [s.label for s in swings if s.is_high and s.label] == ["LH"]
        assert [s.label for s in swings if not s.is_high and s.label] == ["LL"]

    def test_confirmation_lag(self):
        df = zigzag([100, 105, 102, 108])
        swings = st.find_swings(df, strength=2)
        for s in swings:
            pivot_pos = df.index.get_loc(s.ts)
            confirmed_pos = df.index.get_loc(s.confirmed_ts)
            assert confirmed_pos - pivot_pos == 2


class TestTrend:
    def test_uptrend(self):
        swings = st.find_swings(zigzag([100, 105, 102, 108, 104, 112]), 2)
        assert st.trend_state(swings) is st.Trend.UP

    def test_downtrend(self):
        swings = st.find_swings(zigzag([112, 104, 108, 102, 105, 98]), 2)
        assert st.trend_state(swings) is st.Trend.DOWN

    def test_insufficient_data_is_range(self):
        swings = st.find_swings(zigzag([100, 105]), 2)
        assert st.trend_state(swings) is st.Trend.RANGE


class TestEvents:
    def test_bos_in_uptrend(self):
        df = zigzag([100, 105, 102, 108, 104, 112])
        events = st.detect_events(df, st.find_swings(df, 2))
        ups = [e for e in events if e.direction is Direction.LONG]
        assert ups, "expected at least one upside break"
        assert ups[0].kind == "BOS"
        assert round(ups[0].level) == 105  # first confirmed swing high broken

    def test_choch_on_reversal(self):
        # Clean uptrend, then a hard break below the last higher-low (104)
        df = zigzag([100, 105, 102, 108, 104, 112, 101])
        events = st.detect_events(df, st.find_swings(df, 2))
        downs = [e for e in events if e.direction is Direction.SHORT]
        assert downs, "expected a downside break"
        assert downs[-1].kind == "CHOCH"

    def test_no_events_without_breaks(self):
        # Price never closes beyond a confirmed swing level
        df = zigzag([100, 105, 101, 104.5, 100.5])
        events = st.detect_events(df, st.find_swings(df, 2))
        assert all(e.kind != "BOS" or e.level < 105 for e in events)


class TestConsolidation:
    def test_coil_detected_after_trend(self):
        rng = np.random.default_rng(5)
        trend = 100 + np.cumsum(rng.normal(0.5, 0.3, 40))
        coil = trend[-1] + rng.normal(0, 0.05, 40)  # tight flat range
        df = make_candles(np.concatenate([trend, coil]))
        flags = st.is_consolidating(df, lookback=15, atr_mult=2.5)
        assert flags.iloc[-1]
        assert not flags.iloc[39]  # mid-trend is not consolidation
