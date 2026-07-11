import numpy as np
import pandas as pd
import pytest


def make_candles(closes, start="2026-01-05 14:30", freq="5min", volume=None) -> pd.DataFrame:
    """Build a canonical candle frame from a list of closes."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    vol = np.asarray(volume, dtype=float) if volume is not None else np.full(n, 1_000.0)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="ts")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol},
        index=idx,
    )


def df_from_ohlc(rows, start="2026-01-05 14:30", freq="5min") -> pd.DataFrame:
    """Build a canonical frame from explicit (open, high, low, close[, volume])
    tuples — for tests where exact candle geometry matters."""
    norm = [r if len(r) == 5 else (*r, 1_000.0) for r in rows]
    idx = pd.date_range(start, periods=len(norm), freq=freq, tz="UTC", name="ts")
    return pd.DataFrame(
        norm, columns=["open", "high", "low", "close", "volume"], index=idx,
    ).astype(float)


def zigzag(points, bars_per_leg=5, start="2026-01-05 14:30", freq="5min") -> pd.DataFrame:
    """Piecewise-linear close path through `points`, as canonical candles.

    Wicks lean in the direction of travel (5% of body ahead, 2% behind) so a
    turning point's extreme is strictly unique — otherwise the bar after a peak
    (opening at the peak close) would tie the peak's high and no fractal swing
    would ever be detectable.
    """
    closes = [float(points[0])]
    for a, b in zip(points, points[1:]):
        closes.extend(np.linspace(a, b, bars_per_leg + 1)[1:])
    closes = np.asarray(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    body = np.abs(closes - opens)
    pad = np.where(body > 0, body, 0.01)
    bull = closes >= opens
    highs = np.where(bull, closes + 0.05 * pad, opens + 0.02 * pad)
    lows = np.where(bull, opens - 0.02 * pad, closes - 0.05 * pad)
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC", name="ts")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(len(closes), 1_000.0)},
        index=idx,
    )


@pytest.fixture
def trending_up() -> pd.DataFrame:
    """60 bars of steady uptrend with mild noise (seeded)."""
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0.3, 0.15, 60))
    return make_candles(closes)


@pytest.fixture
def choppy() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, 60))
    return make_candles(closes)
