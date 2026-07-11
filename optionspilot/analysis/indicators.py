"""Technical indicators as pure functions over canonical candle DataFrames.

Every function takes the canonical candle frame (see data.base) and returns a
Series or DataFrame aligned to the input index, NaN where not yet defined.
No I/O, no state — the live engine and the backtester call the exact same code.

Formulas follow the standard definitions (Wilder smoothing where the original
indicator specifies it), verified against reference values in the test suite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Moving averages ──────────────────────────────────────────────────────────

def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean().rename(f"sma_{period}")


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean().rename(f"ema_{period}")


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP: resets at the start of each UTC trading day.
    For daily bars this degenerates to typical price, which is expected —
    VWAP is an intraday tool."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    day = df.index.normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["volume"].groupby(day).cumsum()
    return (cum_pv / cum_vol.replace(0, np.nan)).rename("vwap")


# ── Momentum ─────────────────────────────────────────────────────────────────

def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA), seeded with the SMA of the first `period`
    valid values — the classic definition, matching TA-Lib and every major
    charting platform. A plain ewm() without the SMA seed diverges badly on
    short histories."""
    values = series.to_numpy(dtype=float)
    out = np.full(values.shape, np.nan)
    n = len(values)
    i = 0
    while i < n and np.isnan(values[i]):
        i += 1
    start = i + period - 1
    if start >= n:
        return pd.Series(out, index=series.index)
    out[start] = values[i:start + 1].mean()
    for j in range(start + 1, n):
        out[j] = out[j - 1] + (values[j] - out[j - 1]) / period
    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), period)
    loss = _wilder((-delta).clip(lower=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(loss == 0) & gain.notna()] = 100.0  # pure uptrend edge case
    return out.rename(f"rsi_{period}")


def stoch_rsi(
    close: pd.Series, period: int = 14, k: int = 3, d: int = 3
) -> pd.DataFrame:
    r = rsi(close, period)
    lo = r.rolling(period).min()
    hi = r.rolling(period).max()
    stoch = (r - lo) / (hi - lo).replace(0, np.nan) * 100
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return pd.DataFrame({"stochrsi_k": k_line, "stochrsi_d": d_line})


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        "macd": line, "macd_signal": sig, "macd_hist": line - sig,
    })


# ── Volatility ───────────────────────────────────────────────────────────────

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rename("tr")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period).rename(f"atr_{period}")


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    std = close.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    return pd.DataFrame({
        "bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width,
    })


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Returns 'supertrend' (the stop line) and 'supertrend_dir'
    (+1 bullish / -1 bearish). Iterative by definition."""
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, period)
    upper_basic = hl2 + multiplier * a
    lower_basic = hl2 - multiplier * a

    n = len(df)
    close = df["close"].to_numpy()
    ub, lb = upper_basic.to_numpy(), lower_basic.to_numpy()
    final_ub, final_lb = ub.copy(), lb.copy()
    st = np.full(n, np.nan)
    direction = np.zeros(n)

    valid = ~np.isnan(ub)
    if not valid.any():
        return pd.DataFrame(
            {"supertrend": st, "supertrend_dir": direction}, index=df.index
        )
    start = int(np.argmax(valid))  # first bar with a defined ATR
    direction[start] = 1 if close[start] > ub[start] else -1
    st[start] = final_lb[start] if direction[start] == 1 else final_ub[start]

    for i in range(start + 1, n):
        final_ub[i] = ub[i] if (ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]) else final_ub[i - 1]
        final_lb[i] = lb[i] if (lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]) else final_lb[i - 1]
        if direction[i - 1] == -1:
            direction[i] = 1 if close[i] > final_ub[i] else -1
        else:
            direction[i] = -1 if close[i] < final_lb[i] else 1
        st[i] = final_lb[i] if direction[i] == 1 else final_ub[i]

    return pd.DataFrame(
        {"supertrend": st, "supertrend_dir": direction}, index=df.index
    )


# ── Trend strength ───────────────────────────────────────────────────────────

def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_ = _wilder(true_range(df), period)
    plus_di = 100 * _wilder(plus_dm, period) / atr_.replace(0, np.nan)
    minus_di = 100 * _wilder(minus_dm, period) / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({
        "adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di,
    })


# ── Volume ───────────────────────────────────────────────────────────────────

def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum().rename("obv")


def relative_volume(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume vs the trailing average (excluding the current bar).
    > 1.5 is a meaningful spike; < 0.5 is a dead tape."""
    avg = df["volume"].shift().rolling(period).mean()
    return (df["volume"] / avg.replace(0, np.nan)).rename("rvol")
