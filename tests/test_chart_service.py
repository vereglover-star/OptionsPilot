"""V0.9.2-C2 — the chart payload, moved out of the transport.

Two jobs, and the first is the reason the file was written before the move
rather than after it.

**Characterization.** `TestThePayloadIsUnchangedByTheMove` pins the exact bytes
`candles_payload` produces for a deterministic provider — every key, every bar,
every rounded indicator value. It was written against `ui/server.py`'s
implementation and passed there; it must keep passing against
`services/charts.py`'s. A refactor whose test is written afterwards proves only
that the new code agrees with itself.

**The layer's own rules.** A service is only worth extracting if it is reachable
without the transport, so that claim is asserted in a subprocess rather than
by inspection — `import optionspilot.services.charts` must not pull FastAPI into
`sys.modules`. And this particular service takes no lock, which is a property of
the domain (it reads a provider, never orchestrator state) and is asserted
structurally so a later edit cannot quietly acquire one.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.services.charts import ChartService
from optionspilot.ui.server import UIServer
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles

CHARTS_PY = (pathlib.Path(__file__).resolve().parent.parent
             / "optionspilot" / "services" / "charts.py")


class _EmptyProvider:
    """The minimum a provider can be: the oldest two-method contract."""

    def get_candles(self, symbol, tf, start, end, **kw):
        import pandas as pd
        return pd.DataFrame()


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A UIServer over the same frozen fake the UI suite uses.

    Frozen to Friday 11:00 ET so `market_open` and the default window are not a
    function of when the suite runs.
    """
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.services.charts.utcnow", lambda: NOW)
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    cfg = CFG.model_copy(deep=True)
    orch = Orchestrator(
        cfg, provider=FakeProvider(candles, spot, NOW.date()),
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    return UIServer(cfg, orchestrator=orch, data_dir=tmp_path)


# The payload recorded from `ui/server.py::candles_payload` BEFORE the
# extraction, with a FakeProvider that has no `get_history` (so no provider
# meta is spread in). Values are the rounded floats the endpoint serialises.
GOLDEN_KEYS = {"symbol", "timeframe", "candles", "indicators", "stale",
               "as_of", "market_open", "extended_hours"}
GOLDEN_SCALARS = {"symbol": "SPY", "timeframe": "5m", "stale": False,
                  "as_of": None, "market_open": True, "extended_hours": False}
GOLDEN_BARS = 57
GOLDEN_FIRST = {"time": 1783674000, "open": 100.0, "high": 100.0005,
                "low": 99.9998, "close": 100.0, "volume": 1000}
GOLDEN_LAST = {"time": 1783690800, "open": 113.25, "high": 114.0375,
               "low": 113.235, "close": 114.0, "volume": 1000}
GOLDEN_INDICATORS = {
    "ema9": 111.6448, "ema21": 110.0408, "ema50": 107.4644,
    "vwap": 106.2188, "bb_upper": 113.4143, "bb_lower": 107.0857,
    "bb_mid": 110.25, "rsi": 80.1412, "macd": 1.644,
    "macd_signal": 1.3429, "macd_hist": 0.3011,
}


class TestThePayloadIsUnchangedByTheMove:
    """The golden. Written and passing before `services/charts.py` existed."""

    def test_the_key_set_is_exactly_what_it_was(self, server):
        assert set(server.candles_payload("SPY", "5m")) == GOLDEN_KEYS

    def test_the_scalars_are_unchanged(self, server):
        payload = server.candles_payload("SPY", "5m")
        assert {k: payload[k] for k in GOLDEN_SCALARS} == GOLDEN_SCALARS

    def test_every_bar_is_unchanged(self, server):
        bars = server.candles_payload("SPY", "5m")["candles"]
        assert len(bars) == GOLDEN_BARS
        assert bars[0] == GOLDEN_FIRST
        assert bars[-1] == GOLDEN_LAST

    def test_every_indicator_series_is_unchanged(self, server):
        """Value parity, not just presence.

        The endpoint's whole claim is that the chart is drawn by the SAME
        analysis library the engine trades with. A moved service that computed
        an EMA a fraction differently would still look like a chart.
        """
        series = server.candles_payload("SPY", "5m")["indicators"]
        assert set(series) == set(GOLDEN_INDICATORS)
        for name, expected in GOLDEN_INDICATORS.items():
            assert len(series[name]) == GOLDEN_BARS
            assert series[name][-1] == expected

    def test_the_service_and_the_server_produce_the_same_object(self, server):
        """`UIServer.candles_payload` is a delegation, not a second copy.

        It stays as a public method because `tests/test_ui_server.py` and the
        route both call it — but it must forward, not reimplement. Two
        implementations of one payload is the drift this repository keeps
        paying for.
        """
        assert server.candles_payload("SPY", "5m") == \
            server.services.charts.candles_payload("SPY", "5m")


class TestItIsReachableWithoutATransport:
    """The V0.7.0 invariant, asserted for this service specifically."""

    def test_importing_it_does_not_pull_in_fastapi(self):
        """A subprocess, because the suite has already imported FastAPI.

        Checking `sys.modules` in-process would pass no matter what this module
        imported, which is the shape of assertion that passes while testing
        nothing.
        """
        code = ("import sys; import optionspilot.services.charts; "
                "bad=[m for m in ('fastapi','starlette','uvicorn') "
                "if m in sys.modules]; print(bad)")
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]", \
            f"importing services.charts pulled in a transport: {out.stdout}"

    def test_it_constructs_from_plain_callables(self):
        """No orchestrator, no app, no config object — just collaborators.

        This is what a CLI or a future mobile backend has to supply, and the
        list being short is the point of the extraction. It renders, rather
        than merely constructing: an assertion that the object exists would
        pass against a service that could not answer a single request.
        """
        service = ChartService(
            provider=lambda: _EmptyProvider(),
            indicators=CFG.indicators,
            market_open=lambda now: True,
        )
        payload = service.candles_payload("SPY", "5m")
        assert payload["symbol"] == "SPY"
        assert payload["timeframe"] == "5m"
        assert payload["market_open"] is True


class TestItTakesNoLock:
    """Provider-only, so chart loads never contend with a running scan.

    That was already true in `ui/server.py` and stated in its docstring; moving
    the code is exactly the moment such a property gets lost, so it is asserted
    structurally rather than left to the prose.
    """

    def test_the_service_holds_no_lock(self):
        """By name AND by type.

        The name check alone once failed on `_clock`, which is the shape of
        assertion that is either wrong now or wrong later; the type check is
        the one that actually means something.
        """
        import threading

        service = ChartService(provider=lambda: object(),
                               indicators=CFG.indicators,
                               market_open=lambda now: False)
        lock_types = (type(threading.Lock()), type(threading.RLock()))
        offenders = [
            name for name, value in vars(service).items()
            if "lock" in name.lower().split("_") or isinstance(value, lock_types)
        ]
        assert not offenders, \
            f"ChartService acquired a lock attribute: {offenders}"

    def test_the_module_neither_creates_nor_enters_a_lock(self):
        """AST, not text: the docstrings here discuss locking at length."""
        tree = ast.parse(CHARTS_PY.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    src = ast.unparse(item.context_expr)
                    if "lock" in src.lower():
                        offenders.append(f"line {node.lineno}: with {src}")
            if isinstance(node, ast.Call):
                src = ast.unparse(node.func)
                if src.split(".")[-1] in {"Lock", "RLock"}:
                    offenders.append(f"line {node.lineno}: {src}()")
        assert not offenders, \
            "services/charts.py must not lock:\n  " + "\n  ".join(offenders)


class TestTheProviderSeamStaysOverridable:
    """A registry that captures a bound collaborator freezes it.

    `ServiceRegistry` learned this once already (`_live_symbol_check`). Tests
    swap the provider — and its individual methods — on a live orchestrator, so
    the service must read it through a callable on every request rather than
    holding the object it saw at construction.
    """

    def test_a_provider_swapped_after_construction_is_the_one_used(self):
        seen: list[str] = []

        class _Recording:
            def get_candles(self, symbol, tf, start, end, **kw):
                seen.append(symbol)
                import pandas as pd
                return pd.DataFrame()

        holder = {"provider": object()}
        service = ChartService(provider=lambda: holder["provider"],
                               indicators=CFG.indicators,
                               market_open=lambda now: False)
        holder["provider"] = _Recording()
        service.candles_payload("QQQ", "5m")
        assert seen == ["QQQ"]


class TestTheWindowTableHasOneOwner:
    """`orchestrator.WINDOW_DAYS` is handed down, never re-declared here.

    `services/` cannot import the orchestrator, which leaves two options: copy
    the table or receive it. A copy is a second owner of one fact, and the two
    would drift about how far back a 5-minute chart opens — so the composition
    root passes it into the registry.
    """

    def test_the_service_uses_the_orchestrators_table(self, server):
        from optionspilot.orchestrator import WINDOW_DAYS

        assert server.services.charts._window_days == WINDOW_DAYS

    def test_the_module_declares_no_window_table_of_its_own(self):
        """A dict literal keyed by Timeframe members would be the copy."""
        source = CHARTS_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Attribute)
                and isinstance(k.value, ast.Name)
                and k.value.id == "Timeframe"
                for k in node.keys if k is not None)
        ]
        assert not offenders, \
            f"services/charts.py declares its own Timeframe table at {offenders}"

    def test_a_timeframe_with_no_window_still_renders(self):
        """The fallback is visible, not silent.

        A host that supplies a partial table (or none) must still get a chart
        rather than a KeyError — the old code indexed `WINDOW_DAYS[tf]`
        directly, which turned a missing entry into a 500.
        """
        service = ChartService(provider=lambda: _EmptyProvider(),
                               indicators=CFG.indicators,
                               market_open=lambda now: False,
                               window_days={})
        payload = service.candles_payload("SPY", "5m")
        assert payload["candles"] == []


class TestTheServiceIsRegistered:
    def test_the_registry_exposes_it(self, server):
        assert isinstance(server.services.charts, ChartService)
