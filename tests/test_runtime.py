from __future__ import annotations

import time

from optionspilot.services.runtime import BackgroundRuntime, TaskSpec


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
