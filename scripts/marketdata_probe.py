"""Measure what a provider will actually serve, and check it against the table.

`optionspilot/data/capabilities.py` hard-codes each provider's per-interval
history depth. Those numbers came from this script, and they are worth
re-measuring whenever charts start behaving oddly: an upstream provider
tightening its window is indistinguishable, from inside the app, from a bug.

For each interval it walks the requested start further and further back until
the provider refuses, prints the deepest window that worked and the exact
refusal message, and finally compares the measured cliff against the shipped
capability table.

    python scripts/marketdata_probe.py                 # SPY, all intervals
    python scripts/marketdata_probe.py --symbol QQQ
    python scripts/marketdata_probe.py --check         # non-zero if the table drifted

This talks to the real network by design — it is a measurement tool, not a
test. `scripts/marketdata_stress.py` is the offline one.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optionspilot.core.models import Timeframe                      # noqa: E402
from optionspilot.data.adapter import (                             # noqa: E402
    HistoryRequest, ProviderError, ProviderRangeError,
)
from optionspilot.data.capabilities import YAHOO_CAPABILITIES       # noqa: E402
from optionspilot.data.yahoo_provider import YahooChartAdapter      # noqa: E402

# Days back to try, coarse to fine. The cliff is bracketed by the last value
# that worked and the first that did not, then narrowed by a binary search.
LADDER = [1, 3, 7, 8, 15, 30, 59, 60, 61, 90, 180, 365, 729, 730, 731,
          1000, 2000, 4000, 8000, 20000]


def attempt(adapter, symbol: str, tf: Timeframe, days: int,
            now: datetime) -> tuple[str, int, str]:
    """(status, bars, detail) for one probe. Never raises."""
    request = HistoryRequest(symbol, tf, now - timedelta(days=days), now)
    try:
        # Bypass the capability clamp — the whole point is to measure the real
        # limit, not to confirm that our own table clamps to itself.
        spec = adapter.capabilities.spec(tf)
        frame = adapter._fetch_native(symbol, spec, request.start, request.end,
                                      False)
    except ProviderRangeError as exc:
        return "REFUSED", 0, str(exc)[:120]
    except ProviderError as exc:
        return "ERROR", 0, str(exc)[:120]
    except Exception as exc:  # noqa: BLE001 — a measurement must not crash
        return "ERROR", 0, f"{type(exc).__name__}: {exc}"[:120]
    if frame.empty:
        return "EMPTY", 0, "no bars"
    first = frame.index[0].date().isoformat()
    return "OK", len(frame), f"from {first}"


def measure(adapter, symbol: str, tf: Timeframe, now: datetime,
            pause: float) -> tuple[int | None, str]:
    """(deepest days that worked, refusal message). None = no limit found."""
    deepest = None
    refusal = ""
    lo_fail = None
    for days in LADDER:
        status, bars, detail = attempt(adapter, symbol, tf, days, now)
        time.sleep(pause)
        if status == "OK":
            deepest = days
            continue
        if status == "EMPTY":
            # A weekend/holiday window, not a refusal — keep walking back.
            continue
        refusal = detail
        lo_fail = days
        break
    if deepest is None or lo_fail is None:
        return deepest, refusal
    # narrow the cliff between `deepest` (worked) and `lo_fail` (refused)
    lo, hi = deepest, lo_fail
    while hi - lo > 1:
        mid = (lo + hi) // 2
        status, _, detail = attempt(adapter, symbol, tf, mid, now)
        time.sleep(pause)
        if status in ("OK", "EMPTY"):
            lo = mid
        else:
            hi = mid
            refusal = detail
    return lo, refusal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--pause", type=float, default=0.15,
                    help="seconds between requests (be polite)")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the shipped table exceeds what was measured")
    args = ap.parse_args()

    adapter = YahooChartAdapter()
    now = datetime.now(timezone.utc)
    print(f"probing {adapter.provider_name} with {args.symbol} at "
          f"{now.isoformat(timespec='seconds')}\n")
    print(f"{'interval':>8}  {'measured':>10}  {'shipped':>9}  refusal")
    print("-" * 96)

    drift: list[str] = []
    for tf in Timeframe:
        measured, refusal = measure(adapter, args.symbol, tf, now, args.pause)
        shipped = YAHOO_CAPABILITIES.max_lookback_days(tf)
        measured_s = "unlimited" if measured == LADDER[-1] else (
            f"{measured}d" if measured is not None else "none")
        shipped_s = "unlimited" if shipped is None else f"{shipped}d"
        print(f"{str(tf):>8}  {measured_s:>10}  {shipped_s:>9}  {refusal[:60]}")

        if measured is None:
            drift.append(f"{tf}: nothing served at all")
        elif shipped is not None and measured != LADDER[-1] and shipped > measured:
            drift.append(f"{tf}: table says {shipped}d but only {measured}d is served")
        elif shipped is None and measured != LADDER[-1]:
            drift.append(f"{tf}: table says unlimited but only {measured}d is served")

    print()
    if drift:
        print("TABLE DRIFT — capabilities.py promises more than the provider serves:")
        for item in drift:
            print(f"  - {item}")
        print("\nUpdate YAHOO_INTERVALS (and test_capabilities.py) to match.")
        return 1 if args.check else 0
    print("OK: the shipped capability table is within what the provider serves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
