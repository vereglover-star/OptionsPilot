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


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _scan_runs(runtime) -> int:
    return next(t for t in runtime.snapshot().tasks if t["name"] == "scan")["runs"]


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
    scans = {"started": 0, "finished": 0, "overlap": 0, "depth": 0}
    inside = threading.Lock()

    def fake_scan(blocking: bool = True) -> bool:
        """Mirrors `UIServer.run_cycle_now`: one cycle at a time, and a
        non-blocking caller DECLINES rather than queues.

        The acquire is atomic on purpose. An earlier version of this model
        tested `inside.locked()` and then called the body — the same
        check-then-act shape C5 exists to remove — and reported overlaps that
        production cannot have.
        """
        if not inside.acquire(blocking=blocking):
            return False
        try:
            scans["depth"] += 1
            if scans["depth"] > 1:
                scans["overlap"] += 1     # impossible unless the lock is broken
            scans["started"] += 1
            time.sleep(scan_seconds)
            scans["finished"] += 1
            scans["depth"] -= 1
        finally:
            inside.release()
        return True

    # V0.9.1-C5: a manual scan is a runtime task like any other, so the soak
    # runs one. It shares `inside` with the scheduled scan, so any cycle that
    # overlaps another — whichever way it was started — is counted as an
    # overlap and fails the run. That shared lock is the point: before C5 the
    # two paths had separate owners and only `_cycle_lock` stopped them
    # colliding.
    manual = {"requested": 0, "ran": 0, "declined": 0}

    def fake_manual_scan():
        # `_background_scan` calls run_cycle_now(blocking=False): a request
        # arriving while a cycle is running is declined, not queued. Queueing
        # would run a second full cycle the instant the first returned, which
        # is not what the pre-C5 code did.
        if fake_scan(blocking=False):
            manual["ran"] += 1
        else:
            manual["declined"] += 1

    # V0.9.1-C6: the other two jobs the runtime now owns. Both are on-demand
    # worker tasks, so with the scheduled scan and the manual scan the pool has
    # four contenders against a bound of four — the configuration the soak has
    # to exercise, because a bound that is one too small does not error, it
    # just leaves a job sitting at "running" until a slot frees.
    #
    # The backtest models `start_backtest`'s single slot. The claim is atomic:
    # a test-then-set would be the check-then-act shape C5 removed, and the C5
    # soak reported overlaps production cannot have for exactly that reason.
    bt = {"requested": 0, "ran": 0, "refused": 0, "overlap": 0, "depth": 0}
    bt_claim = threading.Lock()
    bt_running = threading.Event()
    refresh = {"requested": 0, "ran": 0}

    def request_backtest() -> None:
        bt["requested"] += 1
        with bt_claim:
            if bt_running.is_set():
                bt["refused"] += 1
                return
            bt_running.set()
        runtime.trigger("backtest")

    def fake_backtest() -> None:
        bt["depth"] += 1
        if bt["depth"] > 1:
            bt["overlap"] += 1       # two backtests at once: the slot failed
        bt["ran"] += 1
        time.sleep(0.25)             # months of candles, then two report files
        bt["depth"] -= 1
        bt_running.clear()

    def fake_refresh() -> None:
        refresh["ran"] += 1
        time.sleep(0.15)             # rebuild the intelligence snapshot

    runtime = BackgroundRuntime(health_interval=60)
    # 0.4s of work on a 0.6s interval: a ~67% duty cycle. Aggressive enough
    # that the coordinator is nearly always competing with a scan (which is
    # what the starvation check needs), but with real gaps, so a manual scan
    # sometimes wins the lock instead of always declining — otherwise C5's
    # path is present in the soak and never exercised by it.
    runtime.register(TaskSpec("scan", 0.6, fake_scan,
                              policy="monitoring", lane="worker"))
    runtime.register(TaskSpec("manual_scan", 3600.0, fake_manual_scan,
                              policy="essential", lane="worker",
                              on_demand=True))
    runtime.register(TaskSpec("backtest", 3600.0, fake_backtest,
                              policy="essential", lane="worker",
                              on_demand=True, restartable=False))
    runtime.register(TaskSpec("intelligence_refresh", 3600.0, fake_refresh,
                              policy="normal", lane="worker",
                              on_demand=True, restartable=False))
    runtime.register(TaskSpec("tray", 0.05, lambda: beats.append(time.monotonic()),
                              policy="essential"))
    baseline_threads = {t.name for t in threading.enumerate() if t.is_alive()}

    print(f"soak(runtime): {minutes:g} min, {scan_seconds:g}s worker scans "
          f"overlapping a 0.05s coordinator task")
    tracemalloc.start()
    started = time.monotonic()
    runtime.start()
    worst_gap = 0.0
    measure_from = started
    triggers = 0
    pauses = 0
    pause_violations = 0
    resume_violations = 0
    try:
        while time.monotonic() < deadline:
            time.sleep(5)
            triggers += 1
            runtime.trigger("scan")          # force the scheduled task due
            # A user pressing "Scan now", repeatedly and impatiently. Before
            # C5 each press could spawn its own thread; now they coalesce.
            # Offset from the forced scheduled scan above: triggering both in
            # the same instant correlates them, so the manual one always lost
            # the lock and never actually ran.
            time.sleep(0.5)
            for _ in range(5):
                manual["requested"] += 1
                runtime.trigger("manual_scan")

            # V0.9.1-C6: a user pressing Run Backtest impatiently (the second
            # press must be refused by the slot, not queued) and a cache warm.
            # Both land while the scheduled scan is very likely mid-cycle, so
            # all four worker tasks contend for the pool.
            for _ in range(3):
                request_backtest()
            refresh["requested"] += 1
            runtime.trigger("intelligence_refresh")

            # V0.9.1-C4: pause/resume under load, every third pass. Pause stops
            # DISPATCH without interrupting work, so the invariants are:
            #   1. the pause settles (in-flight scan finishes, nothing hangs);
            #   2. no scan starts while paused;
            #   3. resume restarts dispatch.
            if triggers % 3 == 0:
                pauses += 1
                runtime.pause()

                settled = _wait_until(
                    lambda: not runtime.snapshot().pause_pending, 10.0)
                if not settled:
                    pause_violations += 1

                quiet = _scan_runs(runtime)
                time.sleep(1.0)
                if _scan_runs(runtime) != quiet:
                    pause_violations += 1     # dispatched while paused

                runtime.resume()
                if not _wait_until(lambda: _scan_runs(runtime) > quiet, 10.0):
                    resume_violations += 1
                # A paused coordinator legitimately stops ticking, so the gap
                # spanning a pause window is not starvation. Measure only from
                # here on, or the instrumentation reports the feature it is
                # testing as the defect it is testing for.
                measure_from = time.monotonic()
            recent = [b for b in beats
                      if b > max(measure_from, time.monotonic() - 5)]
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
        abandoned = runtime.stop(timeout=5)
        stop_took = time.monotonic() - stop_began

    time.sleep(1.0)
    leaked = {t.name for t in threading.enumerate() if t.is_alive()} - baseline_threads
    row = next(t for t in runtime.snapshot().tasks if t["name"] == "scan")
    heap_mb = tracemalloc.get_traced_memory()[0] / 1e6

    print(f"\nbeats: {len(beats)}  worst gap {worst_gap:.2f}s "
          f"(a starved coordinator shows a gap >= the {scan_seconds:g}s scan)")
    print(f"scans: {row['runs']} run, {row['skipped']} skipped, "
          f"{scans['overlap']} overlapping  (manual triggers: {triggers})")
    print(f"manual scans: {manual['requested']} requested, {manual['ran']} ran, "
          f"{manual['declined']} declined while a cycle was in flight "
          f"(coalescing is expected; more RAN than REQUESTED would be a bug)")
    print(f"backtests: {bt['requested']} requested, {bt['ran']} ran, "
          f"{bt['refused']} refused by the single slot, "
          f"{bt['overlap']} overlapping")
    print(f"intelligence refreshes: {refresh['requested']} requested, "
          f"{refresh['ran']} ran")
    print(f"pause/resume cycles: {pauses}  "
          f"pause violations: {pause_violations}  "
          f"resume violations: {resume_violations}")
    print(f"stop() took {stop_took:.2f}s   abandoned: {abandoned or 'none'}   "
          f"leaked threads: {sorted(leaked) or 'none'}")
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
    if pause_violations:
        problems.append(f"{pause_violations} pause violations")
    if resume_violations:
        problems.append(f"{resume_violations} resume violations")
    if manual["ran"] == 0:
        problems.append("no manual scan ever ran - C5's path was not exercised")
    if manual["ran"] > manual["requested"]:
        problems.append(f"{manual['ran']} manual scans for "
                        f"{manual['requested']} requests")
    # V0.9.1-C6. The "ever ran" checks are the ones that matter: the C5 soak
    # passed a full run with `manual ran: 0` because the scheduled scan held
    # the lock throughout, so the path it was written to exercise was present
    # and never executed. A soak that passes without touching the feature is
    # worse than one that fails.
    if bt["overlap"]:
        problems.append(f"{bt['overlap']} overlapping backtests")
    if bt["ran"] == 0:
        problems.append("no backtest ever ran - C6's path was not exercised")
    if bt["refused"] == 0:
        problems.append("the backtest slot never refused a second request")
    if refresh["ran"] == 0:
        problems.append("no intelligence refresh ever ran")
    if refresh["ran"] > refresh["requested"]:
        problems.append(f"{refresh['ran']} refreshes for "
                        f"{refresh['requested']} requests")

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
