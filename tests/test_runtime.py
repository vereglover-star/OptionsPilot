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


class TestOnDemandTasks:
    """V0.9.1-C5: a task that runs only when something asks for it.

    Without this the runtime cannot own user-initiated work at all, because
    `register` deliberately makes every task immediately due — so registering a
    "manual scan" task would run a scan, on every server construction. The
    three ways a task becomes due are registration, its interval, and
    `trigger`; an on-demand task answers only to the third.
    """

    def test_registering_an_on_demand_task_does_not_run_it(self):
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("manual", 0.01, lambda: runs.append(1),
                                  on_demand=True))
        runtime.start()
        try:
            time.sleep(0.3)      # many intervals; none of them apply
            assert runs == [], "an on-demand task ran without being triggered"
        finally:
            runtime.stop(timeout=SETTLE)

    def test_trigger_runs_an_on_demand_task_exactly_once(self):
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("manual", 0.01, lambda: runs.append(1),
                                  on_demand=True))
        runtime.start()
        try:
            runtime.trigger("manual")
            assert _settle(lambda: len(runs) == 1)
            time.sleep(0.3)      # the interval must not re-arm it
            assert len(runs) == 1, f"ran {len(runs)} times for one trigger"
        finally:
            runtime.stop(timeout=SETTLE)

    def test_resume_does_not_fire_an_on_demand_task(self):
        """Resume pulls every waiting task forward. An on-demand task was not
        waiting, so catching it up would be a scan nobody asked for."""
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("manual", 0.01, lambda: runs.append(1),
                                  on_demand=True))
        runtime.register(TaskSpec("normal", 0.01, lambda: None))
        runtime.start()
        try:
            runtime.pause()
            time.sleep(0.05)
            runtime.resume()
            time.sleep(0.3)
            assert runs == [], "resume fired an on-demand task"
        finally:
            runtime.stop(timeout=SETTLE)

    def test_an_ordinary_task_is_unaffected(self):
        """The default must stay exactly what it was."""
        assert TaskSpec("t", 1.0, lambda: None).on_demand is False
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("normal", 0.01, lambda: runs.append(1)))
        runtime.start()
        try:
            assert _settle(lambda: len(runs) > 2)
        finally:
            runtime.stop(timeout=SETTLE)

    def test_an_on_demand_deadline_survives_json(self):
        """`inf` is truthful internally and invalid on the wire.

        The snapshot reaches `/api/runtime` through `json.dumps`, which emits a
        bare `Infinity` that no conforming parser accepts — the failure this
        codebase already recorded once for `intelligence/models._finite`. It
        was reintroduced here and caught by `test_ui_server.py`.
        """
        import json

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("manual", 3600.0, lambda: None,
                                  on_demand=True))
        row = runtime.snapshot().tasks[0]
        assert row["next_due"] is None, "an unscheduled task must not claim a time"
        json.dumps(runtime.snapshot().to_dict())      # must not raise

    def test_an_on_demand_worker_task_is_triggerable_repeatedly(self):
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("manual", 3600.0, lambda: runs.append(1),
                                  lane="worker", on_demand=True))
        runtime.start()
        try:
            for expected in (1, 2, 3):
                runtime.trigger("manual")
                assert _settle(lambda n=expected: len(runs) == n), (
                    f"trigger {expected} did not run: {len(runs)}")
        finally:
            runtime.stop(timeout=SETTLE)


class TestPauseAndStopSemantics:
    """V0.9.1-C4: Decision D-2 made executable.

    Pause **prevents dispatch** and lets in-flight work finish. It does not
    interrupt a running worker task, because `market_monitor` runs a stateful
    cycle that places trades — tearing that down mid-flight risks broker state
    that agrees with nothing. The consequence is that pause is *not
    instantaneous*, and something has to say so: a user who clicks Pause and
    watches a scan keep running needs to see "finishing current work", not a
    UI that claims it already stopped.

    Stop stays **bounded**. A scan can outlast any sensible shutdown budget and
    the shell will ghost an unresponsive window regardless, so anything still
    running when the budget expires is abandoned on a daemon thread — but it is
    abandoned *by name*, in the log, because a shutdown that silently drops
    work is indistinguishable from one that completed.
    """

    def test_pause_prevents_new_worker_dispatch(self):
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("w", 0.01, lambda: runs.append(1), lane="worker"))
        runtime.start()
        try:
            assert _settle(lambda: len(runs) > 0)
            runtime.pause()
            settled = len(runs)
            time.sleep(0.2)
            assert len(runs) == settled, (
                f"{len(runs) - settled} worker runs dispatched while paused")
        finally:
            runtime.stop(timeout=SETTLE)

    def test_pause_lets_an_in_flight_worker_task_finish(self):
        """D-2's core claim. Interrupting a stateful cycle is the unsafe option."""
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def slow():
            entered.set()
            release.wait(SETTLE * 2)
            completed.set()

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("slow", 30.0, slow, lane="worker"))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            runtime.pause()
            assert not completed.is_set(), "pause interrupted a running task"
            release.set()
            assert completed.wait(SETTLE), "pause prevented an in-flight task finishing"
            assert _settle(lambda: not _task_row(runtime, "slow")["running"])
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_the_snapshot_says_when_a_pause_is_not_yet_complete(self):
        """Pause is not instantaneous, so the state has to be reportable.

        Without this a UI can only show "paused" the instant the request is
        made, which is a claim about intent presented as a claim about fact.
        """
        entered = threading.Event()
        release = threading.Event()

        def slow():
            entered.set()
            release.wait(SETTLE * 2)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("slow", 30.0, slow, lane="worker"))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            assert runtime.snapshot().pause_pending is False, (
                "nothing is pausing yet")
            runtime.pause()
            snap = runtime.snapshot()
            assert snap.paused is True
            assert snap.pause_pending is True, (
                "paused with a task still running, but the snapshot reports "
                "the pause as complete")
            release.set()
            assert _settle(lambda: runtime.snapshot().pause_pending is False), (
                "the pause never settled once the task finished")
            assert runtime.snapshot().paused is True
        finally:
            release.set()
            runtime.stop(timeout=SETTLE)

    def test_pause_pending_is_false_for_a_coordinator_task(self):
        """The coordinator runs inline, so a pause observed from outside is
        always already complete for that lane."""
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("c", 0.01, lambda: None))
        runtime.start()
        try:
            assert _settle(lambda: _task_row(runtime, "c")["runs"] > 0)
            runtime.pause()
            assert runtime.snapshot().pause_pending is False
        finally:
            runtime.stop(timeout=SETTLE)

    def test_resume_after_a_pause_dispatches_again(self):
        runs: list[int] = []
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("w", 0.01, lambda: runs.append(1), lane="worker"))
        runtime.start()
        try:
            assert _settle(lambda: len(runs) > 0)
            runtime.pause()
            time.sleep(0.1)
            settled = len(runs)
            runtime.resume()
            assert _settle(lambda: len(runs) > settled)
        finally:
            runtime.stop(timeout=SETTLE)

    def test_stop_drains_a_worker_that_finishes_inside_the_budget(self):
        finished = threading.Event()

        def quick():
            time.sleep(0.05)
            finished.set()

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("quick", 30.0, quick, lane="worker"))
        runtime.start()
        assert _settle(lambda: bool(_task_row(runtime, "quick")["running"]) or
                       finished.is_set())
        runtime.stop(timeout=SETTLE)
        assert finished.is_set(), "stop() did not let a quick task drain"
        assert not runtime.snapshot().tasks[0]["running"]

    def test_stop_logs_an_abandoned_task_by_name(self, caplog):
        """A shutdown that silently drops work looks exactly like a clean one.

        The name matters: "a task was abandoned" is not actionable, and the
        next person debugging a half-finished cycle needs to know which.
        """
        entered = threading.Event()
        release = threading.Event()

        def wedged():
            entered.set()
            release.wait(20)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("wedged_scan", 30.0, wedged, lane="worker"))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            with caplog.at_level("WARNING", logger="optionspilot.services"):
                runtime.stop(timeout=0.2)
            assert "wedged_scan" in caplog.text, (
                f"abandonment was not logged by name: {caplog.text!r}")
            assert "abandon" in caplog.text.lower()
        finally:
            release.set()

    def test_a_clean_stop_logs_no_abandonment(self, caplog):
        """The other direction: a quiet shutdown must stay quiet."""
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("quick", 30.0, lambda: None, lane="worker"))
        runtime.start()
        assert _settle(lambda: _task_row(runtime, "quick")["runs"] > 0)
        with caplog.at_level("WARNING", logger="optionspilot.services"):
            runtime.stop(timeout=SETTLE)
        assert "abandon" not in caplog.text.lower(), (
            f"a clean shutdown reported abandonment: {caplog.text!r}")

    def test_stop_stays_bounded_and_reports_what_it_left(self):
        release = threading.Event()
        entered = threading.Event()

        def wedged():
            entered.set()
            release.wait(20)

        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("wedged", 30.0, wedged, lane="worker"))
        runtime.start()
        try:
            assert entered.wait(SETTLE)
            began = time.monotonic()
            abandoned = runtime.stop(timeout=0.3)
            elapsed = time.monotonic() - began
            assert elapsed < 3.0, f"stop() blocked for {elapsed:.2f}s"
            assert abandoned == ["wedged"], (
                f"stop() must return what it abandoned, got {abandoned!r}")
        finally:
            release.set()

    def test_stop_returns_an_empty_list_when_nothing_was_abandoned(self):
        runtime = BackgroundRuntime(health_interval=60)
        runtime.register(TaskSpec("c", 0.01, lambda: None))
        runtime.start()
        assert _settle(lambda: _task_row(runtime, "c")["runs"] > 0)
        assert runtime.stop(timeout=SETTLE) == []


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
