from __future__ import annotations

import threading
import time

import pytest

from optionspilot.services.runtime import BackgroundRuntime, TaskSpec

#: How long to wait for a property that should hold almost immediately. These
#: are not sleeps — every assertion below waits on an Event and returns the
#: instant it is satisfied, so a passing run costs milliseconds and only a
#: genuine failure spends the budget.
SETTLE = 5.0

#: Enough ticks to distinguish "the short task is running" from "one tick
#: happened to slip through". At a 0.05 s interval this is ~0.25 s of work.
TICKS_REQUIRED = 5

class TestSchedulingFairness:
    """A long task must not starve a short one. This is V0.9.1's bug statement.

    The coordinator executes every due callback *inline, serially, on its own
    thread* (`runtime._run`), so the whole scheduler is blocked for as long as
    the slowest callback runs. That is not a theoretical shape: `market_monitor`
    runs a full orchestrator scan (every watchlist symbol, an option chain each,
    over the network) and `symbol_metadata` makes one metadata call per queued
    symbol, while `tray_status` has a 10-second interval precisely because the
    tray tooltip is meant to stay current. A single scan therefore freezes the
    tray for its entire duration — and the runtime's own snapshot reports the
    task healthy throughout, because it is not *late*, it is never *considered*.

    The property asserted here is the one a user can observe: while a long task
    is in flight, a short task with a much smaller interval still ticks.

    **These were merged red at C1 under `xfail(strict=True)` and went green at
    C3.** The assertions, waits and thresholds below have never changed. The
    only edit either test has ever received is `lane="worker"` on its long
    task — the same one-argument declaration `ui/server.py` makes for
    `market_monitor`, and the reason it is needed rather than automatic is
    C2's deliberately inert default: a task that does not ask for the worker
    lane is still executed inline, and *should* be. The coordinator-lane
    behaviour these tests used to characterise is now asserted positively by
    `TestLanes::test_a_coordinator_task_still_blocks_the_scheduler`, so nothing
    stopped being covered when they flipped.

    On timing. The runtime accepts an injected clock, and the scheduling tests
    below this class use it. These two cannot: starvation is a claim about a
    blocked *thread*, and a fake clock cannot make a thread that is parked in
    `release.wait()` release the coordinator. What a fake clock removes — fixed
    sleeps and their flakiness — is instead removed by waiting on Events with a
    generous ceiling, so nothing here waits a fixed duration.
    """

    def test_a_long_task_does_not_starve_a_short_periodic_task(self):
        ticks: list[float] = []
        entered = threading.Event()
        release = threading.Event()
        enough = threading.Event()
        baseline: dict[str, int | None] = {"n": None}

        def long_scan():
            entered.set()
            release.wait(SETTLE * 2)

        def short_tick():
            ticks.append(time.monotonic())
            start = baseline["n"]
            if start is not None and len(ticks) - start >= TICKS_REQUIRED:
                enough.set()

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("long_scan", 30.0, long_scan,
                                  policy="monitoring", lane="worker"))
        runtime.register(TaskSpec("short_tick", 0.05, short_tick, policy="essential"))
        runtime.start()
        try:
            assert entered.wait(SETTLE), "the long task never started"
            # Count only ticks that occur while the long task is demonstrably
            # still inside its callback.
            baseline["n"] = len(ticks)
            ticked = enough.wait(SETTLE)
            still_running = not release.is_set()
            assert still_running, "the long task finished before the assertion"
            assert ticked, (
                f"the short task (interval 0.05s) managed "
                f"{len(ticks) - baseline['n']} of {TICKS_REQUIRED} ticks in "
                f"{SETTLE}s while a long task was in flight - the coordinator "
                f"is starved")
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_two_long_tasks_run_concurrently_rather_than_head_of_line(self):
        """Independent slow work must overlap, not queue behind whichever was first.

        `market_monitor` and `symbol_metadata` share nothing and both block on
        the network. Serialising them means the second one's interval silently
        becomes the first one's duration plus its own.
        """
        both_in_flight = threading.Event()
        release = threading.Event()
        inside: list[int] = []
        guard = threading.Lock()

        def slow():
            with guard:
                inside.append(1)
                if len(inside) == 2:
                    both_in_flight.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("slow_a", 30.0, slow,
                                  policy="monitoring", lane="worker"))
        runtime.register(TaskSpec("slow_b", 30.0, slow,
                                  policy="normal", lane="worker"))
        runtime.start()
        try:
            assert both_in_flight.wait(SETTLE), (
                "only one slow task was ever in flight - they ran head-of-line")
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)


def _settle(predicate, timeout: float = SETTLE) -> bool:
    """Wait for a property, returning the instant it holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _task_row(runtime, name: str) -> dict:
    return next(t for t in runtime.snapshot().tasks if t["name"] == name)


class TestLanes:
    """V0.9.1-C2: the mechanism, exercised only where a task opts in.

    The commit's whole safety argument is that the worker path is unreachable
    by default, so these tests pass `lane="worker"` explicitly. The class
    immediately above (C1's fairness tests) is the other half of that argument:
    it registers tasks WITHOUT a lane and is still expected to fail.
    """

    def test_the_default_lane_is_coordinator(self):
        """The rollback property, asserted at the API surface.

        If this default ever changes, reverting an activated task stops being a
        one-argument edit and becomes a revert — on the riskiest change in the
        milestone.
        """
        assert TaskSpec("t", 1.0, lambda: None).lane == "coordinator"

    def test_an_unknown_lane_is_rejected(self):
        with pytest.raises(ValueError, match="lane"):
            TaskSpec("t", 1.0, lambda: None, lane="turbo")

    def test_a_coordinator_task_still_blocks_the_scheduler(self):
        """The inert default, asserted as BEHAVIOUR rather than as a constant.

        This is C1's fairness test inverted: with no lane declared, a long task
        still starves a short one, because C2 changed nothing for tasks that
        did not ask. When C3 flips the fairness tests green, this one must stay
        green too — they are not contradictory, they are the two sides of an
        opt-in.
        """
        ticks: list[int] = []
        entered = threading.Event()
        release = threading.Event()

        def long_task():
            entered.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("long", 30.0, long_task))          # no lane
        runtime.register(TaskSpec("short", 0.01, lambda: ticks.append(1)))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            before = len(ticks)
            assert not _settle(lambda: len(ticks) > before + 3, timeout=0.5), (
                "a coordinator-lane task no longer blocks the scheduler - the "
                "default is not inert any more")
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_a_worker_task_does_not_block_the_coordinator(self):
        """The mechanism, once a task opts in. C3 makes this true in production."""
        ticks: list[int] = []
        entered = threading.Event()
        release = threading.Event()

        def long_task():
            entered.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("long", 30.0, long_task, lane="worker"))
        runtime.register(TaskSpec("short", 0.01, lambda: ticks.append(1)))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            before = len(ticks)
            assert _settle(lambda: len(ticks) >= before + TICKS_REQUIRED), (
                f"short task ticked {len(ticks) - before} times while a worker "
                f"task was in flight")
            assert not release.is_set(), "the long task finished too early"
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_a_worker_task_never_runs_concurrently_with_itself(self):
        """Overlap guard. A task slower than its own interval must not stack up.

        Skipped runs are counted rather than queued: queueing them would defeat
        the pool bound, and a task that cannot keep up should say so instead of
        accumulating a backlog that all arrives at once when it recovers.
        """
        concurrent = []
        peak = []
        release = threading.Event()
        guard = threading.Lock()

        def slow():
            with guard:
                concurrent.append(1)
                peak.append(len(concurrent))
            try:
                release.wait(SETTLE * 2)
            finally:
                with guard:
                    concurrent.pop()

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("slow", 0.01, slow, lane="worker"))
        runtime.start()
        try:
            assert _settle(lambda: bool(peak))
            assert _settle(lambda: _task_row(runtime, "slow")["skipped"] > 0), (
                "intervals elapsed while the task was in flight but none was "
                "recorded as skipped")
            assert max(peak) == 1, f"task overlapped itself (peak {max(peak)})"
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_the_pool_bound_is_respected(self):
        """`max_workers=1` must serialise two independent worker tasks."""
        concurrent = []
        peak = []
        release = threading.Event()
        guard = threading.Lock()

        def slow():
            with guard:
                concurrent.append(1)
                peak.append(len(concurrent))
            try:
                release.wait(SETTLE * 2)
            finally:
                with guard:
                    concurrent.pop()

        runtime = BackgroundRuntime(health_interval=60, max_workers=1)
        runtime.register(TaskSpec("a", 30.0, slow, lane="worker"))
        runtime.register(TaskSpec("b", 30.0, slow, lane="worker"))
        runtime.start()
        try:
            assert _settle(lambda: bool(peak))
            time.sleep(0.2)
            assert max(peak) == 1, f"pool bound of 1 admitted {max(peak)}"
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_two_worker_tasks_overlap_within_the_bound(self):
        """The default bound must be enough for the two tasks earmarked for it.

        `market_monitor` and `symbol_metadata` share nothing and both block on
        the network; serialising them would make the second one's interval its
        own plus the first one's duration.
        """
        both = threading.Event()
        release = threading.Event()
        inside = []
        guard = threading.Lock()

        def slow():
            with guard:
                inside.append(1)
                if len(inside) == 2:
                    both.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("a", 30.0, slow, lane="worker"))
        runtime.register(TaskSpec("b", 30.0, slow, lane="worker"))
        runtime.start()
        try:
            assert both.wait(SETTLE), "independent worker tasks ran head-of-line"
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_snapshot_distinguishes_queued_from_running(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60, max_workers=1)
        runtime.register(TaskSpec("first", 30.0, slow, lane="worker"))
        runtime.register(TaskSpec("second", 30.0, slow, lane="worker"))
        runtime.start()
        try:
            assert started.wait(SETTLE)
            assert _settle(lambda: any(t["running"] for t in runtime.snapshot().tasks))
            assert _settle(lambda: any(t["queued"] for t in runtime.snapshot().tasks)), (
                "with a single worker the second task must report queued, not "
                "running - the snapshot cannot tell them apart")
            rows = {t["name"]: t for t in runtime.snapshot().tasks}
            assert not (rows["first"]["queued"] and rows["first"]["running"])
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_an_exception_in_a_worker_does_not_kill_the_coordinator(self):
        ticks: list[int] = []

        def boom():
            raise RuntimeError("worker exploded")

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("boom", 0.01, boom, lane="worker",
                                  restartable=False))
        runtime.register(TaskSpec("tick", 0.01, lambda: ticks.append(1)))
        runtime.start()
        try:
            assert _settle(lambda: _task_row(runtime, "boom")["failures"] > 0)
            before = len(ticks)
            assert _settle(lambda: len(ticks) > before + 2), (
                "the coordinator stopped after a worker raised")
            assert "exploded" in (_task_row(runtime, "boom")["last_error"] or "")
        finally:
            runtime.stop(timeout=SETTLE)

    def test_stop_leaves_no_worker_thread_behind(self):
        """Bounded shutdown, and the leak the pool could introduce.

        Pool threads carry the coordinator's name prefix precisely so the
        lifecycle leak test counts them; this asserts the same property
        directly for a runtime used on its own.
        """
        def quick():
            time.sleep(0.01)

        def live_workers():
            return [t for t in threading.enumerate()
                    if t.is_alive() and t.name.startswith("background-runtime")]

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("quick", 0.01, quick, lane="worker"))
        runtime.start()
        assert _settle(lambda: _task_row(runtime, "quick")["runs"] > 0)
        assert live_workers(), "no pool thread was ever created"
        runtime.stop(timeout=SETTLE)
        assert _settle(lambda: not live_workers()), (
            f"threads survived stop(): {[t.name for t in live_workers()]}")

    def test_stop_is_bounded_even_with_a_worker_still_running(self):
        """A scan can outlast any sensible shutdown budget; exit must not."""
        release = threading.Event()
        started = threading.Event()

        def forever():
            started.set()
            release.wait(30)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("forever", 30.0, forever, lane="worker"))
        runtime.start()
        try:
            assert started.wait(SETTLE)
            began = time.monotonic()
            runtime.stop(timeout=0.5)
            assert time.monotonic() - began < 3.0, (
                "stop() waited on an in-flight worker instead of abandoning it")
        finally:
            release.set()

    def test_a_runtime_with_no_worker_task_creates_no_pool(self):
        """Inertness, measured rather than argued."""
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("plain", 0.01, lambda: None))
        runtime.start()
        try:
            assert _settle(lambda: _task_row(runtime, "plain")["runs"] > 0)
            assert runtime._pool is None, "a pool was created for no worker task"
        finally:
            runtime.stop(timeout=SETTLE)

    def test_max_workers_must_be_positive(self):
        with pytest.raises(ValueError, match="max_workers"):
            BackgroundRuntime(max_workers=0)


def test_runtime_runs_registered_task_once_and_stops_cleanly():
    calls = []
    runtime = BackgroundRuntime(health_interval=60)
    runtime.register(TaskSpec("one", 0.01, lambda: calls.append("run")))
    runtime.start()
    deadline = time.monotonic() + 1
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime.stop()
    assert calls
    assert not runtime.snapshot().running


def test_hidden_essential_reduced_runs_essential_and_monitoring_not_normal():
    calls = []
    runtime = BackgroundRuntime(health_interval=60)
    runtime.set_visibility(False)
    runtime.register(TaskSpec("essential", 0.01, lambda: calls.append("essential"),
                              policy="essential"))
    runtime.register(TaskSpec("monitoring", 0.01, lambda: calls.append("monitoring"),
                              policy="monitoring"))
    runtime.register(TaskSpec("normal", 0.01, lambda: calls.append("normal"),
                              policy="normal"))
    runtime.start()
    time.sleep(0.08)
    runtime.stop()
    assert "essential" in calls
    assert "monitoring" in calls
    assert "normal" not in calls


def test_pause_resume_is_cooperative():
    calls = []
    runtime = BackgroundRuntime(health_interval=60)
    runtime.register(TaskSpec("one", 0.01, lambda: calls.append(1)))
    runtime.start()
    time.sleep(0.04)
    runtime.pause()
    count = len(calls)
    time.sleep(0.05)
    assert len(calls) == count
    runtime.resume()
    time.sleep(0.04)
    runtime.stop()
    assert len(calls) > count


def test_duplicate_task_names_are_rejected():
    runtime = BackgroundRuntime()
    runtime.register(TaskSpec("one", 1, lambda: None))
    try:
        runtime.register(TaskSpec("one", 1, lambda: None))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate task was accepted")


def test_failed_restartable_task_gets_one_coordinator_owned_retry():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary")

    runtime = BackgroundRuntime(health_interval=60)
    runtime.register(TaskSpec("flaky", 0.5, flaky))
    runtime.start()
    deadline = time.monotonic() + 1
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime.stop()
    task = runtime.snapshot().tasks[0]
    assert len(calls) >= 2
    assert task["restart_count"] == 1
    assert task["last_success"] is not None
    assert not task["recovery_pending"]


def test_stop_is_idempotent_and_does_not_leave_runtime_thread():
    runtime = BackgroundRuntime(health_interval=60)
    runtime.start()
    runtime.stop()
    runtime.stop()
    snap = runtime.snapshot()
    assert not snap.running
    assert not snap.thread_alive


def test_trigger_uses_the_owned_scheduler_without_creating_a_worker():
    runtime = BackgroundRuntime()
    calls = []
    runtime.register(TaskSpec("metadata", 60, lambda: calls.append("run")))
    assert runtime.trigger("metadata") is True
    assert runtime.trigger("missing") is False
    runtime.start()
    try:
        deadline = time.monotonic() + 1
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == ["run"]
        assert runtime.snapshot().thread_alive
    finally:
        runtime.stop()
