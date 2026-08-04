"""V0.9.2-C5 — the backtest job slot, moved out of the transport.

The last of the four extractions. What moves is the *slot* — the atomic claim,
the parameter hand-off, the report writing and the user-visible `backtest_job`
record — while task **registration** stays in `ui/server.py`, exactly where
V0.9.1 put it.

Two things this file pins that are easy to lose in a move:

**The claim is atomic.** `_bt_lock` guards the slot test, the parameter stash
and the job record as one fact. A slot claimed with nobody's parameters behind
it would run the previous request's symbol; a slot claimed and then abandoned
would wedge every later backtest for the life of the process. Both are asserted.

**`_run_backtest` stays an overridable seam on the server.** `tests/
test_runtime_lifecycle.py` monkeypatches it to block a worker deterministically,
and that file must keep passing unchanged — so the runtime task body calls
`self._run_backtest`, which delegates, rather than reaching into the service and
bypassing the patch.

Every test here was written and passing against `ui/server.py` before
`services/backtest.py` existed, except the two that name the new module.
"""

from __future__ import annotations

import pathlib

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles

BACKTEST_PY = (pathlib.Path(__file__).resolve().parent.parent
               / "optionspilot" / "services" / "backtest.py")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    from optionspilot.ui.server import UIServer

    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    cfg = CFG.model_copy(deep=True)
    orch = Orchestrator(
        cfg, provider=FakeProvider(candles, spot, NOW.date()),
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    srv = UIServer(cfg, orchestrator=orch, data_dir=tmp_path)
    yield srv
    srv.close()


class TestTheSlotClaimIsUnchanged:
    def test_a_fresh_server_has_an_idle_slot(self, server):
        assert server.backtest_job == {"state": "idle"}

    def test_starting_claims_the_slot_and_names_the_symbol(self, server,
                                                           monkeypatch):
        monkeypatch.setattr(server, "_run_backtest", lambda *a: None)
        out = server.start_backtest("spy", 5, None)
        assert out["symbol"] == "SPY"
        assert out["state"] in {"running", "done"}

    def test_a_second_request_while_running_returns_the_same_job(self, server,
                                                                 monkeypatch):
        """Not a new claim, and not an error: the caller is told what is
        already happening. Re-claiming would run a second backtest that
        overwrites the first one's report."""
        monkeypatch.setattr(server, "_run_backtest", lambda *a: None)
        server.backtest_job = {"state": "running", "symbol": "SPY"}
        out = server.start_backtest("QQQ", 5, None)
        assert out == {"state": "running", "symbol": "SPY"}

    def test_the_parameters_are_stashed_with_the_claim(self, server,
                                                       monkeypatch):
        """`TaskSpec.callback` takes no arguments, so a parameterised job hands
        its parameters over through state. They are written under the same lock
        that claims the slot, so a claim with nobody's parameters behind it
        cannot exist — that would run the previous request's symbol.
        """
        seen: list[tuple] = []
        monkeypatch.setattr(server, "_run_backtest",
                            lambda *a: seen.append(a))
        server.start_backtest("qqq", 40, 0.75)
        server._background_backtest()
        assert seen == [("QQQ", 40, 0.75)]

    def test_the_stash_and_the_claim_are_inside_one_lock(self):
        """Structural, because a single-threaded test cannot see this.

        Moving `self._pending = ...` outside the `with self._lock` was tried
        and the behavioural test above still passed — the parameters still
        arrive, they are just no longer written atomically with the claim. The
        defect only appears under concurrency, where a second caller can see a
        claimed slot with the previous request's parameters behind it. So the
        invariant is asserted on the AST of `start()`: both assignments live
        inside the lock.
        """
        import ast

        tree = ast.parse(BACKTEST_PY.read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "start")
        # The FIRST critical section specifically — the claim. Two earlier
        # versions of this check passed against broken code: one unioned the
        # assignments of every `with self._lock` in `start()`, and one accepted
        # any single block. Both were satisfied by the failure-release branch,
        # which legitimately assigns `self._pending = None` and `self.job`
        # together. Only the claim block answers the question being asked.
        blocks = sorted(
            (node for node in ast.walk(start)
             if isinstance(node, ast.With)
             and "_lock" in ast.unparse(node.items[0].context_expr)),
            key=lambda n: n.lineno)
        assert blocks, "start() takes no lock at all"
        claim = {ast.unparse(inner.targets[0])
                 for inner in ast.walk(blocks[0])
                 if isinstance(inner, ast.Assign)}
        assert {"self._pending", "self.job"} <= claim, (
            "the FIRST critical section in start() must write the parameter "
            f"stash AND the job record; it assigned {sorted(claim)}")

    def test_a_trigger_that_fails_releases_the_slot(self, server, monkeypatch):
        """Released rather than trusted. A claimed slot with nothing behind it
        would wedge every later backtest for the life of the process."""
        monkeypatch.setattr(server.background, "trigger", lambda name: False)
        out = server.start_backtest("SPY", 5, None)
        assert out["state"] == "error"
        assert "unavailable" in out["error"]
        # and the slot is genuinely free again
        monkeypatch.setattr(server.background, "trigger", lambda name: True)
        monkeypatch.setattr(server, "_run_backtest", lambda *a: None)
        assert server.start_backtest("SPY", 5, None)["state"] == "running"

    def test_a_trigger_with_no_claim_behind_it_does_nothing(self, server,
                                                            monkeypatch):
        """Inventing parameters here would run a backtest nobody requested."""
        ran: list = []
        monkeypatch.setattr(server, "_run_backtest",
                            lambda *a: ran.append(a))
        server._background_backtest()
        assert ran == []

    def test_the_pending_claim_is_drained_exactly_once(self, server,
                                                       monkeypatch):
        ran: list = []
        monkeypatch.setattr(server, "_run_backtest",
                            lambda *a: ran.append(a))
        server.start_backtest("SPY", 5, None)
        server._background_backtest()
        server._background_backtest()
        assert len(ran) == 1


class TestTheRunIsUnchanged:
    def test_a_completed_run_records_the_report_and_writes_both_files(
            self, server, tmp_path):
        """Needs a longer history than the shared fixture carries.

        `bullish_candles()` yields 57 five-minute bars and the backtester
        refuses anything under 60 warm-up bars — so the default rig can only
        ever exercise the error path, which is a different test.
        """
        from tests.test_orchestrator import zigzag

        longer = {
            Timeframe.M5: zigzag([100, 104, 102, 107, 105, 111, 108, 114],
                                 bars_per_leg=16, freq="5min",
                                 start="2026-07-06 09:00"),
            Timeframe.H1: zigzag([90, 98, 94, 104, 100, 112],
                                 bars_per_leg=20, freq="1h",
                                 start="2026-06-20 09:00"),
        }
        spot = float(longer[Timeframe.M5]["close"].iloc[-1])
        server.orch.provider = FakeProvider(longer, spot, NOW.date())
        server._run_backtest("SPY", 5, None)
        assert server.backtest_job["state"] == "done", server.backtest_job
        assert server.backtest_job["symbol"] == "SPY"
        assert "report" in server.backtest_job
        reports = tmp_path / "reports"
        assert (reports / "spy.json").exists()
        assert (reports / "spy.html").exists()

    def test_a_failing_run_records_the_error_rather_than_raising(self, server):
        """A backtest is a user-visible job. A traceback that escapes the
        worker vanishes into the pool and the job sits on "running" forever."""
        def boom(*a, **k):
            raise RuntimeError("provider exploded")

        server.orch.provider.get_candles = boom
        server._run_backtest("SPY", 5, None)
        assert server.backtest_job["state"] == "error"
        assert "provider exploded" in server.backtest_job["error"]

    def test_min_confidence_overrides_only_the_copy(self, server):
        """The live config must not be mutated by a backtest — it is a
        `model_copy(deep=True)` for that reason."""
        before = server.cfg.engine.min_confidence
        server._run_backtest("SPY", 5, 0.99)
        assert server.cfg.engine.min_confidence == before


class TestItIsRuntimeOwned:
    def test_starting_a_backtest_starts_the_runtime(self, server, monkeypatch):
        """`start()` is idempotent and registers no scheduled work, so a
        server built with `run_loop=False` still runs the job it was asked
        for."""
        calls: list[str] = []
        monkeypatch.setattr(server.background, "start",
                            lambda: calls.append("start"))
        monkeypatch.setattr(server.background, "trigger",
                            lambda name: calls.append(name) or True)
        monkeypatch.setattr(server, "_run_backtest", lambda *a: None)
        server.start_backtest("SPY", 5, None)
        assert calls == ["start", "backtest"]

    def test_the_task_is_registered_at_construction(self, server):
        """Registered in `__init__`, not `start_loop`: `POST /api/backtest` is
        served whether or not a schedule was ever started."""
        names = {t["name"] for t in server.background.snapshot().tasks}
        assert "backtest" in names


class TestTheServiceIsWiredAndTransportFree:
    def test_the_registry_exposes_it(self, server):
        from optionspilot.services.backtest import BacktestService

        assert isinstance(server.services.backtest, BacktestService)

    def test_it_does_not_import_the_backtester_or_a_transport(self):
        """The `Backtester` is INJECTED, not imported, and the reasoning is the
        one C3 set: a pure single-purpose renderer is imported so a second
        cannot be substituted, but heavy machinery is injected. `Backtester`
        drives engine + risk + broker + journal to simulate trades, so
        importing it here would pull the whole trading stack into a layer whose
        value is that a client can use it without one.
        """
        import subprocess
        import sys

        code = ("import sys; import optionspilot.services.backtest; "
                "print([m for m in ('fastapi','starlette','uvicorn',"
                "'optionspilot.backtest') if m in sys.modules])")
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]", \
            f"services/backtest.py pulled in {out.stdout}"
