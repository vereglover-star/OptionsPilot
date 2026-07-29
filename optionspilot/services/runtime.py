"""Platform-neutral background task coordination.

The runtime owns one coordinator thread for application background work. Tasks
are callbacks with metadata, not independent timers or threads. This keeps
pause/resume, shutdown, health reporting, and future non-desktop hosts honest.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


PROFILES = ("essential_reduced", "normal", "monitoring_only")
POLICIES = ("essential", "normal", "monitoring")


class BackgroundTask(Protocol):
    name: str
    interval: float
    policy: str

    def run(self) -> None: ...


@dataclass(slots=True)
class TaskSpec:
    name: str
    interval: float
    callback: Callable[[], None]
    policy: str = "essential"
    owner: str = "runtime"
    restartable: bool = True
    next_due: float = 0.0
    heartbeat: float | None = None
    last_started: float | None = None
    last_success: float | None = None
    last_failure: float | None = None
    runs: int = 0
    failures: int = 0
    restart_count: int = 0
    recovery_pending: bool = False
    last_error: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("background task name cannot be empty")
        if self.interval <= 0:
            raise ValueError("background task interval must be positive")
        if self.policy not in POLICIES:
            raise ValueError(f"unknown background task policy: {self.policy!r}")
        if not self.owner.strip():
            raise ValueError("background task owner cannot be empty")


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    running: bool
    paused: bool
    visible: bool
    profile: str
    thread_alive: bool
    started_at: float | None
    health_checks: int
    tasks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "paused": self.paused,
            "visible": self.visible,
            "profile": self.profile,
            "thread_alive": self.thread_alive,
            "started_at": self.started_at,
            "health_checks": self.health_checks,
            "tasks": list(self.tasks),
        }


class BackgroundRuntime:
    """One lifecycle owner for lightweight application work.

    The scheduler deliberately uses ``Event.wait`` rather than ``sleep`` so a
    shutdown or profile change wakes it immediately. Callbacks must be
    short/non-blocking or manage their own bounded work; they are never run
    while the runtime lock is held.
    """

    def __init__(self, *, health_check: Callable[[RuntimeSnapshot], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 health_interval: float = 6 * 60 * 60):
        if health_interval <= 0:
            raise ValueError("health_interval must be positive")
        self._clock = clock
        self._health_check = health_check
        self._health_interval = health_interval
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tasks: dict[str, TaskSpec] = {}
        self._profile = "essential_reduced"
        self._paused = False
        self._visible = True
        self._started_at: float | None = None
        self._next_health = 0.0
        self._health_checks = 0

    def register(self, task: TaskSpec) -> None:
        with self._lock:
            if task.name in self._tasks:
                raise ValueError(f"background task already registered: {task.name}")
            now = self._clock()
            task.next_due = now
            self._tasks[task.name] = task
            self._wake.set()

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tasks.pop(name, None)
            self._wake.set()

    def trigger(self, name: str) -> bool:
        """Make a registered task eligible on the next coordinator pass.

        Producers use this instead of creating their own timer or thread.  A
        missing task is normal during host construction, so callers can queue
        work before the runtime lifecycle begins.
        """
        with self._lock:
            task = self._tasks.get(name)
            if task is None:
                return False
            task.next_due = min(task.next_due, self._clock())
            self._wake.set()
            return True

    def set_profile(self, profile: str) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown background profile: {profile!r}")
        with self._lock:
            self._profile = profile
            self._wake.set()

    def set_visibility(self, visible: bool) -> None:
        with self._lock:
            self._visible = bool(visible)
            self._wake.set()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            now = self._clock()
            self._stop.clear()
            self._started_at = self._started_at or now
            self._next_health = now + self._health_interval
            self._thread = threading.Thread(
                target=self._run, name="background-runtime", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._wake.set()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            now = self._clock()
            for task in self._tasks.values():
                task.next_due = min(task.next_due, now)
            self._wake.set()

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            thread = self._thread
            tasks = [
                {
                    "name": task.name,
                    "interval": task.interval,
                    "policy": task.policy,
                    "owner": task.owner,
                    "restartable": task.restartable,
                    "heartbeat": task.heartbeat,
                    "last_started": task.last_started,
                    "last_success": task.last_success,
                    "last_failure": task.last_failure,
                    "next_due": task.next_due,
                    "runs": task.runs,
                    "failures": task.failures,
                    "restart_count": task.restart_count,
                    "recovery_pending": task.recovery_pending,
                    "last_error": task.last_error,
                }
                for task in self._tasks.values()
            ]
            return RuntimeSnapshot(
                running=bool(thread and thread.is_alive()),
                paused=self._paused,
                visible=self._visible,
                profile=self._profile,
                thread_alive=bool(thread and thread.is_alive()),
                started_at=self._started_at,
                health_checks=self._health_checks,
                tasks=tasks,
            )

    def _eligible(self, task: TaskSpec) -> bool:
        if self._visible:
            return True
        if self._profile == "normal":
            return True
        if self._profile == "monitoring_only":
            return task.policy == "monitoring"
        return task.policy in ("essential", "monitoring")

    def _run(self) -> None:
        while not self._stop.is_set():
            now = self._clock()
            due: list[TaskSpec] = []
            with self._lock:
                paused = self._paused
                for task in self._tasks.values():
                    if not paused and self._eligible(task) and now >= task.next_due:
                        due.append(task)
                        # Schedule from now, not the old deadline, so a slow
                        # callback cannot accumulate a backlog of executions.
                        task.next_due = now + task.interval
                health_due = not paused and self._health_check and now >= self._next_health
                if health_due:
                    self._next_health = now + self._health_interval
                    self._health_checks += 1
            for task in due:
                try:
                    with self._lock:
                        task.last_started = self._clock()
                    task.callback()
                    with self._lock:
                        task.runs += 1
                        task.heartbeat = self._clock()
                        task.last_success = task.heartbeat
                        task.last_error = None
                        task.recovery_pending = False
                except Exception as exc:  # noqa: BLE001 - task isolation
                    with self._lock:
                        task.failures += 1
                        task.last_failure = self._clock()
                        task.last_error = str(exc)
                        # A task callback has no independently-owned worker to
                        # restart.  Recovery therefore means one immediate,
                        # coordinator-owned retry.  A second failure remains
                        # visible to the health monitor instead of producing an
                        # unbounded hot loop.
                        if task.restartable and not task.recovery_pending:
                            task.recovery_pending = True
                            task.restart_count += 1
                            task.next_due = task.last_failure
                            self._wake.set()
            if health_due:
                try:
                    self._health_check(self.snapshot())
                except Exception:
                    # Health reporting must never kill the coordinator.
                    pass
            self._wake.wait(self._wait_time())
            self._wake.clear()

    def _wait_time(self) -> float:
        with self._lock:
            if self._paused:
                return 1.0
            now = self._clock()
            deadlines = [self._next_health]
            deadlines.extend(
                task.next_due for task in self._tasks.values()
                if self._eligible(task)
            )
        return max(0.01, min(1.0, max(0.0, min(deadlines) - now)))
