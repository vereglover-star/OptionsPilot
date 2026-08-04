"""V0.9.2-C3 — the market-data console, moved out of the transport.

`services/marketdata.py::MarketDataAdminService` owns what `ui/server.py` used
to: the diagnostics payload, its text rendering, trace replay, and the twelve
control-centre delegations behind Settings ▸ Market data.

**On the name.** The specification calls this commit "extract MarketDataService",
but `optionspilot/data/service.py::MarketDataService` — the tier ladder, the
class `tests/test_marketdata_service.py` calls "the file that matters most" —
already owns that name. Two classes with one name in one codebase is a
permanent tax on every grep and every import line, so this one says what it
actually does: it *administers* the market-data stack rather than being it.

**Characterization first, again.** The twelve delegations are the risk in this
move: each is two lines, they differ only in which `MarketDataControl` method
they name, and swapping two would leave a green suite and a settings page that
resets the provider order when you asked it to test a connection. So they are
pinned against a recording double — by method name AND by arguments — and that
pinning was written and passing against `ui/server.py` before the new module
existed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.core.models import Timeframe
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles

MD_PY = (pathlib.Path(__file__).resolve().parent.parent
         / "optionspilot" / "services" / "marketdata.py")


class _RecordingControl:
    """Stands in for `data/control.py::MarketDataControl`.

    Every method records its own name and arguments and returns a marker, so a
    delegation that calls the wrong method fails loudly rather than returning a
    plausible dict.
    """

    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"called": name}
        return record


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
    return UIServer(cfg, orchestrator=orch, data_dir=tmp_path)


#: (UIServer method, arguments, expected MarketDataControl method). The whole
#: point of the table is that the third column is asserted, not assumed.
DELEGATIONS = [
    ("marketdata_dashboard", (), "dashboard"),
    ("marketdata_set_key", ("finnhub", "KEY123"), "set_api_key"),
    ("marketdata_remove_key", ("finnhub",), "remove_api_key"),
    ("marketdata_set_enabled", ("finnhub", False), "set_enabled"),
    ("marketdata_move", ("finnhub", "up"), "move"),
    ("marketdata_set_order", (["a", "b"],), "set_order"),
    ("marketdata_reset_order", (), "reset_order"),
    ("marketdata_set_ordering_mode", ("static",), "set_ordering_mode"),
    ("marketdata_test", ("finnhub",), "test_connection"),
    ("marketdata_maintenance", ("rebuild",), "start_maintenance"),
    ("marketdata_maintenance_status", (), "maintenance_status"),
    ("marketdata_maintenance_cancel", (), "cancel_maintenance"),
]


class TestTheDelegationsAreUnchangedByTheMove:
    """Twelve two-line methods that differ only in the name they call."""

    @pytest.mark.parametrize("method,args,expected", DELEGATIONS)
    def test_it_calls_the_named_control_method(self, server, method, args,
                                               expected):
        control = _RecordingControl()
        server.orch.marketdata = control
        result = getattr(server, method)(*args)
        assert control.calls == [(expected, args, {})], \
            f"{method} called {control.calls} instead of {expected}{args}"
        assert result == {"called": expected}

    def test_every_delegation_is_covered_by_the_table(self, server):
        """A thirteenth delegation added without a row here is a gap.

        The table is only evidence if it is complete, and 'someone will
        remember to add a row' is not a mechanism.
        """
        public = {
            name for name in dir(server)
            if name.startswith("marketdata_")
            and name not in {"marketdata_diagnostics", "marketdata_report",
                             "marketdata_replay"}
        }
        assert public == {row[0] for row in DELEGATIONS}

    @pytest.mark.parametrize("method,args,expected", DELEGATIONS)
    def test_without_a_control_plane_it_reports_unavailable(self, server,
                                                            method, args,
                                                            expected):
        """A provider double has no registry to administer.

        Answering "not available for this provider" rather than raising is what
        lets the settings page render against any build instead of 500ing.
        """
        server.orch.marketdata = None
        result = getattr(server, method)(*args)
        assert result == {
            "available": False,
            "reason": "this provider does not expose market-data controls",
        }


class TestTheDiagnosticsPayloadIsUnchanged:
    def test_a_provider_without_diagnostics_says_so(self, server):
        payload = server.marketdata_diagnostics()
        assert payload == {
            "available": False,
            "reason": "this provider does not expose diagnostics",
        }

    def test_a_capable_provider_gets_health_plus_traces_and_version(self, server):
        from optionspilot import __version__

        recorded = {}

        class _Diagnostics:
            def recent(self, n):
                recorded["n"] = n
                return [{"id": 1}]

        server.orch.provider.health = lambda: {"providers": []}
        server.orch.provider.diagnostics = _Diagnostics()
        payload = server.marketdata_diagnostics(7)
        assert payload["available"] is True
        assert payload["traces"] == [{"id": 1}]
        assert payload["version"] == __version__
        assert recorded["n"] == 7

    @pytest.mark.parametrize("asked,clamped", [(-5, 0), (0, 0), (25, 25),
                                               (500, 200)])
    def test_the_trace_count_is_clamped_to_the_ring(self, server, asked,
                                                    clamped):
        """`max(0, min(traces, 200))`, preserved exactly.

        The upper bound stops a client asking for a payload larger than the
        ring holds; the lower one stops a negative slice reading from the end.
        """
        recorded = {}

        class _Diagnostics:
            def recent(self, n):
                recorded["n"] = n
                return []

        server.orch.provider.health = lambda: {}
        server.orch.provider.diagnostics = _Diagnostics()
        server.marketdata_diagnostics(asked)
        assert recorded["n"] == clamped

    def test_the_text_report_renders_the_same_payload(self, server):
        from optionspilot import __version__

        class _Diagnostics:
            def recent(self, n):
                return []

        server.orch.provider.health = lambda: {"providers": []}
        server.orch.provider.diagnostics = _Diagnostics()
        text = server.marketdata_report(5)
        assert isinstance(text, str)
        assert __version__ in text


class TestReplayIsUnchanged:
    def test_a_provider_without_replay_support_says_so(self, server):
        assert server.marketdata_replay(1) == {
            "error": "this provider does not support replay"}

    def test_an_unknown_trace_id_names_the_ring(self, server):
        class _Diagnostics:
            def find(self, trace_id):
                return None

        server.orch.provider.service = object()
        server.orch.provider.diagnostics = _Diagnostics()
        result = server.marketdata_replay(99)
        assert "no trace 99" in result["error"]

    def test_a_known_trace_is_replayed_through_the_injected_engine(self, server):
        """The replay engine is a collaborator, not an import.

        `data/replay.py` reaches the provider registry, the adapters and the
        quality checks — importing it into `services/` would drag the whole
        market-data stack into a layer whose point is that it does not need
        one. So the composition root hands the function down.
        """
        seen = {}

        class _Result:
            def as_dict(self):
                return {"replayed": True}

        class _Diagnostics:
            def find(self, trace_id):
                return {"id": trace_id}

        def _fake_replay(service, trace):
            seen["service"] = service
            seen["trace"] = trace
            return _Result()

        sentinel = object()
        server.orch.provider.service = sentinel
        server.orch.provider.diagnostics = _Diagnostics()
        server.services.marketdata._replay = _fake_replay
        assert server.marketdata_replay(4) == {"replayed": True}
        assert seen["service"] is sentinel
        assert seen["trace"] == {"id": 4}


class TestItTakesNoLock:
    """The documented decision C3 must preserve, not fix.

    These methods touch the provider stack, which is thread-safe and
    independent of the orchestrator's mutable state. Taking the lock would let
    a running scan block the settings page — precisely when a user is most
    likely to be looking at it.
    """

    def test_the_service_holds_no_lock(self, server):
        import threading

        service = server.services.marketdata
        lock_types = (type(threading.Lock()), type(threading.RLock()))
        offenders = [
            name for name, value in vars(service).items()
            if "lock" in name.lower().split("_") or isinstance(value, lock_types)
        ]
        assert not offenders, f"MarketDataAdminService holds {offenders}"

    def test_the_module_neither_creates_nor_enters_a_lock(self):
        tree = ast.parse(MD_PY.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    src = ast.unparse(item.context_expr)
                    if "lock" in src.lower():
                        offenders.append(f"line {node.lineno}: with {src}")
            if isinstance(node, ast.Call):
                if ast.unparse(node.func).split(".")[-1] in {"Lock", "RLock"}:
                    offenders.append(f"line {node.lineno}: lock constructed")
        assert not offenders, \
            "services/marketdata.py must not lock:\n  " + "\n  ".join(offenders)


class TestTheControlPlaneSeamStaysOverridable:
    def test_a_control_plane_attached_after_construction_is_the_one_used(self,
                                                                        server):
        """The QA routes reach `server.marketdata` directly, and tests attach a
        control plane to a live orchestrator. Resolving it at construction
        would freeze it to whatever the orchestrator had at startup — usually
        nothing."""
        assert server.marketdata is None
        control = _RecordingControl()
        server.orch.marketdata = control
        assert server.marketdata is control
