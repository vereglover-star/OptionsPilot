"""MarketDataAdminService — diagnosing and administering the data stack.

V0.9.2-C3, and a move like C2: `ui/server.py` keeps every method name as a
delegation, and `tests/test_services_marketdata.py` pins the behaviour it had
before the move — including which `MarketDataControl` method each of the twelve
control-centre calls reaches.

**On the name.** The specification calls this "MarketDataService", but
`data/service.py::MarketDataService` — the tier ladder — has owned that name
since V0.5.2. Two identically-named classes in one codebase is a permanent cost
to every import line and every search, so this one is named for what it does:
it administers and diagnoses the stack rather than being it.

**It takes no lock, and that is the point rather than an omission.** Everything
here touches the provider stack, which is thread-safe and independent of the
orchestrator's mutable state. Taking the orchestrator lock would let a running
scan block the settings page — precisely when a user is most likely to be
looking at it, since the reason they opened it is usually that data is not
arriving.

**Two collaborators, and the difference between them is the layering rule.**
The text renderer is *imported*: `data/report.py` is pure stdlib, and the whole
reason it exists is that the JSON export, the dashboard and the text report
render one payload, so a second renderer must not be injectable. The replay
engine is *injected*: `data/replay.py` reaches the provider registry, the
adapters and the quality checks, and importing it here would drag the entire
market-data stack into a layer whose value is that it does not need one.

Nothing here raises. Every method answers a diagnostic question, and a
diagnostic that fails is worse than one that says "not available for this
provider" — which is also why an absent control plane is a `dict`, not an
exception. The `ServiceError` hierarchy (V0.9.2-C1) arrives at C7 for the
services that make *claims*; these mostly report.
"""

from __future__ import annotations

from optionspilot import __version__
from optionspilot.data import report as mdreport

#: The ring holds the most recent requests only, so asking for more than this
#: cannot be satisfied and asking for a negative count would slice from the end.
MAX_TRACES = 200


class MarketDataAdminService:
    """The Settings ▸ Market data console, without a web server."""

    def __init__(self, *, provider, control, replay=None):
        #: Both are callables resolved per request. The QA routes reach the
        #: control plane directly and tests attach one to a live orchestrator,
        #: so resolving either at construction would freeze it to whatever the
        #: orchestrator held at startup — usually nothing.
        self._provider = provider
        self._control = control
        #: `data.replay.replay(service, trace)`, handed down by the host.
        self._replay = replay

    # ── diagnostics ──────────────────────────────────────────────────────────

    def diagnostics(self, traces: int = 25) -> dict:
        """Provider health + cache stats + recent request traces.

        Returns `{"available": False}` rather than erroring when the injected
        provider predates this architecture, so the endpoint is safe to call
        against any build.
        """
        provider = self._provider()
        health = getattr(provider, "health", None)
        diagnostics = getattr(provider, "diagnostics", None)
        if health is None or diagnostics is None:
            return {"available": False,
                    "reason": "this provider does not expose diagnostics"}
        payload = health()
        payload["available"] = True
        payload["traces"] = diagnostics.recent(max(0, min(traces, MAX_TRACES)))
        payload["version"] = __version__
        return payload

    def report(self, traces: int = 25) -> str:
        """The same diagnostics rendered as plain text for a bug report.

        Rendering happens in `data/report.py` over the *same* payload the JSON
        export and the dashboard use, so the three can never disagree about a
        number.
        """
        return mdreport.render(self.diagnostics(traces),
                               traces=traces,
                               title=f"OptionsPilot v{__version__} — market "
                                     f"data diagnostics")

    def replay(self, trace_id: int) -> dict:
        """Re-run a recorded request and poll every provider directly.

        This spends real upstream requests, so it is a POST triggered by an
        explicit click on the diagnostics page — never anything the chart or a
        background timer can reach.
        """
        provider = self._provider()
        service = getattr(provider, "service", None)
        diagnostics = getattr(provider, "diagnostics", None)
        if service is None or diagnostics is None or self._replay is None:
            return {"error": "this provider does not support replay"}
        trace = diagnostics.find(trace_id)
        if trace is None:
            return {"error": f"no trace {trace_id} in the ring "
                             f"(it holds the most recent requests only)"}
        return self._replay(service, trace).as_dict()

    # ── control centre (Settings ▸ Market data) ──────────────────────────────
    #
    # Every method below delegates to `data/control.py`. That is the whole
    # design: a transport decides status codes and nothing else, so the
    # control-centre logic is testable without a web server and cannot acquire
    # a second implementation inside a route handler.

    @property
    def control(self):
        """The control plane, or None when the provider is not the real stack.

        A test (or an embedding) that injects a `MarketDataProvider` double has
        no registry to administer. Returning None rather than raising lets the
        endpoints answer "not available for this provider" the same way the
        diagnostics endpoint already does, instead of 500ing.
        """
        return self._control()

    @staticmethod
    def _unavailable() -> dict:
        return {"available": False,
                "reason": "this provider does not expose market-data controls"}

    def _delegate(self, method: str, *args) -> dict:
        """One dispatcher instead of twelve near-identical two-line methods.

        Written this way deliberately: the twelve differed only in the name
        they called, which makes a copy-paste that names the wrong one both
        easy to write and invisible to review. The names now appear exactly
        once each, in the wrappers below, and
        `tests/test_services_marketdata.py::DELEGATIONS` asserts every one.
        """
        control = self.control
        if control is None:
            return self._unavailable()
        return getattr(control, method)(*args)

    def dashboard(self) -> dict:
        return self._delegate("dashboard")

    def set_key(self, name: str, api_key: str) -> dict:
        return self._delegate("set_api_key", name, api_key)

    def remove_key(self, name: str) -> dict:
        return self._delegate("remove_api_key", name)

    def set_enabled(self, name: str, enabled: bool) -> dict:
        return self._delegate("set_enabled", name, enabled)

    def move(self, name: str, direction: str) -> dict:
        return self._delegate("move", name, direction)

    def set_order(self, order: list[str]) -> dict:
        return self._delegate("set_order", order)

    def reset_order(self) -> dict:
        return self._delegate("reset_order")

    def set_ordering_mode(self, mode: str) -> dict:
        return self._delegate("set_ordering_mode", mode)

    def test_connection(self, name: str) -> dict:
        return self._delegate("test_connection", name)

    def maintenance(self, action: str) -> dict:
        return self._delegate("start_maintenance", action)

    def maintenance_status(self) -> dict:
        return self._delegate("maintenance_status")

    def maintenance_cancel(self) -> dict:
        return self._delegate("cancel_maintenance")
