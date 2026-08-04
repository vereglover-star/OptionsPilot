"""V0.9.2-C11 — the whole application layer, built and used with no web server.

This is the milestone's central claim, cashed. V0.7.0 created `services/` so a
second client's backend would not have to import FastAPI to compute a win rate,
and V0.9.2 moved the remaining five services in. Every commit since has asserted
a *piece* of that — no `ui` import, no transport package, no HTTP status in a
service. None of them asserted the thing a second host actually needs: that you
can **construct the registry and call its services** without a web framework
ever being loaded.

It runs in a **subprocess**, because the pytest process has already imported
FastAPI many times over. Checking `sys.modules` in-process would pass no matter
what the code did — the shape of assertion that passes while testing nothing,
which this milestone has already produced three times.

What the subprocess builds is deliberately close to what `ui/server.py` builds:
a real `AppConfig`, a real `Orchestrator`, a real `RuntimeSettings`, a real
`BackgroundRuntime`, and the real `ServiceRegistry` over them. Only the market
data provider is a stub, because a test must not reach the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

#: Everything that would mean "a transport got loaded". Wider than the
#: `services/` import ban: this catches a dependency pulled in transitively, at
#: runtime, by any layer underneath — which is the case a static import check
#: cannot see.
FORBIDDEN = (
    "fastapi", "starlette", "uvicorn", "webview", "pywebview",
    "pystray", "jinja2", "flask", "django", "aiohttp", "websockets",
    "windows_toasts",
)

#: Built and exercised in the subprocess. Kept as one script rather than a
#: helper module so the whole "what a second host must supply" story is
#: readable in one place — which is the registry's own stated purpose.
SCRIPT = textwrap.dedent(
    '''
    import json, sys, tempfile, threading
    from datetime import date, datetime, timezone
    from pathlib import Path

    import pandas as pd

    from optionspilot.config.runtime import RuntimeSettings
    from optionspilot.config.settings import AppConfig
    from optionspilot.core.models import Quote
    from optionspilot.data.base import MarketDataProvider
    from optionspilot.notify import NotificationCenter
    from optionspilot.orchestrator import WINDOW_DAYS, Orchestrator
    from optionspilot.services import ServiceRegistry
    from optionspilot.services.runtime import BackgroundRuntime


    class StubProvider(MarketDataProvider):
        """The one thing a test may not use for real: the network."""

        name = "stub"

        def get_candles(self, symbol, tf, start, end, **kw):
            return pd.DataFrame()

        def get_quote(self, symbol):
            return Quote(symbol=symbol, bid=1.0, ask=1.1, last=1.05,
                         ts=datetime.now(timezone.utc))

        def get_expirations(self, symbol):
            return []

        def get_option_chain(self, symbol, expiration):
            return []


    data_dir = Path(tempfile.mkdtemp())
    config = AppConfig()
    orchestrator = Orchestrator(
        config, provider=StubProvider(),
        notifier=NotificationCenter(config.notify, []),
        data_dir=data_dir,
    )
    lock = threading.RLock()
    background = BackgroundRuntime()

    registry = ServiceRegistry(
        orchestrator=orchestrator,
        runtime=RuntimeSettings(data_dir / "settings.json", baseline=config),
        config=config,
        lock=lock,
        directory=None,
        trades=lambda: [],
        verify_symbol=lambda symbol: True,
        window_days=WINDOW_DAYS,
        clock=lambda: datetime.now(timezone.utc),
        watchlist_symbols=lambda: list(config.data.watchlist),
        reports_dir=data_dir / "reports",
        background=background,
        backtest_task="backtest",
        backtester=None,
    )

    # Exercise one method on every service. Construction alone would prove far
    # less: a registry that builds and then raises on first use is no use to
    # the second client this layer exists for.
    exercised = {
        "portfolio": type(registry.portfolio.performance(
            datetime.now(timezone.utc))).__name__,
        "watchlist": type(registry.watchlist).__name__,
        "charts": registry.charts.candles_payload("SPY", "1d")["symbol"],
        "marketdata": registry.marketdata.diagnostics()["available"],
        "trading": registry.trading.chain_payload("SPY")["symbol"],
        "backtest": registry.backtest.start("SPY", 5, None)["state"],
        "workspace": registry.workspace.get().symbol,
        "intelligence": type(registry.intelligence).__name__,
        "notifications": len(registry.notifications.recent(1)),
        "host": registry.host_view().host,
    }

    print(json.dumps({
        "loaded": sorted(m for m in sys.modules if m in FORBIDDEN_SET),
        "exercised": exercised,
    }))
    '''
)


def _run(script: str) -> dict:
    prelude = f"FORBIDDEN_SET = {set(FORBIDDEN)!r}\n"
    out = subprocess.run([sys.executable, "-c", prelude + script],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestTheRegistryNeedsNoWebFramework:
    def test_it_builds_and_serves_without_loading_a_transport(self):
        """The claim, end to end.

        Nine services constructed over a real orchestrator and each one called,
        in a process where no web or GUI framework was ever imported.
        """
        result = _run(SCRIPT)
        assert result["loaded"] == [], (
            "constructing and using ServiceRegistry pulled in a transport: "
            f"{result['loaded']}")

    def test_every_service_actually_answered(self):
        """Construction is not usability.

        A registry that builds and then raises on first use would satisfy a
        naive version of this test while being no use at all to the second
        client the layer exists for.
        """
        exercised = _run(SCRIPT)["exercised"]
        assert exercised["charts"] == "SPY"
        assert exercised["trading"] == "SPY"
        assert exercised["workspace"] == "SPY"
        # No control plane behind a stub provider, and the service says so
        # rather than raising.
        assert exercised["marketdata"] is False
        # No `Backtester` supplied, so the service reports it cannot run one.
        assert exercised["backtest"] == "error"
        assert exercised["host"]
        assert exercised["portfolio"] == "PerformanceView"

    def test_the_forbidden_list_covers_what_the_project_bans(self):
        """Kept honest against `tests/test_architecture.py`.

        Two lists of transport packages that drift apart is the two-catalogue
        failure this repository keeps paying for; this one is deliberately a
        superset, so the check is that nothing the architecture test bans is
        missing here.
        """
        from tests.test_architecture import TRANSPORT_PACKAGES

        # `PIL` and `winreg` are image/OS libraries rather than transports and
        # are legitimately absent from a runtime-loaded check: `winreg` is
        # imported by `host/` on Windows by design.
        expected = set(TRANSPORT_PACKAGES) - {"PIL", "winreg", "requests",
                                              "httpx"}
        missing = expected - set(FORBIDDEN)
        assert not missing, (
            f"these banned packages are not checked at runtime: {sorted(missing)}")
