"""Whole-application lifecycle: repeated start/stop leaks no threads.

Every other runtime test drives `BackgroundRuntime` in isolation. Nothing
asserted the property the V0.8 runtime actually promises — that a *desktop
session*, which starts the scheduler, hides to tray, restores, pauses, resumes
and exits, returns the process to the thread count it began with, and that a
second `start_loop` does not produce a second scheduler.

These are cheap and they are the only automated evidence for the "no thread
leaks / no scheduler duplication" claims in the release checklist.
"""

from __future__ import annotations

import threading
import time

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.services.runtime import BackgroundRuntime, TaskSpec
from optionspilot.ui.server import UIServer
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles


def live_worker_names() -> set[str]:
    """Threads this application owns, ignoring pytest's and the interpreter's."""
    owned = ("background-runtime", "system-tray", "desktop-", "uvicorn",
             "marketdata-")
    return {t.name for t in threading.enumerate()
            if t.is_alive() and t.name.startswith(owned)}


def settle(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    cfg = CFG.model_copy(deep=True)
    orch = Orchestrator(
        cfg, provider=FakeProvider(candles, spot, NOW.date()),
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    instance = UIServer(cfg, orchestrator=orch, data_dir=tmp_path)
    yield instance
    instance.close()


class TestThreadOwnership:
    def test_a_full_session_leaves_no_worker_behind(self, server):
        """Start, hide, restore, pause, resume, exit — back to baseline."""
        baseline = live_worker_names()
        server.start_loop()
        assert settle(lambda: "background-runtime" in live_worker_names())

        server.set_background_visibility(False)     # hidden to tray
        server.set_background_visibility(True)      # restored
        server.pause_background()
        server.resume_background()

        server.close()
        assert settle(lambda: live_worker_names() == baseline), (
            f"leaked {live_worker_names() - baseline}")

    def test_repeated_sessions_do_not_accumulate_schedulers(self, tmp_path,
                                                            monkeypatch, server):
        """A user who restarts the loop must not get a second coordinator.

        Two schedulers over one task set would run every cycle twice — and
        `market_monitor` places trades.
        """
        baseline = live_worker_names()
        for _ in range(5):
            server.start_loop()
            assert settle(lambda: server.background.snapshot().running)
            running = [n for n in live_worker_names()
                       if n == "background-runtime"]
            assert len(running) == 1
            server.stop_loop()
            assert settle(lambda: not server.background.snapshot().running)
        assert settle(lambda: live_worker_names() == baseline)

    def test_starting_twice_registers_each_task_once(self, server):
        """`start_loop` is idempotent about its task set, not just its thread."""
        server.start_loop()
        server.stop_loop()
        server.start_loop()
        names = [task["name"] for task in server.background.snapshot().tasks]
        assert sorted(names) == sorted(set(names)), f"duplicated: {names}"
        assert "market_monitor" in names

    def test_close_is_idempotent_and_never_raises(self, server):
        server.start_loop()
        server.close()
        server.close()
        assert not server.background.snapshot().running


class TestScanDoesNotFreezeTheApplication:
    """V0.9.1-C3, against the real `UIServer` and its real registrations.

    `tests/test_runtime.py` proves the runtime CAN run a long task off the
    coordinator. This proves the application actually ASKS it to — which is a
    different claim, and the one that was false. `RiskManager.approve_manual_entry`
    is this codebase's standing example of a mechanism that worked and a call
    site that never used it.

    The symptom being eliminated: `market_monitor` runs a full watchlist fetch
    plus an option chain per symbol over the network, and executing that inline
    froze every other task for its duration. `tray_status` carries a 10-second
    interval precisely because the tooltip is meant to stay current, so it was
    the most visible casualty — the tray went stale exactly when a user looked
    at it, while the runtime's own snapshot reported it healthy throughout.
    """

    @pytest.fixture
    def blocking_scan(self, server, monkeypatch):
        """Make the REAL `market_monitor` callback block until released.

        `_background_cycle` and the registration are untouched: the scan itself
        is what blocks, so the task under test is the one production registers,
        with the lane production gives it.
        """
        entered = threading.Event()
        release = threading.Event()

        def slow_cycle():
            entered.set()
            release.wait(20)
            return {}

        monkeypatch.setattr(server.orch, "market_open", lambda now: True)
        monkeypatch.setattr(server, "run_cycle_now", slow_cycle)
        monkeypatch.setattr(server.orch, "_maybe_send_summaries", lambda now: None)
        yield server, entered, release
        release.set()

    def test_market_monitor_is_registered_on_the_worker_lane(self, server):
        """The one-argument activation, asserted where it is actually made.

        Deleting `lane="worker"` from `ui/server.py` restores the previous
        behaviour with no other change — that is the rollback property, and
        this test is what notices if it is removed by accident rather than
        by decision.
        """
        server.start_loop()
        rows = {t["name"]: t for t in server.background.snapshot().tasks}
        assert rows["market_monitor"]["lane"] == "worker"
        # Everything else stays inline. C3 moves one task, not all of them.
        assert rows["symbol_metadata"]["lane"] == "coordinator"

    def test_coordinator_work_continues_while_a_scan_is_running(self, blocking_scan):
        """The user-visible acceptance criterion, at the tray's own interval."""
        server, entered, release = blocking_scan
        beats: list[float] = []
        server.background.register(
            TaskSpec("tray_status_probe", 0.05,
                     lambda: beats.append(time.monotonic()), policy="essential"))
        server.start_loop()

        assert settle(lambda: entered.is_set()), "the scan never started"
        before = len(beats)
        assert settle(lambda: len(beats) >= before + 5), (
            f"a coordinator task ticked {len(beats) - before} times while a "
            f"scan was in flight - the scan is still starving it")
        assert not release.is_set(), "the scan finished before the assertion"

        rows = {t["name"]: t for t in server.background.snapshot().tasks}
        assert rows["market_monitor"]["running"] is True
        assert rows["market_monitor"]["queued"] is False

    def test_a_scan_never_overlaps_itself(self, blocking_scan):
        """Overlap protection survives the move to a real workload.

        A scan slower than `scan_interval_seconds` must be skipped, not queued:
        two concurrent cycles would place trades twice.
        """
        server, entered, release = blocking_scan
        server.background.register(
            TaskSpec("nudge", 0.02, lambda: None, policy="essential"))
        server.start_loop()
        assert settle(lambda: entered.is_set())
        # Force the scan due repeatedly while it is still in flight.
        for _ in range(20):
            server.background.trigger("market_monitor")
            time.sleep(0.01)
        row = next(t for t in server.background.snapshot().tasks
                   if t["name"] == "market_monitor")
        assert row["running"] is True
        assert row["runs"] == 0, "a second cycle started while the first was in flight"
        assert row["skipped"] > 0, "re-entry was refused but never recorded"

    def test_shutdown_stays_clean_with_a_scan_in_flight(self, blocking_scan):
        """Bounded exit, and no thread left behind once the scan lets go."""
        server, entered, release = blocking_scan
        baseline = live_worker_names()
        server.start_loop()
        assert settle(lambda: entered.is_set())

        started = time.monotonic()
        server.close()
        elapsed = time.monotonic() - started
        assert elapsed < 8.0, f"close() blocked for {elapsed:.2f}s behind a scan"
        assert not server.background.snapshot().running

        # The abandoned worker is a daemon; once its work returns it must go.
        release.set()
        assert settle(lambda: live_worker_names() == baseline, timeout=10), (
            f"leaked {live_worker_names() - baseline}")


class TestSchedulerShutdown:
    def test_stop_returns_even_when_a_task_is_still_working(self):
        """Shutdown is bounded. A wedged callback must not hold the process.

        This is what lets the desktop close path promise a bounded exit: the
        join has a timeout, the thread is a daemon, and `stop` returns.
        """
        runtime = BackgroundRuntime(health_interval=60)
        entered = threading.Event()
        release = threading.Event()

        def wedged():
            entered.set()
            release.wait(30)

        runtime.register(TaskSpec("wedged", 0.05, wedged))
        runtime.start()
        try:
            assert entered.wait(5)
            started = time.monotonic()
            runtime.stop(timeout=0.5)
            elapsed = time.monotonic() - started
            assert elapsed < 3.0, f"stop blocked for {elapsed:.2f}s"
        finally:
            release.set()
            runtime.stop(timeout=5)

    def test_a_task_that_always_fails_does_not_become_a_hot_loop(self):
        """One coordinator-owned retry, then back to the normal interval."""
        runs = []
        runtime = BackgroundRuntime(health_interval=60)

        def always_fails():
            runs.append(time.monotonic())
            raise RuntimeError("nope")

        runtime.register(TaskSpec("failing", 60.0, always_fails))
        runtime.start()
        try:
            assert settle(lambda: len(runs) >= 2)
            time.sleep(0.5)
            assert len(runs) == 2, f"retried {len(runs)} times in half a second"
            task = runtime.snapshot().tasks[0]
            assert task["failures"] == 2
            assert task["last_error"] == "nope"
        finally:
            runtime.stop()
