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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optionspilot.core.models import Timeframe                      # noqa: E402
from optionspilot.data.capabilities import YAHOO_CAPABILITIES       # noqa: E402
from optionspilot.data.discovery import (                           # noqa: E402
    CapabilityStore, discover, drift,
)
from optionspilot.data.yahoo_provider import YahooChartAdapter      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--pause", type=float, default=0.15,
                    help="seconds between requests (be polite)")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the shipped table exceeds what was measured")
    ap.add_argument("--save", metavar="PATH",
                    help="persist the measurement to a capability store JSON file")
    args = ap.parse_args()

    adapter = YahooChartAdapter()
    now = datetime.now(timezone.utc)
    print(f"probing {adapter.provider_name} with {args.symbol} at "
          f"{now.isoformat(timespec='seconds')}\n")
    print(f"{'interval':>8}  {'measured':>10}  {'shipped':>9}  probes")
    print("-" * 96)

    # The measurement itself lives in `optionspilot/data/discovery.py` so the
    # app and this script cannot disagree about how depth is measured.
    result = discover(adapter, args.symbol, now=now, pause=args.pause)

    for tf in Timeframe:
        finding = result.intervals.get(str(tf))
        if finding is None:
            print(f"{str(tf):>8}  {'unsupported':>10}")
            continue
        shipped = YAHOO_CAPABILITIES.max_lookback_days(tf)
        if not finding.served:
            measured_s = "none"
        elif finding.max_lookback_days is None:
            measured_s = "unlimited"
        else:
            measured_s = f"{finding.max_lookback_days}d"
        shipped_s = "unlimited" if shipped is None else f"{shipped}d"
        print(f"{str(tf):>8}  {measured_s:>10}  {shipped_s:>9}  {finding.probes}")

    print(f"\n{result.requests_spent} upstream requests spent.")

    if args.save:
        store = CapabilityStore(args.save)
        store.save(result)
        print(f"saved to {args.save}")

    problems = drift(result, YAHOO_CAPABILITIES)
    print()
    if problems:
        print("TABLE DRIFT — capabilities.py promises more than the provider serves:")
        for item in problems:
            print(f"  - {item}")
        print("\nUpdate YAHOO_INTERVALS (and test_capabilities.py) to match.")
        return 1 if args.check else 0
    print("OK: the shipped capability table is within what the provider serves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
