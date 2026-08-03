"""Platform-neutral background task coordination.

The runtime owns one coordinator thread for application background work. Tasks
are callbacks with metadata, not independent timers or threads. This keeps
pause/resume, shutdown, health reporting, and future non-desktop hosts honest.

Lanes (V0.9.1-C2)
-----------------
A task declares which *lane* it runs in:

``coordinator``
    Executed inline on the coordinator thread, in registration order, exactly
    as every task was before lanes existed. Cheap, ordered, predictable — and
    the right home for anything that returns in milliseconds.

``worker``
    Dispatched to a small bounded pool. The coordinator hands the task over and
    moves on, so a callback that blocks for a minute no longer holds up every
    other task's schedule.

**The default is ``coordinator``, deliberately.** Until a task opts in, the
worker path is never entered and no pool thread is ever created, so this
module's observable behaviour is unchanged. That inertness is the rollback
story for the riskiest change in V0.9.1: reverting an activated task is
removing one ``lane=`` argument, not reverting a commit. Do not change the
default to make a test go green — a test that needs the worker lane should say
so.

A worker task never runs concurrently with *itself*: if it is still in flight
when its next interval comes round, the run is skipped and counted
(``skipped``), rather than queued behind it. Different worker tasks do overlap
with each other, up to the pool bound — `market_monitor` and `symbol_metadata`
share nothing and both block on the network, so serialising them would silently
turn the second one's interval into the first one's duration plus its own.

Pause, resume and stop (V0.9.1-C4, Decision D-2)
------------------------------------------------
``pause()`` **prevents dispatch and does not interrupt anything.** A worker
task already inside its callback runs to completion. This is a deliberate
choice rather than a limitation: `market_monitor` runs a stateful cycle that
places trades, and tearing that down mid-flight would leave broker state
agreeing with nothing. Nothing new is dispatched in either lane while paused.

The consequence is that **pause is not instantaneous**, so the state has to be
reportable rather than assumed. :attr:`RuntimeSnapshot.pause_pending` is true
while the runtime is paused *and* a worker task is still finishing. A client
that shows "paused" the moment the request returns is describing its own
intent, not the system — the honest label while ``pause_pending`` holds is
something like "finishing current work".

``resume()`` makes every task due immediately, so work restarts promptly rather
than waiting out the remainder of an interval it slept through.

``stop(timeout)`` is **bounded, and says what it dropped.** It stops the
coordinator, cancels worker runs that have not started, and waits for in-flight
ones with whatever remains of the caller's budget. Anything still running when
that budget expires is *abandoned*: left on a daemon thread so exit is not
delayed. Abandonment is logged at WARNING **by name** and returned to the
caller, because a shutdown that silently drops a half-finished cycle is
indistinguishable from one that completed — and the next person debugging that
cycle needs to know which task it was.
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from typing import Callable, Protocol

from optionspilot.core.logging_setup import get_logger

log = get_logger("services")

PROFILES = ("essential_reduced", "normal", "monitoring_only")
POLICIES = ("essential", "normal", "monitoring")
LANES = ("coordinator", "worker")

#: Pool bound. Small on purpose: this is a scheduler for periodic housekeeping,
#: not a job runner, and an unbounded pool would let a slow provider convert a
#: scheduling problem into a thread-count problem. Two is the minimum that lets
#: the two independent periodic tasks earmarked for the worker lane overlap;
#: raise it deliberately when more tasks move, not pre-emptively.
DEFAULT_MAX_WORKERS = 2

#: Pool threads carry the coordinator's prefix so that
#: `tests/test_runtime_lifecycle.py::live_worker_names` — which matches on
#: `background-runtime` — counts them as application threads. A pool named
#: anything else would leak invisibly past the one test written to catch leaks.
_WORKER_THREAD_PREFIX = "background-runtime-worker"


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
    #: Which lane executes this task — see the module docstring. Defaults to
    #: `coordinator`, which is the pre-lanes behaviour; the worker path stays
    #: unreachable until something opts in.
    lane: str = "coordinator"
    #: Runs ONLY when :meth:`BackgroundRuntime.trigger` makes it due — never on
    #: registration, never on resume, never on its interval. This is what lets
    #: user-initiated work (a manual scan) be a runtime task rather than a raw
    #: thread: without it, registering the task would run it, because
    #: `register` makes every task immediately due by design.
    on_demand: bool = False
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
    #: Handed to the pool but not yet started. Distinct from `running` because
    #: "waiting for a worker" and "occupying one" are different answers to
    #: "what is this task doing", and only one of them is a reason to worry.
    queued: bool = False
    running: bool = False
    #: Intervals that came round while the previous run was still in flight.
    #: Counted rather than queued: a task that cannot keep up should say so,
    #: not accumulate a backlog that arrives all at once when it recovers.
    skipped: int = 0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("background task name cannot be empty")
        if self.interval <= 0:
            raise ValueError("background task interval must be positive")
        if self.policy not in POLICIES:
            raise ValueError(f"unknown background task policy: {self.policy!r}")
        if self.lane not in LANES:
            raise ValueError(f"unknown background task lane: {self.lane!r}")
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
    #: Paused, but a worker task is still finishing. Pause never interrupts
    #: (Decision D-2), so "paused" alone describes the request rather than the
    #: system — a client that wants to be truthful shows "finishing current
    #: work" while this holds. False when idle, and always False for the
    #: coordinator lane, which cannot be observed mid-callback from outside.
    pause_pending: bool = False

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "paused": self.paused,
            "pause_pending": self.pause_pending,
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
                 health_interval: float = 6 * 60 * 60,
                 max_workers: int = DEFAULT_MAX_WORKERS):
        if health_interval <= 0:
            raise ValueError("health_interval must be positive")
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._clock = clock
        self._health_check = health_check
        self._health_interval = health_interval
        self._max_workers = max_workers
        # Created on first worker dispatch, not here: ThreadPoolExecutor spawns
        # threads lazily, and with the default lane nothing ever dispatches, so
        # an application that opts no task in pays literally nothing.
        self._pool: ThreadPoolExecutor | None = None
        self._inflight: set[Future] = set()
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
            task.next_due = math.inf if task.on_demand else now
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

    def stop(self, timeout: float = 5.0) -> list[str]:
        """Shut down both lanes within ``timeout``. Returns abandoned task names.

        Bounded by construction — see the module docstring. The return value is
        the caller's half of the abandonment contract: a host that wants to
        report "a scan was interrupted by shutdown" can, without parsing a log.
        """
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._wake.set()
        deadline = self._clock() + max(0.0, timeout)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        # The coordinator is down; drain the pool with whatever budget is left
        # so the total cost of stop() stays inside the caller's timeout rather
        # than becoming the coordinator's join PLUS the pool's.
        abandoned = self._shutdown_pool(max(0.0, deadline - self._clock()))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
        return abandoned

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._wake.set()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            now = self._clock()
            for task in self._tasks.values():
                # An on-demand task was not waiting for its interval, so there
                # is nothing for resume to catch up. Pulling it forward here
                # would turn every resume into a scan nobody asked for.
                if not task.on_demand:
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
                    "lane": task.lane,
                    # "waiting for a worker" and "occupying one" answer
                    # different questions; only the second explains a delay.
                    "queued": task.queued,
                    "running": task.running,
                    "skipped": task.skipped,
                    "owner": task.owner,
                    "restartable": task.restartable,
                    "heartbeat": task.heartbeat,
                    "last_started": task.last_started,
                    "last_success": task.last_success,
                    "last_failure": task.last_failure,
                    # An on-demand task's deadline is `inf`, which is a truthful
                    # internal value and NOT valid JSON — `json.dumps` emits a
                    # bare `Infinity` and a browser's parse dies on it. `None`
                    # is the honest wire form: there is no scheduled time.
                    # (Same boundary rule as `intelligence/models._finite`.)
                    "next_due": (task.next_due if math.isfinite(task.next_due)
                                 else None),
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
                pause_pending=self._paused and any(
                    task.running for task in self._tasks.values()),
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
                        # An on-demand task goes back to sleep instead: it runs
                        # once per trigger, not once per interval thereafter.
                        task.next_due = (math.inf if task.on_demand
                                         else now + task.interval)
                health_due = not paused and self._health_check and now >= self._next_health
                if health_due:
                    self._next_health = now + self._health_interval
                    self._health_checks += 1
            for task in due:
                if task.lane == "worker":
                    self._dispatch(task)
                else:
                    self._execute(task)
            if health_due:
                try:
                    self._health_check(self.snapshot())
                except Exception:
                    # Health reporting must never kill the coordinator.
                    pass
            self._wake.wait(self._wait_time())
            self._wake.clear()

    def _execute(self, task: TaskSpec) -> None:
        """Run one task's callback and record what happened.

        Shared verbatim by both lanes. The bookkeeping is the same fact however
        the task got here, and two copies of it would drift — the coordinator's
        counters and a worker's counters disagreeing is precisely the kind of
        thing that makes a health report untrustworthy.

        Never raises: a failing task must not take down the coordinator, and on
        a worker thread an escape would vanish into the pool's Future.
        """
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

    def _dispatch(self, task: TaskSpec) -> None:
        """Hand a worker-lane task to the pool without blocking the coordinator.

        The overlap guard lives here rather than in the worker body so that a
        task still in flight is never even submitted — queueing it would defeat
        the bound, since a task slower than its own interval would enqueue
        forever.
        """
        with self._lock:
            if task.queued or task.running:
                task.skipped += 1
                return
            # Pause and stop are enforced at the due-collection step above, so
            # neither is reachable here today. Checked anyway: the next caller
            # of _dispatch will be a manual trigger or a lane conversion (C5,
            # C6), and a guarantee that only holds because of where it happens
            # to be called from is one the second caller silently breaks.
            if self._stop.is_set() or self._paused:
                return
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix=_WORKER_THREAD_PREFIX)
            task.queued = True
            pool = self._pool
        try:
            future = pool.submit(self._run_worker, task)
        except RuntimeError:
            # The pool was shut down between the check above and here (stop()
            # racing a dispatch). Leaving `queued` set would make the task
            # permanently ineligible on the next start.
            with self._lock:
                task.queued = False
            return
        with self._lock:
            self._inflight.add(future)
        future.add_done_callback(self._retire)

    def _retire(self, future: Future) -> None:
        with self._lock:
            self._inflight.discard(future)

    def _run_worker(self, task: TaskSpec) -> None:
        with self._lock:
            task.queued = False
            task.running = True
        try:
            self._execute(task)
        finally:
            with self._lock:
                task.running = False

    def _shutdown_pool(self, timeout: float) -> list[str]:
        """Cancel queued worker runs and wait, bounded, for in-flight ones.

        Bounded is the requirement: an in-flight scan can outlast any sensible
        shutdown budget, and a shell that ghosts an unresponsive window does
        not care why. Anything still running when the budget expires is left on
        a daemon thread rather than delaying exit — and named, here, because a
        shutdown that silently drops a half-finished cycle looks exactly like
        one that completed.

        Returns the names of tasks abandoned this way.
        """
        with self._lock:
            pool, self._pool = self._pool, None
            pending = set(self._inflight)
            for task in self._tasks.values():
                task.queued = False
        if pool is None:
            return []
        pool.shutdown(wait=False, cancel_futures=True)
        if pending and timeout > 0:
            futures_wait(pending, timeout=timeout)
        # Read the task flags rather than the futures: `running` is what the
        # worker body actually maintains, so a future still pending because it
        # was cancelled before starting is correctly NOT reported as abandoned.
        with self._lock:
            abandoned = sorted(task.name for task in self._tasks.values()
                               if task.running)
        if abandoned:
            log.warning(
                "background runtime shutdown abandoned %d in-flight task(s) "
                "after %.1fs: %s — they are daemon threads and will finish "
                "without blocking exit",
                len(abandoned), max(0.0, timeout), ", ".join(abandoned))
        return abandoned

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
