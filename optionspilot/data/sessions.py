"""US equity trading-session classification.

Pure, dependency-light helpers that label a timestamp as pre-market, regular
(RTH), or after-hours based on its US/Eastern wall-clock time. Used by the
chart's extended-hours mode to shade sessions and by any future consumer (the
AI scanner, replay) that needs to reason about which session a bar belongs to.

Boundaries follow the standard US equity schedule:
  pre-market   04:00–09:30 ET
  regular      09:30–16:00 ET
  after-hours  16:00–20:00 ET

Known limitation: this is a time-of-day classifier only. It does NOT know about
market holidays or early-close (half) days — on those days a bar is still
labeled by its clock time. yfinance simply won't return bars for a closed
session, so the practical effect is limited to half-day after-hours labeling.
A holiday/half-day calendar can be layered in later without changing callers.
"""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")
_PRE_OPEN = time(4, 0)
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_POST_CLOSE = time(20, 0)

PRE = "pre"
RTH = "rth"
POST = "post"
CLOSED = "closed"


def classify(ts) -> str:
    """Label a single (tz-aware) timestamp's session by its US/Eastern time."""
    et = pd.Timestamp(ts)
    if et.tzinfo is None:
        et = et.tz_localize("UTC")
    t = et.tz_convert(_ET).time()
    if _RTH_OPEN <= t < _RTH_CLOSE:
        return RTH
    if _PRE_OPEN <= t < _RTH_OPEN:
        return PRE
    if _RTH_CLOSE <= t < _POST_CLOSE:
        return POST
    return CLOSED


def labels(index: pd.DatetimeIndex) -> list[str]:
    """Vectorized session labels for a DatetimeIndex (one per bar)."""
    if len(index) == 0:
        return []
    idx = index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    et = idx.tz_convert(_ET)
    mins = et.hour * 60 + et.minute
    out: list[str] = []
    o, c, p, q = 9 * 60 + 30, 16 * 60, 4 * 60, 20 * 60
    for m in mins:
        if o <= m < c:
            out.append(RTH)
        elif p <= m < o:
            out.append(PRE)
        elif c <= m < q:
            out.append(POST)
        else:
            out.append(CLOSED)
    return out


__all__ = ["classify", "labels", "PRE", "RTH", "POST", "CLOSED"]
