import numpy as np

from optionspilot.analysis import volume as vol
from tests.conftest import df_from_ohlc, make_candles


class TestSpikes:
    def test_spike_flagged(self):
        v = np.full(30, 1000.0)
        v[-1] = 2500.0
        df = make_candles(np.linspace(100, 101, 30), volume=v)
        flags = vol.volume_spikes(df, period=20, threshold=2.0)
        assert flags.iloc[-1] and not flags.iloc[-2]


class TestPressure:
    def test_buyers_in_control(self):
        # Every bar closes at its high
        rows = [(100 + i, 100 + i + 1.0, 100 + i - 0.05, 100 + i + 1.0) for i in range(20)]
        p = vol.pressure(df_from_ohlc(rows), period=14)
        assert p.iloc[-1] > 0.8

    def test_sellers_in_control(self):
        rows = [(100 - i, 100 - i + 0.05, 100 - i - 1.0, 100 - i - 1.0) for i in range(20)]
        p = vol.pressure(df_from_ohlc(rows), period=14)
        assert p.iloc[-1] < -0.8

    def test_bounded(self):
        rng = np.random.default_rng(11)
        df = make_candles(100 + np.cumsum(rng.normal(0, 0.5, 60)))
        p = vol.pressure(df).dropna()
        assert ((p >= -1) & (p <= 1)).all()


class TestDivergence:
    def test_bearish_divergence(self):
        # First half: rally to 110 on heavy volume.
        # Second half: dump on heavy volume, then a marginal new high on air.
        closes = np.concatenate([
            np.linspace(100, 110, 15),          # strong rally
            np.linspace(109, 100, 7),           # heavy pullback
            np.linspace(101, 111, 8),           # new high...
        ])
        volumes = np.concatenate([
            np.full(15, 5000.0),
            np.full(7, 6000.0),
            np.full(8, 300.0),                  # ...on no volume
        ])
        df = make_candles(closes, volume=volumes)
        assert vol.detect_divergence(df, lookback=30) == -1

    def test_healthy_rally_no_divergence(self):
        closes = np.linspace(100, 115, 40)
        df = make_candles(closes, volume=np.full(40, 5000.0))
        assert vol.detect_divergence(df, lookback=30) == 0

    def test_short_history_is_neutral(self):
        df = make_candles(np.linspace(100, 105, 10))
        assert vol.detect_divergence(df, lookback=30) == 0
