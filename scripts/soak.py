"""Soak test: run repeated orchestrator cycles against live data and watch
for instability — exceptions, memory growth, cycle-time drift.

Usage (from the repo root):
    .venv\\Scripts\\python scripts\\soak.py --cycles 10

Runs against a scratch data directory so the real paper account is untouched.
Outside market hours the risk gate vetoes entries, but every heavy subsystem
(data fetch, full multi-timeframe analysis of the whole watchlist, marking,
summaries) still executes — which is what a soak is for. Run it again during
market hours before trusting long unattended sessions.

Exit code 0 = stable: no cycle raised, and heap growth over the run stayed
under the threshold.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optionspilot.config import load_config
from optionspilot.core.logging_setup import setup_logging
from optionspilot.orchestrator import Orchestrator

GROWTH_LIMIT_MB = 30.0   # heap growth allowed between cycle 1 and the last


def main() -> int:
    parser = argparse.ArgumentParser(description="OptionsPilot soak test")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.logging)

    scratch = Path(tempfile.mkdtemp(prefix="optionspilot-soak-"))
    orch = Orchestrator(cfg, data_dir=scratch)
    print(f"soak: {args.cycles} cycles, watchlist {cfg.data.watchlist}, "
          f"scratch dir {scratch}")

    tracemalloc.start()
    baseline_mb = None
    times: list[float] = []
    failures = 0

    for i in range(1, args.cycles + 1):
        t0 = time.perf_counter()
        try:
            summary = orch.run_cycle()
            errors = {s: r for s, r in summary["skipped"].items()
                      if r.startswith("scan error")}
            if errors:
                failures += 1
        except Exception as exc:  # noqa: BLE001 — count, keep soaking
            failures += 1
            errors = {"cycle": str(exc)}
            summary = {"signals": {}}
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        current_mb = tracemalloc.get_traced_memory()[0] / 1e6
        if baseline_mb is None:
            baseline_mb = current_mb
        sigs = {s: f"{v['confidence']:.0f}%" for s, v in summary["signals"].items()}
        print(f"cycle {i:>3}/{args.cycles}: {elapsed:6.1f}s  heap {current_mb:6.1f} MB"
              f"  signals {sigs or '-'}"
              + (f"  ERRORS {errors}" if errors else ""))

    growth = (tracemalloc.get_traced_memory()[0] / 1e6) - (baseline_mb or 0.0)
    print(f"\ncycles: {len(times)}  avg {sum(times)/len(times):.1f}s  "
          f"max {max(times):.1f}s")
    print(f"heap growth after first cycle: {growth:+.1f} MB "
          f"(limit {GROWTH_LIMIT_MB} MB)")
    print(f"cycle failures: {failures}")

    ok = failures == 0 and growth < GROWTH_LIMIT_MB
    print("SOAK PASS" if ok else "SOAK FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
