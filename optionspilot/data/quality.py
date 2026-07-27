"""Validation and scoring for candle history, before it can reach a chart.

`base.validate_candles` is the *shape* gate — it guarantees the canonical frame
(UTC index, five float columns, no duplicates, no non-finite OHLC) and every
provider still runs it. This module is the *semantic* gate on top: it answers
"is this dataset believable?" and returns a report rather than only a cleaned
frame, so the service layer can decide between using it, preferring another
provider's answer, or refusing to render it at all.

Design rules:
  - Pure. No I/O, no network, no clock reads except the `now` passed in. This
    mirrors the `analysis/` convention so the same checks run in tests, in the
    live app, and in the backtester.
  - Never raise for bad data. Bad data is a *result* (`HistoryReport`), not an
    exception — the service has to fall back, and a traceback is not a fallback.
  - Repairs are conservative and always reported. We drop bars we can prove are
    wrong; we do not invent, interpolate, or smooth anything.

The distinction that matters downstream is `usable`: a frame with a few dropped
glitch bars is usable (score < 100, `issues` non-empty); a frame whose bars are
mostly off-grid, or which is empty after repair, is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.base import CANDLE_COLUMNS, validate_candles

log = get_logger("data")

#: The app's bar convention, asserted here so it's stated exactly once.
#: Split-adjusted (a 4:1 split must not print a 75% gap), dividend-UNadjusted
#: (the chart shows the price that actually traded). Providers that can only
#: serve fully-adjusted bars declare that in their capabilities; the service
#: refuses to *mix* conventions inside one frame.
ADJUSTMENT_CONVENTION = "split-adjusted, dividend-unadjusted"

#: A bar whose open time is further in the future than this is impossible and
#: is dropped. Small tolerance: providers legitimately stamp the forming bar at
#: its open, and clocks drift.
FUTURE_TOLERANCE = timedelta(minutes=2)

#: Calendar frames (1d/1w/1mo) have inherently uneven spacing — 28-to-31-day
#: months, holiday weeks. The interval check allows a spacing this fraction of
#: the nominal interval before calling it a mismatch.
CALENDAR_TOLERANCE = 0.5

#: A single bar whose range exceeds this multiple of the frame's median range
#: AND which reverts immediately is treated as a bad print, not a real move.
SPIKE_RANGE_MULTIPLE = 25.0


@dataclass(slots=True)
class HistoryReport:
    """What validation found. Attached to every served frame for diagnostics."""

    bars: int = 0
    #: 0-100. 100 = nothing to report. Each defect class subtracts a weight.
    score: float = 100.0
    usable: bool = True
    #: Human-readable defect strings, e.g. "dropped 2 bars with high<low".
    issues: list[str] = field(default_factory=list)
    #: Counts by defect class, for diagnostics aggregation.
    counts: dict[str, int] = field(default_factory=dict)
    first: datetime | None = None
    last: datetime | None = None
    #: False when the bars are spaced like a DIFFERENT interval than requested
    #: (a provider silently serving daily bars for a 1-minute request).
    interval_ok: bool = True
    #: Tightest and widest observed spacing, in whole intervals.
    min_gap_intervals: float = 1.0
    max_gap_intervals: int = 1

    def note(self, key: str, count: int, message: str, penalty: float) -> None:
        if count <= 0:
            return
        self.counts[key] = self.counts.get(key, 0) + count
        self.issues.append(message)
        self.score = max(0.0, self.score - penalty)

    def as_dict(self) -> dict:
        return {
            "bars": self.bars,
            "score": round(self.score, 1),
            "usable": self.usable,
            "issues": list(self.issues),
            "counts": dict(self.counts),
            "first": self.first.isoformat() if self.first else None,
            "last": self.last.isoformat() if self.last else None,
            "interval_ok": self.interval_ok,
            "min_gap_intervals": round(self.min_gap_intervals, 3),
            "max_gap_intervals": self.max_gap_intervals,
        }


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDLE_COLUMNS,
                        index=pd.DatetimeIndex([], tz="UTC", name="ts"))


def validate_history(df: pd.DataFrame, timeframe: Timeframe, *,
                     now: datetime | None = None,
                     context: str = "") -> tuple[pd.DataFrame, HistoryReport]:
    """Semantic validation + conservative repair. Returns `(frame, report)`.

    Never raises: a frame that cannot be repaired comes back empty with
    `report.usable == False` and the reason in `report.issues`.
    """
    report = HistoryReport()
    try:
        df = validate_candles(df, context=context)
    except Exception as exc:  # noqa: BLE001 — malformed input is a result, not a crash
        report.usable = False
        report.score = 0.0
        report.issues.append(f"unusable frame: {exc}")
        report.counts["malformed"] = 1
        return _empty(), report

    if df.empty:
        report.usable = False
        report.score = 0.0
        report.issues.append("no bars")
        return df, report

    n_in = len(df)

    # ── impossible timestamps ────────────────────────────────────────────────
    if now is not None:
        horizon = pd.Timestamp(now) + FUTURE_TOLERANCE
        future = df.index > horizon
        n_future = int(future.sum())
        if n_future:
            df = df[~future]
            report.note("future_ts", n_future,
                        f"dropped {n_future} bars stamped in the future", 20.0)
        if df.empty:
            report.usable = False
            report.score = 0.0
            report.issues.append("every bar was in the future")
            return _empty(), report

    # `validate_candles` already sorted and de-duplicated, so an out-of-order
    # index here would be a bug in it. Assert it cheaply rather than trust it.
    if not df.index.is_monotonic_increasing:  # pragma: no cover — defensive
        df = df.sort_index()
        report.note("unordered", 1, "re-sorted a non-monotonic index", 10.0)

    # ── OHLC self-consistency ────────────────────────────────────────────────
    # high must be the max and low the min of the bar; anything else is a
    # provider defect and makes candle geometry render inverted.
    o, h, l, c = (df["open"], df["high"], df["low"], df["close"])
    body_hi = np.maximum(o, c)
    body_lo = np.minimum(o, c)
    # Tolerate float noise at the 1e-9 relative level; reject real violations.
    tol = np.maximum(np.abs(c) * 1e-9, 1e-9)
    bad_ohlc = (h < l - tol) | (h < body_hi - tol) | (l > body_lo + tol)
    n_bad = int(bad_ohlc.sum())
    if n_bad:
        df = df[~bad_ohlc]
        report.note("ohlc_inconsistent", n_bad,
                    f"dropped {n_bad} bars with inconsistent OHLC", 15.0)
    if df.empty:
        report.usable = False
        report.score = 0.0
        report.issues.append("no bars survived OHLC validation")
        return _empty(), report

    # ── negative volume ──────────────────────────────────────────────────────
    neg_vol = df["volume"] < 0
    n_neg = int(neg_vol.sum())
    if n_neg:
        df = df.copy()
        df.loc[neg_vol, "volume"] = 0.0
        report.note("negative_volume", n_neg,
                    f"zeroed {n_neg} negative volumes", 3.0)

    # ── isolated price spikes (bad prints) ───────────────────────────────────
    df, n_spikes = _drop_spikes(df)
    if n_spikes:
        report.note("price_spike", n_spikes,
                    f"dropped {n_spikes} isolated bad prints", 8.0)

    # ── interval conformance + gaps ──────────────────────────────────────────
    interval_ok, min_gap, max_gap = _interval_stats(df, timeframe)
    report.interval_ok = interval_ok
    report.min_gap_intervals = min_gap
    report.max_gap_intervals = max_gap
    if not interval_ok:
        # The bars are spaced like a different interval than the one requested.
        # Rendering them would silently mislabel the chart's axis, so the frame
        # is refused and the service fails over. (This is the data-side twin of
        # the adapter's `dataGranularity` check.)
        report.usable = False
        report.score = 0.0
        report.issues.append(
            f"bar spacing does not match {timeframe} (tightest gap is "
            f"{min_gap:.2f} intervals) — wrong interval served")
        report.bars = len(df)
        report.first, report.last = df.index[0], df.index[-1]
        log.error("validate_history%s: interval mismatch (min gap %.2f intervals)",
                  f" [{context}]" if context else "", min_gap)
        return _empty(), report
    if max_gap > 1:
        # Gaps are normal (overnight, weekends, holidays) and are NOT a defect —
        # they are recorded for diagnostics only, with no score penalty.
        report.counts["gap_intervals"] = max_gap

    report.bars = len(df)
    report.first, report.last = df.index[0], df.index[-1]
    dropped = n_in - len(df)
    if dropped:
        log.warning("validate_history%s: %d/%d bars removed (%s)",
                    f" [{context}]" if context else "", dropped, n_in,
                    "; ".join(report.issues))
    return df, report


def _drop_spikes(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove single bars whose range dwarfs the frame's and which the
    neighbours do not corroborate — a classic bad print (a 0.01 low on an
    otherwise 400-dollar stock). Requires >= 5 bars so a short frame is never
    "corrected" against a meaningless median.
    """
    if len(df) < 5:
        return df, 0
    rng = (df["high"] - df["low"]).to_numpy()
    median = float(np.median(rng[rng > 0])) if (rng > 0).any() else 0.0
    if median <= 0:
        return df, 0
    close = df["close"].to_numpy()
    prev_close = np.concatenate([[close[0]], close[:-1]])
    next_close = np.concatenate([close[1:], [close[-1]]])
    # Neighbours agree with each other but not with this bar's extremes.
    neighbour_mid = (prev_close + next_close) / 2.0
    reverts = np.abs(next_close - prev_close) < median * 3.0
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    excursion = np.maximum(np.abs(high - neighbour_mid),
                           np.abs(neighbour_mid - low))
    spike = (rng > median * SPIKE_RANGE_MULTIPLE) & reverts & \
            (excursion > median * SPIKE_RANGE_MULTIPLE / 2.0)
    # never drop the newest bar: it is legitimately half-formed
    spike[-1] = False
    n = int(spike.sum())
    return (df[~spike], n) if n else (df, 0)


def _interval_stats(df: pd.DataFrame,
                    timeframe: Timeframe) -> tuple[bool, float, int]:
    """(is this really `timeframe`?, tightest gap, widest gap) in intervals.

    The test is on the TIGHTEST spacing, not on how many bars sit on a grid.
    That distinction matters: a 4-hour chart of US equities has two bars per
    session and a 20-hour overnight gap, so only half its spacings are exactly
    one interval — a grid-share test rejects perfectly good data, while the
    tightest-gap test passes it and still catches the real defect (a provider
    answering a 1-minute request with daily bars, or vice versa).

    A frame is the interval it claims to be when its tightest spacing is
    exactly one interval: never shorter (that would be a finer interval) and
    never longer than one (that would be a coarser one). Calendar frames get
    `CALENDAR_TOLERANCE` slack because months and holiday weeks are uneven.
    """
    if len(df) < 3:
        # Too short to infer spacing from — accept it rather than guess. Two
        # bars either side of a weekend would otherwise look like a mismatch.
        return True, 1.0, 1
    step = timeframe.minutes * 60
    # `.total_seconds()` on the TimedeltaIndex, NOT an int64 cast of the index:
    # pandas 3 returns MICROseconds from `astype("int64")` on a tz-aware index
    # where pandas 2 returned nanoseconds, and a unit assumption here silently
    # turns every spacing check into nonsense (caught by test_cached).
    deltas = (df.index[1:] - df.index[:-1]).total_seconds().to_numpy(dtype=float)
    deltas = deltas[deltas > 0]
    if deltas.size == 0:  # pragma: no cover — duplicates are removed upstream
        return True, 1.0, 1
    min_gap = float(deltas.min()) / step
    max_gap = int(max(1, round(float(deltas.max()) / step)))
    if timeframe.minutes >= Timeframe.D1.minutes:
        ok = CALENDAR_TOLERANCE <= min_gap < (1.0 / CALENDAR_TOLERANCE)
    else:
        # Allow 1% slack for providers that stamp bars a second or two off.
        ok = 0.99 <= min_gap <= 1.01
    return ok, min_gap, max_gap


def disagreement(a: pd.DataFrame, b: pd.DataFrame) -> float | None:
    """Median relative close difference over bars the two frames share.

    Used to compare two providers' answers for the same window. None when they
    overlap in fewer than 3 bars (no meaningful comparison). A value above a
    few thousandths means the providers genuinely disagree — usually one is
    dividend-adjusted and the other is not.
    """
    if a.empty or b.empty:
        return None
    shared = a.index.intersection(b.index)
    if len(shared) < 3:
        return None
    ca = a.loc[shared, "close"].to_numpy()
    cb = b.loc[shared, "close"].to_numpy()
    denom = np.where(np.abs(ca) > 0, np.abs(ca), np.nan)
    rel = np.abs(ca - cb) / denom
    rel = rel[np.isfinite(rel)]
    if rel.size == 0:
        return None
    return float(np.median(rel))


__all__ = ["ADJUSTMENT_CONVENTION", "HistoryReport", "validate_history",
           "disagreement", "CALENDAR_TOLERANCE", "FUTURE_TOLERANCE"]
