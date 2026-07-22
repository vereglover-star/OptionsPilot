"""Yahoo Finance data provider (free, no API key).

Known limitations, accepted for v1 paper trading (see ARCHITECTURE.md §5):
  - Quotes are delayed (~15 min for most US equities).
  - Intraday history windows are limited (~60 days of 5m, ~730 days of 1h).
  - No 4h interval upstream — built here by resampling 1h bars.
  - Option chains include IV/OI/volume but not greeks; greeks are computed
    downstream by analysis.options_metrics (Phase 2).
"""

from __future__ import annotations

import importlib
import threading
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import OptionContract, OptionRight, Quote, Timeframe, utcnow
from optionspilot.data.base import MarketDataProvider, validate_candles

log = get_logger("data")

# Upper bound (seconds) on any single upstream HTTP request. Yahoo occasionally
# hangs a connection; without a cap the worker thread blocks forever and stalls
# every queued request for that key (see get_candles for the failure it caused).
REQUEST_TIMEOUT = 10.0

# yfinance costs ~0.3s to import and drags in its whole scraping stack; defer
# it to the first actual data request so app startup (and every CLI command
# that never fetches) stays fast.
yf = None


def _yf():
    global yf
    if yf is None:
        yf = importlib.import_module("yfinance")
    return yf

# Fetch spec per timeframe: (yfinance interval, pandas resample rule or None).
# Intervals Yahoo doesn't serve natively (3m, 10m, 2h, 4h) are built by
# resampling the nearest finer native interval. Adding a future interval is
# one line here (plus the enum/window/TTL entries the models.py docstring
# lists — test_models enforces completeness).
_FETCH_SPEC: dict[Timeframe, tuple[str, str | None]] = {
    Timeframe.M1: ("1m", None),
    Timeframe.M2: ("2m", None),
    Timeframe.M3: ("1m", "3min"),
    Timeframe.M5: ("5m", None),
    Timeframe.M10: ("5m", "10min"),
    Timeframe.M15: ("15m", None),
    Timeframe.M30: ("30m", None),
    Timeframe.H1: ("1h", None),
    Timeframe.H2: ("1h", "2h"),
    Timeframe.H4: ("1h", "4h"),
    Timeframe.D1: ("1d", None),
    Timeframe.W1: ("1wk", None),
    Timeframe.MN1: ("1mo", None),
}

# Yahoo's free intraday feed has a tighter usable window than the app's
# broader chart-history window. If we ask for an older range than Yahoo can
# serve for that interval, the provider returns an empty frame and the chart
# looks like history simply "stopped" at an arbitrary date. Clamp the request
# to the first supported start so the app still reaches the earliest bar Yahoo
# can actually provide for that interval.
_HISTORY_MAX_DAYS: dict[Timeframe, int | None] = {
    Timeframe.M1: 7,
    Timeframe.M2: 7,
    Timeframe.M3: 7,
    Timeframe.M5: 60,
    Timeframe.M10: 60,
    Timeframe.M15: 60,
    Timeframe.M30: 60,
    Timeframe.H1: 730,
    Timeframe.H2: 730,
    Timeframe.H4: 730,
    Timeframe.D1: None,
    Timeframe.W1: None,
    Timeframe.MN1: None,
}


def _symbol_candidates(symbol: str) -> list[str]:
    """Best-effort Yahoo symbol variants.

    Yahoo Finance uses hyphens where many data sources and users type dots
    (for example, BRK.B -> BRK-B). We keep the original spelling first so
    ordinary symbols stay on the fast path, then try the common punctuation
    variant before giving up.
    """
    raw = symbol.upper().strip()
    variants = [raw]
    if "." in raw:
        variants.append(raw.replace(".", "-"))
    if "-" in raw:
        variants.append(raw.replace("-", "."))
    # preserve order while dropping duplicates
    return list(dict.fromkeys(v for v in variants if v))


def _clamp_history_window(timeframe: Timeframe, start: datetime, end: datetime
                          ) -> tuple[datetime, datetime]:
    max_days = _HISTORY_MAX_DAYS.get(timeframe)
    if max_days is None:
        return start, end
    oldest_allowed = end - timedelta(days=max_days)
    if start < oldest_allowed:
        return oldest_allowed, end
    return start, end


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    # 0.15s between requests is well inside Yahoo's tolerance for the small
    # request counts the CachedProvider lets through (was 0.5s when every scan
    # cycle re-fetched all symbols x all timeframes serially).
    def __init__(self, min_request_interval: float = 0.15):
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
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime,
        *, extended_hours: bool = False,
    ) -> pd.DataFrame:
        self._throttle()
        interval, resample_rule = _FETCH_SPEC[timeframe]
        requested_start = start
        start, end = _clamp_history_window(timeframe, start, end)
        if start != requested_start:
            log.info("history request for %s %s clamped to Yahoo-supported window %s..%s (requested %s..%s)",
                     symbol, timeframe, start, end, requested_start, end)
        # Pre-/after-market bars come back only when prepost=True, and only for
        # intraday intervals — daily+ bars are RTH aggregates upstream, so the
        # flag is a no-op there. This is display-only: the engine/trading path
        # never sets it (see CachedProvider), so paper execution is unchanged.
        prepost = extended_hours and timeframe.minutes < Timeframe.D1.minutes
        raw = pd.DataFrame()
        last_symbol = symbol
        for candidate in _symbol_candidates(symbol):
            last_symbol = candidate
            # `timeout` bounds the underlying HTTP request. Without it a hung
            # Yahoo connection blocks this thread indefinitely — and because it
            # holds the CachedProvider's per-key in-flight slot, every later
            # request for the same symbol/timeframe piles up behind it, which
            # surfaced as a chart that "loads blank and stays blank until the app
            # is restarted" (V3.3.1). On timeout yfinance raises; we fall through
            # to the next candidate / an empty frame, handled by the caller.
            try:
                raw = _yf().Ticker(candidate).history(
                    start=start, end=end,
                    interval=interval,
                    auto_adjust=False, actions=False, prepost=prepost,
                    timeout=REQUEST_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001 — a timeout/network error is
                log.warning("history fetch failed %s %s: %s",  # not fatal: try the
                            candidate, timeframe, exc)          # next variant, else empty
                raw = pd.DataFrame()
            if not raw.empty:
                break
        if raw.empty:
            log.warning("history fetch empty %s %s requested %s..%s (clamped %s..%s)",
                        symbol, timeframe, requested_start, end, start, end)
            return validate_candles(pd.DataFrame())
        df = raw.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        if df.index.tz is None:  # daily bars come back tz-naive
            df.index = df.index.tz_localize("UTC")
        df = validate_candles(df, context=f"{last_symbol} {timeframe}")
        if resample_rule is not None:
            df = _resample(df, resample_rule)
        log.debug("provider history %s %s requested %s..%s -> %d bars %s..%s",
                  symbol, timeframe, requested_start, end, len(df),
                  df.index[0] if not df.empty else None,
                  df.index[-1] if not df.empty else None)
        return df

    def get_quote(self, symbol: str) -> Quote:
        self._throttle()
        last = 0.0
        used_symbol = symbol
        info = None
        for candidate in _symbol_candidates(symbol):
            used_symbol = candidate
            try:
                info = _yf().Ticker(candidate).fast_info
                last = float(info["last_price"])
            except Exception:  # noqa: BLE001 - retry common symbol variants
                continue
            if last > 0:
                break
        if last <= 0:
            raise ValueError(f"could not resolve quote for {symbol!r}")
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
            for candidate in _symbol_candidates(symbol):
                try:
                    cap = _yf().Ticker(candidate).fast_info["market_cap"]
                except Exception:  # noqa: BLE001 - try common symbol variants
                    continue
                return float(cap) if cap else None
        except Exception:  # noqa: BLE001 — sorting metadata is never critical
            return None
        return None

    def get_expirations(self, symbol: str) -> list[date]:
        self._throttle()
        for candidate in _symbol_candidates(symbol):
            try:
                return sorted(
                    datetime.strptime(s, "%Y-%m-%d").date()
                    for s in _yf().Ticker(candidate).options
                )
            except Exception:  # noqa: BLE001 - retry common symbol variants
                continue
        return []

    def get_option_chain(self, symbol: str, expiration: date) -> list[OptionContract]:
        self._throttle()
        chain = None
        used_symbol = symbol
        for candidate in _symbol_candidates(symbol):
            used_symbol = candidate
            try:
                chain = _yf().Ticker(candidate).option_chain(
                    expiration.strftime("%Y-%m-%d"))
                break
            except Exception:  # noqa: BLE001 - retry common symbol variants
                continue
        if chain is None:
            return []
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
