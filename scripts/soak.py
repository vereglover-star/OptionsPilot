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


def soak_runtime(cfg, minutes: float) -> int:
    """V0.9.1-C3: soak the SCHEDULER, not just the orchestrator.

    ``main`` below drives ``run_cycle`` directly and never constructs a
    ``BackgroundRuntime``, so it cannot see a lane, a pool or a starved task —
    it would have passed identically before and after C3. This mode runs the
    real runtime with a long worker task overlapping short coordinator tasks
    and repeated manual triggers, which is the shape V0.9.1 changed.

    Exit criteria, all of which the pre-C3 scheduler fails:

    * coordinator tasks keep ticking at their own interval throughout;
    * the worker task never overlaps itself;
    * nothing accumulates — skipped runs are counted, not queued;
    * shutdown is bounded and leaves no thread behind.
    """
    import threading

    from optionspilot.services.runtime import BackgroundRuntime, TaskSpec

    deadline = time.monotonic() + minutes * 60
    scan_seconds = 0.4          # a "scan" far slower than the tray's interval
    beats: list[float] = []
    scans = {"started": 0, "finished": 0, "overlap": 0}
    inside = threading.Lock()

    def fake_scan():
        if not inside.acquire(blocking=False):
            scans["overlap"] += 1
            return
        try:
            scans["started"] += 1
            time.sleep(scan_seconds)
            scans["finished"] += 1
        finally:
            inside.release()

    runtime = BackgroundRuntime(health_interval=60)
    runtime.register(TaskSpec("scan", 0.2, fake_scan,
                              policy="monitoring", lane="worker"))
    runtime.register(TaskSpec("tray", 0.05, lambda: beats.append(time.monotonic()),
                              policy="essential"))
    baseline_threads = {t.name for t in threading.enumerate() if t.is_alive()}

    print(f"soak(runtime): {minutes:g} min, {scan_seconds:g}s worker scans "
          f"overlapping a 0.05s coordinator task")
    tracemalloc.start()
    started = time.monotonic()
    runtime.start()
    worst_gap = 0.0
    triggers = 0
    try:
        while time.monotonic() < deadline:
            time.sleep(5)
            triggers += 1
            runtime.trigger("scan")          # manual trigger, as a user would
            recent = [b for b in beats if b > time.monotonic() - 5]
            gaps = [b - a for a, b in zip(recent, recent[1:])]
            worst_gap = max([worst_gap, *gaps]) if gaps else worst_gap
            row = next(t for t in runtime.snapshot().tasks if t["name"] == "scan")
            elapsed = time.monotonic() - started
            print(f"  t+{elapsed:6.0f}s  beats {len(beats):>6}  "
                  f"scans {row['runs']:>5}  skipped {row['skipped']:>5}  "
                  f"queued={row['queued']} running={row['running']}  "
                  f"worst beat gap {worst_gap:.2f}s  "
                  f"heap {tracemalloc.get_traced_memory()[0] / 1e6:.1f} MB")
    finally:
        stop_began = time.monotonic()
        runtime.stop(timeout=5)
        stop_took = time.monotonic() - stop_began

    time.sleep(1.0)
    leaked = {t.name for t in threading.enumerate() if t.is_alive()} - baseline_threads
    row = next(t for t in runtime.snapshot().tasks if t["name"] == "scan")
    heap_mb = tracemalloc.get_traced_memory()[0] / 1e6

    print(f"\nbeats: {len(beats)}  worst gap {worst_gap:.2f}s "
          f"(a starved coordinator shows a gap >= the {scan_seconds:g}s scan)")
    print(f"scans: {row['runs']} run, {row['skipped']} skipped, "
          f"{scans['overlap']} overlapping  (manual triggers: {triggers})")
    print(f"stop() took {stop_took:.2f}s   leaked threads: {sorted(leaked) or 'none'}")
    print(f"heap: {heap_mb:.1f} MB (limit {GROWTH_LIMIT_MB} MB)")

    problems = []
    if scans["overlap"]:
        problems.append(f"{scans['overlap']} overlapping scans")
    if worst_gap >= scan_seconds:
        problems.append(f"coordinator starved for {worst_gap:.2f}s")
    if leaked:
        problems.append(f"leaked threads {sorted(leaked)}")
    if stop_took > 6.0:
        problems.append(f"unbounded shutdown ({stop_took:.2f}s)")
    if heap_mb > GROWTH_LIMIT_MB:
        problems.append(f"heap {heap_mb:.1f} MB")
    if row["queued"] or row["running"]:
        problems.append("a task was still queued/running after stop()")

    print("SOAK PASS" if not problems else f"SOAK FAIL: {'; '.join(problems)}")
    return 0 if not problems else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OptionsPilot soak test")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--runtime", action="store_true",
                        help="soak the background scheduler instead of the "
                             "orchestrator (V0.9.1)")
    parser.add_argument("--minutes", type=float, default=30.0,
                        help="duration for --runtime (default 30, the "
                             "milestone's stated exit criterion)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.runtime:
        setup_logging(cfg.logging)
        return soak_runtime(cfg, args.minutes)
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
