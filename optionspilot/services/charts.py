"""ChartService — the Charts tab's data, computed without a web server.

V0.9.2-C2. This is a *move*, not a rewrite: `ui/server.py::candles_payload` is
now three lines that forward here, and `tests/test_chart_service.py` pins the
exact bytes the old implementation produced so the two cannot have diverged.
It is the first of the four extractions (C2–C5) and it goes first because it is
the smallest, which makes it the cheapest place to get the pattern wrong.

**The pattern the later extractions follow.**

*Collaborators are injected and duck-typed.* Three of them: a `provider`
source, the indicator settings, and a `market_open` predicate. Nothing here
knows what an Orchestrator is, so a CLI or a future mobile backend can render
the same chart by supplying three callables. `provider` is a *callable*
returning the provider rather than the provider itself, because a registry that
captures a collaborator freezes it — `ServiceRegistry` learned that once with
`_live_symbol_check`, and the UI suite swaps provider methods on a live
orchestrator between requests.

*The window policy is handed in, not restated.* `window_days` comes from
`orchestrator.WINDOW_DAYS`, which remains its owner. A default table here would
be a second place tracking one fact, and the two would eventually disagree
about how far back a 5-minute chart opens.

*No lock, and that is a domain property rather than an oversight.* This service
reads a provider and touches no orchestrator state, so a chart load can never
contend with a running scan — which is exactly when a user is most likely to be
looking at one. `tests/test_chart_service.py::TestItTakesNoLock` asserts it
structurally, because moving code is the moment such a property gets lost.

*The return value stays a `dict`.* Every other service in this layer returns a
frozen view model, and this one deliberately does not: the payload spreads
`result.as_meta()` from the market-data service, whose keys are that layer's to
choose. A view model would have to freeze them, which is a behaviour change
wearing a refactor's clothes — and C2 is a move. If the meta block is ever
given a stated shape, this becomes a view model in the same commit.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from optionspilot.analysis import indicators as ind
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe, utcnow
from optionspilot.data import sessions
from optionspilot.data.base import validate_candles

log = get_logger("ui")


class ChartService:
    """OHLCV plus indicator series for one symbol and timeframe."""

    def __init__(self, *, provider, indicators, market_open, window_days=None,
                 clock=None):
        #: Called on every request — never stored — so a provider (or one of
        #: its methods) replaced after construction is the one actually used.
        self._provider = provider
        self._indicators = indicators
        self._market_open = market_open
        self._window_days = dict(window_days or {})
        #: The host's clock, not this module's. Two things are decided from
        #: "now" — the default `start` and the market-open flag — and a service
        #: reading its own module-level `utcnow` answers from a different clock
        #: than the app around it. That is not hypothetical: it is what this
        #: extraction did on its first pass, and nine market-data tests caught
        #: it. The default keeps a bare `ChartService(...)` usable.
        self._clock = clock or utcnow

    def _default_start(self, end: datetime, tf: Timeframe) -> datetime:
        days = self._window_days.get(tf)
        if days is None:
            # A timeframe the host gave no window for. The old code indexed
            # `WINDOW_DAYS[tf]` and turned that into a 500; a chart is worth
            # more than the exact window, so it falls back. This does NOT
            # protect against a host that forgets the table entirely — every
            # timeframe would quietly get 30 days — so the wiring is asserted
            # instead, by `TestTheWindowTableHasOneOwner`.
            days = 30
        return end - timedelta(days=days)

    def candles_payload(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        extended_hours: bool = False,
    ) -> dict:
        """OHLCV + indicator series for the Charts tab, computed by the SAME
        analysis library the engine trades with (guaranteed visual parity).
        Provider-only — no orchestrator state, so no lock is taken and chart
        loads never contend with a running scan."""
        symbol = symbol.upper()
        tf = Timeframe.from_string(timeframe)
        end = end or self._clock()
        start = start or self._default_start(end, tf)
        if end < start:
            start, end = end, start
        provider = self._provider()
        # display surface: prefer clearly-flagged stale bars over a blank
        # chart when the live fetch fails (feature-detected so tests that
        # inject bare fake providers keep working)
        # Extended hours only exists for intraday intervals; daily+ bars are RTH
        # aggregates so the flag is forced off there (keeps the cache key and the
        # session tagging honest).
        ext = extended_hours and tf.minutes < Timeframe.D1.minutes
        # `get_history` returns the full result — which tier answered, which
        # provider, whether the window is older than anything can serve, the
        # validation report and the diagnostics trace id — so the frontend can
        # be EXPLICIT about its state instead of inferring one from an empty
        # array. Feature-detected so tests injecting a bare fake provider (and
        # any legacy adapter) keep working on the older two-method contract.
        get_history = getattr(provider, "get_history", None)
        meta: dict = {}
        if get_history is not None:
            result = (get_history(symbol, tf, start, end, extended_hours=True)
                      if ext else get_history(symbol, tf, start, end))
            df, stale = result.frame, result.stale
            meta = result.as_meta()
        else:
            stale_ok = getattr(provider, "get_candles_stale_ok", None)
            # Only thread the kwarg when actually requesting extended hours, so
            # plain 4-arg providers are unaffected.
            if stale_ok is not None:
                df, stale = (stale_ok(symbol, tf, start, end, extended_hours=True)
                             if ext else stale_ok(symbol, tf, start, end))
            else:
                df = (provider.get_candles(symbol, tf, start, end,
                                           extended_hours=True)
                      if ext else provider.get_candles(symbol, tf, start, end))
                stale = False
        # One sanitization choke point for everything derived below: candles
        # AND indicator series. Providers validate their own output, but this
        # endpoint must stay robust to any that don't — a single non-finite
        # bar otherwise poisons computed indicators (inf VWAP from one inf
        # high) and 500s the response during JSON serialization.
        df = validate_candles(df, context=f"/api/candles {symbol} {timeframe}")
        # Whether the US market is open right now decides how the frontend reads
        # a stale (disk-fallback) payload: while the market is CLOSED the newest
        # cached bar already IS the freshest bar the market ever produced, so a
        # "live data unavailable" banner would be a category error — the chart
        # is simply showing the last session. Only an OPEN-market stale payload
        # means the display has genuinely fallen behind live prices.
        market_open = self._market_open(self._clock())
        if df.empty:
            # An empty payload is NOT one condition. `meta["outcome"]` says
            # which of them it is — `exhausted` (the window predates every
            # provider: the true start of history, and the frontend must stop
            # asking), `empty` (a holiday or pre-listing window: legitimate),
            # or `failed` (nothing could answer: the only case that deserves an
            # error state). Conflating the three is what made a scroll into old
            # intraday history retry forever.
            return {"symbol": symbol, "timeframe": timeframe, "candles": [],
                    "indicators": {}, "stale": False, "market_open": market_open,
                    **meta}

        icfg = self._indicators
        close = df["close"]
        series: dict[str, list] = {}

        def col(name: str, s) -> None:
            series[name] = [round(float(v), 4) if math.isfinite(v) else None
                            for v in s]

        if icfg.ema:
            for period in icfg.ema_periods[:3]:
                col(f"ema{period}", ind.ema(close, period))
        if icfg.vwap and tf.minutes < Timeframe.D1.minutes:
            col("vwap", ind.vwap(df))
        if icfg.bollinger:
            bb = ind.bollinger(close)
            col("bb_upper", bb["bb_upper"])
            col("bb_lower", bb["bb_lower"])
            col("bb_mid", bb["bb_mid"])
        if icfg.rsi:
            col("rsi", ind.rsi(close, icfg.rsi_period))
        if icfg.macd:
            m = ind.macd(close)
            col("macd", m["macd"])
            col("macd_signal", m["macd_signal"])
            col("macd_hist", m["macd_hist"])

        times = [int(ts.timestamp()) for ts in df.index]
        # Per-bar session labels only when extended hours are shown (in RTH-only
        # mode every bar is regular, so the field is omitted to keep the payload
        # lean; the frontend defaults a missing session to "rth").
        sess = sessions.labels(df.index) if ext else None
        # validate_candles() above already dropped non-finite bars; these
        # guards are the last line of defense — one rogue float would 500 the
        # whole endpoint during JSON serialization (allow_nan=False).
        candles = []
        for i, (t, r) in enumerate(zip(times, df.itertuples(index=False))):
            if not all(math.isfinite(v) for v in (r.open, r.high, r.low, r.close)):
                continue
            bar = {"time": t, "open": round(r.open, 4), "high": round(r.high, 4),
                   "low": round(r.low, 4), "close": round(r.close, 4),
                   "volume": int(r.volume) if math.isfinite(r.volume) else 0}
            if sess is not None:
                bar["session"] = sess[i]
            candles.append(bar)
        log.debug("candles %s %s: %d bars%s%s", symbol, timeframe, len(candles),
                  " (stale)" if stale else "", " ext" if ext else "")
        return {"symbol": symbol, "timeframe": timeframe,
                "candles": candles, "indicators": series, "stale": stale,
                "as_of": times[-1] if stale else None,
                "market_open": market_open, "extended_hours": ext, **meta}
