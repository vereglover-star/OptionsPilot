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

#: V0.9.1-C2 adds `TaskSpec.lane`, defaulting to "coordinator" so that the new
#: dispatch path is inert until a task opts in — that inert default IS the
#: rollback property, and it must not be traded away to make these go green.
#: The consequence for these two tests is concrete: they register tasks with no
#: lane, so C2 alone will NOT flip them. C2 must add `lane="worker"` to the long
#: tasks here in the same commit that introduces the field.
_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="V0.9.1-C1 states the bug executably; the fix is C2 (lanes + a "
           "bounded worker pool) and C3 (market_monitor onto the worker lane). "
           "strict=True so this fails the suite the moment it starts passing, "
           "which is what forces C2/C3 to come back and flip it.",
)


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

    On timing. The runtime accepts an injected clock, and the scheduling tests
    below this class use it. These two cannot: starvation is a claim about a
    blocked *thread*, and a fake clock cannot make a thread that is parked in
    `release.wait()` release the coordinator. What a fake clock removes — fixed
    sleeps and their flakiness — is instead removed by waiting on Events with a
    generous ceiling, so nothing here waits a fixed duration.
    """

    @_XFAIL
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
        runtime.register(TaskSpec("long_scan", 30.0, long_scan, policy="monitoring"))
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

    @_XFAIL
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
        runtime.register(TaskSpec("slow_a", 30.0, slow, policy="monitoring"))
        runtime.register(TaskSpec("slow_b", 30.0, slow, policy="normal"))
        runtime.start()
        try:
            assert both_in_flight.wait(SETTLE), (
                "only one slow task was ever in flight - they ran head-of-line")
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)


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
