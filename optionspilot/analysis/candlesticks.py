"""Candlestick pattern detection — vectorized boolean detectors.

Each detector returns a boolean Series aligned to the candle index; True on the
bar that *completes* the pattern (so multi-bar patterns fire on their last bar
and can be acted on without lookahead). `detect_all` bundles every pattern into
one DataFrame for the engine, computing the shared bar geometry (body, range,
wicks) once instead of once per detector.

Patterns here are purely geometric. Context (trend location, volume, structure)
is the ConfluenceScorer's job — a hammer in a downtrend and a hammer mid-range
are the same *shape*, but not the same *signal*, and that distinction lives in
the engine, not here.
"""

from __future__ import annotations

import pandas as pd


def _parts(df: pd.DataFrame):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l)
    upper = h - o.where(o > c, c)   # high - max(open, close)
    lower = o.where(o < c, c) - l   # min(open, close) - low
    return o, h, l, c, body, rng, upper, lower


def doji(df: pd.DataFrame, max_body_frac: float = 0.1) -> pd.Series:
    return _doji(_parts(df), max_body_frac)


def _doji(parts, max_body_frac: float = 0.1) -> pd.Series:
    o, h, l, c, body, rng, *_ = parts
    return ((rng > 0) & (body <= max_body_frac * rng)).rename("doji")


def hammer(df: pd.DataFrame) -> pd.Series:
    """Long lower wick, small body near the top, negligible upper wick."""
    return _hammer(_parts(df))


def _hammer(parts) -> pd.Series:
    o, h, l, c, body, rng, upper, lower = parts
    return (
        (rng > 0)
        & (body <= 0.35 * rng)
        & (lower >= 2 * body)
        & (lower >= 0.6 * rng)
        & (upper <= 0.15 * rng)
    ).rename("hammer")


def shooting_star(df: pd.DataFrame) -> pd.Series:
    return _shooting_star(_parts(df))


def _shooting_star(parts) -> pd.Series:
    o, h, l, c, body, rng, upper, lower = parts
    return (
        (rng > 0)
        & (body <= 0.35 * rng)
        & (upper >= 2 * body)
        & (upper >= 0.6 * rng)
        & (lower <= 0.15 * rng)
    ).rename("shooting_star")


def marubozu(df: pd.DataFrame, min_body_frac: float = 0.9) -> pd.DataFrame:
    return _marubozu(_parts(df), min_body_frac)


def _marubozu(parts, min_body_frac: float = 0.9) -> pd.DataFrame:
    o, h, l, c, body, rng, *_ = parts
    full = (rng > 0) & (body >= min_body_frac * rng)
    return pd.DataFrame({
        "bullish_marubozu": full & (c > o),
        "bearish_marubozu": full & (c < o),
    })


def engulfing(df: pd.DataFrame) -> pd.DataFrame:
    return _engulfing(_parts(df))


def _engulfing(parts) -> pd.DataFrame:
    o, h, l, c, body, rng, *_ = parts
    po, pc, pbody = o.shift(), c.shift(), body.shift()
    bull = (
        (pc < po)                    # previous bearish
        & (c > o)                    # current bullish
        & (o <= pc) & (c >= po)      # body covers previous body
        & (body > pbody)             # and is strictly bigger
    )
    bear = (
        (pc > po)
        & (c < o)
        & (o >= pc) & (c <= po)
        & (body > pbody)
    )
    return pd.DataFrame({"bullish_engulfing": bull, "bearish_engulfing": bear})


def _star(parts, bullish: bool) -> pd.Series:
    """Morning star (bullish=True) / evening star: strong bar, small pause bar,
    strong reversal bar closing beyond the midpoint of bar 1's body."""
    o, h, l, c, body, rng, *_ = parts
    o1, c1, body1, rng1 = o.shift(2), c.shift(2), body.shift(2), rng.shift(2)
    body2 = body.shift(1)
    mid1 = (o1 + c1) / 2
    bar1_strong = body1 >= 0.5 * rng1
    bar2_small = body2 <= 0.3 * body1
    if bullish:
        return (c1 < o1) & bar1_strong & bar2_small & (c > o) & (c >= mid1)
    return (c1 > o1) & bar1_strong & bar2_small & (c < o) & (c <= mid1)


def morning_star(df: pd.DataFrame) -> pd.Series:
    return _star(_parts(df), bullish=True).rename("morning_star")


def evening_star(df: pd.DataFrame) -> pd.Series:
    return _star(_parts(df), bullish=False).rename("evening_star")


def inside_bar(df: pd.DataFrame) -> pd.Series:
    return ((df["high"] < df["high"].shift()) & (df["low"] > df["low"].shift())
            ).rename("inside_bar")


def outside_bar(df: pd.DataFrame) -> pd.Series:
    return ((df["high"] > df["high"].shift()) & (df["low"] < df["low"].shift())
            ).rename("outside_bar")


def _three_soldiers_or_crows(parts, bullish: bool) -> pd.Series:
    o, h, l, c, body, rng, *_ = parts
    solid = (rng > 0) & (body >= 0.5 * rng)
    if bullish:
        direction = c > o
        progressing = c > c.shift()
        opens_in_prev_body = (o > o.shift()) & (o < c.shift())
    else:
        direction = c < o
        progressing = c < c.shift()
        opens_in_prev_body = (o < o.shift()) & (o > c.shift())
    bar_ok = solid & direction
    seq = bar_ok & bar_ok.shift(1, fill_value=False) & bar_ok.shift(2, fill_value=False)
    steps = progressing & progressing.shift(1, fill_value=False)
    opens = opens_in_prev_body & opens_in_prev_body.shift(1, fill_value=False)
    return seq & steps & opens


def three_white_soldiers(df: pd.DataFrame) -> pd.Series:
    return _three_soldiers_or_crows(_parts(df), bullish=True
                                    ).rename("three_white_soldiers")


def three_black_crows(df: pd.DataFrame) -> pd.Series:
    return _three_soldiers_or_crows(_parts(df), bullish=False
                                    ).rename("three_black_crows")


def detect_all(df: pd.DataFrame) -> pd.DataFrame:
    """All patterns as one boolean DataFrame, NaN-safe, aligned to df.index.
    Shares one `_parts` computation across every detector."""
    parts = _parts(df)
    out = pd.concat(
        [
            _doji(parts), _hammer(parts), _shooting_star(parts),
            _marubozu(parts), _engulfing(parts),
            _star(parts, bullish=True).rename("morning_star"),
            _star(parts, bullish=False).rename("evening_star"),
            inside_bar(df), outside_bar(df),
            _three_soldiers_or_crows(parts, bullish=True
                                     ).rename("three_white_soldiers"),
            _three_soldiers_or_crows(parts, bullish=False
                                     ).rename("three_black_crows"),
        ],
        axis=1,
    )
    return out.fillna(False).astype(bool)
