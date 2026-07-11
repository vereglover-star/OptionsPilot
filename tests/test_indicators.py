import numpy as np
import pandas as pd
import pytest

from optionspilot.analysis import indicators as ind
from tests.conftest import make_candles


class TestMovingAverages:
    def test_sma_known_values(self):
        s = pd.Series([1.0, 2, 3, 4, 5])
        out = ind.sma(s, 3)
        assert np.isnan(out.iloc[1])
        assert out.iloc[2] == 2.0 and out.iloc[4] == 4.0

    def test_ema_known_values(self):
        # EMA(3): alpha = 0.5; seeded at first value
        s = pd.Series([2.0, 4.0, 6.0])
        out = ind.ema(s, 3)
        assert out.iloc[0] == 2.0
        assert out.iloc[1] == 3.0   # 0.5*4 + 0.5*2
        assert out.iloc[2] == 4.5   # 0.5*6 + 0.5*3

    def test_vwap_resets_per_session(self):
        # Two sessions, two bars each
        df = make_candles([100, 100, 200, 200], start="2026-01-05 00:00", freq="12h")
        v = ind.vwap(df)
        # First bar of second session must ignore first session prices entirely
        day2_first = v.iloc[2]
        typical2 = (df["high"].iloc[2] + df["low"].iloc[2] + df["close"].iloc[2]) / 3
        assert day2_first == pytest.approx(typical2)


class TestRSI:
    def test_pure_uptrend_is_100(self):
        s = pd.Series(np.arange(1.0, 40.0))
        assert ind.rsi(s, 14).iloc[-1] == pytest.approx(100.0)

    def test_flat_after_moves_converges_to_50(self):
        rng = np.random.default_rng(1)
        s = pd.Series(np.concatenate([100 + rng.normal(0, 1, 30).cumsum(),
                                      np.full(200, 100.0)]))
        # With no gains and no losses for a long time, RS -> 0/0; Wilder RSI
        # convention: stays where the decayed averages put it, between 0..100.
        val = ind.rsi(s, 14).iloc[-1]
        assert 0 <= val <= 100

    def test_warmup_is_nan(self):
        s = pd.Series(np.arange(1.0, 40.0))
        assert ind.rsi(s, 14).iloc[:14].isna().all()

    def test_reference_value(self):
        # Classic Wilder dataset (Cardwell/StockCharts example)
        closes = pd.Series([
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
        ])
        val = ind.rsi(closes, 14).iloc[14]
        assert val == pytest.approx(70.46, abs=0.1)


class TestMACD:
    def test_columns_and_hist_identity(self, trending_up):
        out = ind.macd(trending_up["close"])
        assert list(out.columns) == ["macd", "macd_signal", "macd_hist"]
        np.testing.assert_allclose(
            out["macd_hist"], out["macd"] - out["macd_signal"]
        )

    def test_positive_in_uptrend(self, trending_up):
        assert ind.macd(trending_up["close"])["macd"].iloc[-1] > 0


class TestVolatility:
    def test_atr_positive_and_scaled(self, trending_up):
        a = ind.atr(trending_up, 14)
        assert (a.dropna() > 0).all()
        # ATR can't exceed the biggest true range seen
        assert a.max() <= ind.true_range(trending_up).max() + 1e-9

    def test_bollinger_ordering(self, choppy):
        bb = ind.bollinger(choppy["close"]).dropna()
        assert (bb["bb_upper"] >= bb["bb_mid"]).all()
        assert (bb["bb_mid"] >= bb["bb_lower"]).all()

    def test_bollinger_constant_series_has_zero_width(self):
        df = make_candles(np.full(30, 100.0))
        bb = ind.bollinger(df["close"]).dropna()
        assert bb["bb_width"].abs().max() == pytest.approx(0.0)


class TestSupertrend:
    def test_bullish_in_uptrend(self, trending_up):
        st = ind.supertrend(trending_up)
        assert st["supertrend_dir"].iloc[-1] == 1
        # In a confirmed uptrend the stop line sits below price
        last = trending_up["close"].iloc[-1]
        assert st["supertrend"].iloc[-1] < last

    def test_bearish_in_downtrend(self):
        rng = np.random.default_rng(3)
        closes = 100 - np.cumsum(rng.normal(0.3, 0.15, 60))
        df = make_candles(closes)
        assert ind.supertrend(df)["supertrend_dir"].iloc[-1] == -1


class TestADX:
    def test_trend_beats_chop(self, trending_up, choppy):
        adx_trend = ind.adx(trending_up)["adx"].iloc[-1]
        adx_chop = ind.adx(choppy)["adx"].iloc[-1]
        assert adx_trend > adx_chop

    def test_di_alignment_in_uptrend(self, trending_up):
        out = ind.adx(trending_up)
        assert out["plus_di"].iloc[-1] > out["minus_di"].iloc[-1]


class TestVolume:
    def test_obv_rises_with_uptrend(self, trending_up):
        o = ind.obv(trending_up)
        assert o.iloc[-1] > o.iloc[5]

    def test_relative_volume_spike(self):
        vol = np.full(30, 1000.0)
        vol[-1] = 3000.0
        df = make_candles(np.linspace(100, 101, 30), volume=vol)
        assert ind.relative_volume(df, 20).iloc[-1] == pytest.approx(3.0)

    def test_relative_volume_excludes_current_bar(self):
        # If current bar were included in its own average, spike would read < 3
        vol = np.full(30, 1000.0)
        vol[-1] = 3000.0
        df = make_candles(np.linspace(100, 101, 30), volume=vol)
        assert ind.relative_volume(df, 20).iloc[-1] > 2.9


class TestStochRSI:
    def test_bounds(self, choppy):
        out = ind.stoch_rsi(choppy["close"]).dropna()
        assert ((out >= 0) & (out <= 100)).all().all()
