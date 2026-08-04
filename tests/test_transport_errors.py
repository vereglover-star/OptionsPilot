"""V0.9.2-C8 — a service failure becomes an HTTP status, in one place.

C1 gave failures a vocabulary, C7 made services speak it, and this is where the
vocabulary meets the wire. Three things are asserted here and each corresponds
to a way the old code was wrong:

**The table is total, in both directions.** A declared code with no status
falls through to 500 and reports a client's mistake as a server fault; a status
for a code nobody declares is dead weight that outlives its meaning. The
`ui/guide.py` catalogue drift is the standing lesson — a one-directional check
passes while the two halves separate.

**An unclassified exception is a 500.** `ui/api_v1.py` inferred a status from
the builtin type: `except ValueError` became 422 and `except KeyError` became
404. But `ValueError` is what `int("x")` and half the standard library raise,
so an internal defect arrived at the user as *their* mistake — with the
traceback discarded, because the handler believed it understood the failure.
That is finding H-7, and those two clauses are gone.

**The status table stays out of `services/`.** A `NotFound` is a statement
about the domain; that it becomes 404 over HTTP is a transport decision, and a
CLI would map the same code to an exit status.
"""

from __future__ import annotations

import pytest

from optionspilot.services.contracts import ERROR_CODES
from optionspilot.services.errors import (
    Conflict, NotFound, RateLimited, ServiceError, UnavailableProvider,
    ValidationError,
)
from optionspilot.ui.errors import (
    STATUS_FOR_CODE, UNCLASSIFIED_STATUS, missing_codes, status_for,
)


class TestTheMappingIsTotal:
    def test_every_declared_code_has_a_status(self):
        assert missing_codes() == set(), (
            "these error codes would fall through to 500 and report a client "
            f"mistake as a server fault: {sorted(missing_codes())}")

    def test_the_table_declares_no_status_for_an_unknown_code(self):
        """The other direction. A status for a code nobody raises is dead
        weight, and dead weight in a mapping is how a stale meaning survives."""
        extra = set(STATUS_FOR_CODE) - set(ERROR_CODES)
        assert extra == set(), f"statuses for undeclared codes: {sorted(extra)}"

    @pytest.mark.parametrize("code,status", [
        ("validation_error", 422),
        ("authentication_required", 401),
        ("forbidden", 403),
        ("not_found", 404),
        ("conflict", 409),
        ("rate_limited", 429),
        ("unavailable_provider", 503),
        ("internal_error", 500),
    ])
    def test_each_code_maps_to_its_stated_status(self, code, status):
        assert status_for(code) == status

    def test_an_upstream_outage_is_503_not_502(self):
        """503, because the app is reachable and working — an upstream it
        depends on is not. A 502 claims *this* server produced an invalid
        response, which points a bug report at the wrong system."""
        assert status_for("unavailable_provider") == 503

    def test_an_unknown_code_falls_back_rather_than_raising(self):
        """The caller is already handling a failure. A second exception raised
        inside the error path turns a clean 4xx into an empty 500."""
        assert status_for("something_new") == UNCLASSIFIED_STATUS
        assert status_for(None) == UNCLASSIFIED_STATUS


class TestTheStatusTableIsATransportConcern:
    def test_no_service_module_imports_it(self):
        """`services/` must not learn what an HTTP status is.

        The whole reason `ServiceError` carries no status is that a second
        client might not speak HTTP at all.
        """
        import ast
        import pathlib

        pkg = pathlib.Path(__file__).resolve().parent.parent / "optionspilot"
        offenders = []
        for py in (pkg / "services").rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                elif isinstance(node, ast.Import):
                    names.extend(a.name for a in node.names)
                if any(n.startswith("optionspilot.ui") for n in names):
                    offenders.append(f"{py.name}:{node.lineno}")
        assert not offenders, f"services/ reached into ui/: {offenders}"


class TestTheLegacyTransportMapsCodes:
    """`{"error": "<message>"}` is preserved; only the status is new."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from optionspilot.core.models import Timeframe
        from optionspilot.notify import NotificationCenter
        from optionspilot.orchestrator import Orchestrator
        from optionspilot.ui.server import create_app
        from tests.test_notify import CollectingNotifier
        from tests.test_orchestrator import (
            CFG, NOW, FakeProvider, bullish_candles,
        )

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
        app = create_app(cfg, orchestrator=orch, run_loop=False,
                         data_dir=tmp_path)
        with TestClient(app) as c:
            c.server = app.state.server
            yield c

    def test_a_validation_error_is_422_with_the_legacy_shape(self, client):
        r = client.get("/api/candles?symbol=SPY&tf=7m")
        assert r.status_code == 422
        # A string, not the v1 envelope's dict — this is what index.html reads.
        assert isinstance(r.json()["error"], str)

    @pytest.mark.parametrize("error,status", [
        (NotFound("gone"), 404),
        (Conflict("busy"), 409),
        (RateLimited("slow down"), 429),
        (UnavailableProvider("upstream down"), 503),
        (ValidationError("bad"), 422),
    ])
    def test_each_code_reaches_the_wire_with_its_status(self, client, error,
                                                        status):
        """Raised from a real service method, through the real handler.

        Patching the service rather than adding a test-only route, so this
        exercises the handler the application actually registers.
        """
        def boom(*a, **k):
            raise error

        client.server.services.charts.candles_payload = boom
        r = client.get("/api/candles?symbol=SPY&tf=5m")
        assert r.status_code == status
        assert r.json()["error"] == error.message

    def test_an_unclassified_failure_is_still_a_502_on_this_route(self, client):
        """Unchanged, and deliberately so.

        A provider that genuinely cannot answer is an upstream failure, and
        this route has always said so. C8 changed only the case where the
        SERVICE classified the failure as the client's.
        """
        def boom(*a, **k):
            raise RuntimeError("provider exploded")

        client.server.services.charts.candles_payload = boom
        assert client.get("/api/candles?symbol=SPY&tf=5m").status_code == 502


class TestTheV1TransportMapsCodes:
    """`/api/v1/*` gets the full envelope: code, message, details, request id."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from optionspilot.core.models import Timeframe
        from optionspilot.notify import NotificationCenter
        from optionspilot.orchestrator import Orchestrator
        from optionspilot.ui.server import create_app
        from tests.test_notify import CollectingNotifier
        from tests.test_orchestrator import (
            CFG, NOW, FakeProvider, bullish_candles,
        )

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
        app = create_app(cfg, orchestrator=orch, run_loop=False,
                         data_dir=tmp_path)
        with TestClient(app) as c:
            c.server = app.state.server
            yield c

    def test_a_service_error_carries_its_code_and_details(self, client):
        def boom():
            raise NotFound("no such symbol", details={"symbol": "ZZZZ"})

        client.server.status_payload = boom
        r = client.get("/api/v1/status")
        assert r.status_code == 404
        body = r.json()["error"]
        assert body["code"] == "not_found"
        assert body["message"] == "no such symbol"
        assert body["details"] == {"symbol": "ZZZZ"}

    def test_an_internal_defect_is_no_longer_reported_as_the_clients_fault(
            self, client):
        """The heart of finding H-7.

        A `KeyError` from a dict-lookup bug used to come back as **404 not
        found** — an actionable-looking answer that was completely wrong, which
        is worse than an honest 500 because the user acts on it.
        """
        def boom():
            raise KeyError("positions")

        client.server.status_payload = boom
        r = client.get("/api/v1/status")
        assert r.status_code == 500
        body = r.json()["error"]
        assert body["code"] == "internal_error"
        # Says nothing about internals; the type is kept for a bug report.
        assert "positions" not in body["message"]
        assert body["details"]["type"] == "KeyError"

    def test_a_value_error_is_no_longer_a_422(self, client):
        """`ValueError` is what `int("x")` raises. It is not evidence about
        the request."""
        def boom():
            raise ValueError("invalid literal for int()")

        client.server.status_payload = boom
        assert client.get("/api/v1/status").status_code == 500


class TestTheBaseErrorStillCannotBeRaised:
    def test_service_error_is_abstract(self):
        with pytest.raises(TypeError):
            raise ServiceError("nope")
