"""Yahoo Finance data provider (free, no API key).

Known limitations, accepted for v1 paper trading (see ARCHITECTURE.md §5):
  - Quotes are delayed (~15 min for most US equities).
  - Intraday history windows are limited (~60 days of 5m, ~730 days of 1h).
  - No 4h interval upstream — built here by resampling 1h bars.
  - Option chains include IV/OI/volume but not greeks; greeks are computed
    downstream by analysis.options_metrics (Phase 2).
"""

from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from optionspilot.core.models import OptionContract, OptionRight, Quote, Timeframe, utcnow
from optionspilot.data.base import MarketDataProvider, validate_candles

_YF_INTERVAL = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "1h",   # fetched as 1h, resampled to 4h below
    Timeframe.D1: "1d",
}


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def __init__(self, min_request_interval: float = 0.5):
        self._min_interval = min_request_interval
        self._last_request = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._min_interval - (_time.monotonic() - self._last_request)
            if wait > 0:
                _time.sleep(wait)
            self._last_request = _time.monotonic()

    def get_candles(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pd.DataFrame:
        self._throttle()
        raw = yf.Ticker(symbol).history(
            start=start, end=end,
            interval=_YF_INTERVAL[timeframe],
            auto_adjust=False, actions=False,
        )
        if raw.empty:
            return validate_candles(pd.DataFrame())
        df = raw.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        if df.index.tz is None:  # daily bars come back tz-naive
            df.index = df.index.tz_localize("UTC")
        df = validate_candles(df)
        if timeframe is Timeframe.H4:
            df = _resample(df, "4h")
        return df

    def get_quote(self, symbol: str) -> Quote:
        self._throttle()
        info = yf.Ticker(symbol).fast_info
        last = float(info["last_price"])
        # Yahoo bid/ask are often stale or zero outside market hours; fall back
        # to a synthetic quote around last so downstream math stays sane.
        bid = float(info.get("bid") or 0) or last
        ask = float(info.get("ask") or 0) or last
        if ask < bid:
            bid = ask = last
        return Quote(symbol=symbol.upper(), ts=utcnow(), bid=bid, ask=ask, last=last)

    def get_market_cap(self, symbol: str) -> float | None:
        """Best-effort market cap for watchlist sorting (not part of the
        Broker/data ABC — callers must feature-detect)."""
        self._throttle()
        try:
            cap = yf.Ticker(symbol).fast_info["market_cap"]
            return float(cap) if cap else None
        except Exception:  # noqa: BLE001 — sorting metadata is never critical
            return None

    def get_expirations(self, symbol: str) -> list[date]:
        self._throttle()
        return sorted(
            datetime.strptime(s, "%Y-%m-%d").date()
            for s in yf.Ticker(symbol).options
        )

    def get_option_chain(self, symbol: str, expiration: date) -> list[OptionContract]:
        self._throttle()
        chain = yf.Ticker(symbol).option_chain(expiration.strftime("%Y-%m-%d"))
        out: list[OptionContract] = []
        for frame, right in ((chain.calls, OptionRight.CALL), (chain.puts, OptionRight.PUT)):
            for row in frame.itertuples(index=False):
                out.append(OptionContract(
                    underlying=symbol.upper(),
                    expiration=expiration,
                    strike=float(row.strike),
                    right=right,
                    bid=_f(getattr(row, "bid", 0)),
                    ask=_f(getattr(row, "ask", 0)),
                    last=_f(getattr(row, "lastPrice", 0)),
                    volume=_i(getattr(row, "volume", 0)),
                    open_interest=_i(getattr(row, "openInterest", 0)),
                    implied_volatility=_f(getattr(row, "impliedVolatility", 0)),
                ))
        return out


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    return validate_candles(out)


def _f(v) -> float:
    return 0.0 if v is None or pd.isna(v) else float(v)


def _i(v) -> int:
    return 0 if v is None or pd.isna(v) else int(v)
