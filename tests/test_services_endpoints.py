"""The V0.7.0 endpoints, plus the regressions that come with the extraction.

Three groups:

  * `/api/workspace` — the new server-owned workspace, including the durability
    property that is the whole reason it exists.
  * `/api/host` and `/api/diagnostics/sync` — the readiness surfaces.
  * The extraction regressions. `UIServer` kept every method name and every
    payload shape, and the assertions here are the ones that would have caught
    a behaviour change hidden inside a "pure refactor" — including the
    `/api/learning` weights path, which was genuinely broken before this
    milestone.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from optionspilot.learning import WeightStore
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.ui.server import create_app
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles
from optionspilot.core.models import Timeframe


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    app = create_app(cfg, orchestrator=orch, run_loop=False, data_dir=tmp_path)
    with TestClient(app) as c:
        c.orch = orch
        c.server = app.state.server
        c.tmp_path = tmp_path
        yield c


class TestWorkspaceEndpoint:
    def test_a_fresh_install_gets_the_shipped_defaults(self, client):
        doc = client.get("/api/workspace").json()
        assert doc["symbol"] == "SPY" and doc["timeframe"] == "1d"
        assert doc["layouts"] == {} and doc["recent_symbols"] == []

    def test_a_patch_is_merged_and_persisted(self, client):
        client.post("/api/workspace", json={"symbol": "qqq", "tab": "charts"})
        doc = client.get("/api/workspace").json()
        assert doc["symbol"] == "QQQ" and doc["tab"] == "charts"

    def test_a_partial_patch_does_not_clobber_other_keys(self, client):
        """The property that lets a less capable client write safely."""
        client.post("/api/workspace", json={"tab": "charts",
                                            "layouts": {"mine": {"a": 1}}})
        client.post("/api/workspace", json={"symbol": "NVDA"})
        doc = client.get("/api/workspace").json()
        assert doc["tab"] == "charts" and doc["layouts"] == {"mine": {"a": 1}}

    def test_workspace_survives_a_server_restart(self, client, tmp_path,
                                                 monkeypatch):
        """The whole reason this moved off `localStorage`.

        A cleared WebView2 profile, a restored backup or a reinstall silently
        discarded every one of these. The same argument V0.6.1 made for
        onboarding progress — a returning user must not be greeted as a
        beginner — applies to a returning user's chart.
        """
        client.post("/api/workspace", json={"symbol": "AMD", "timeframe": "15m"})
        monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
        monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
        cfg = CFG.model_copy(deep=True)
        candles = bullish_candles()
        orch = Orchestrator(
            cfg, provider=FakeProvider(candles,
                                       float(candles[Timeframe.M5]["close"].iloc[-1]),
                                       NOW.date()),
            notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
            data_dir=tmp_path,
        )
        fresh = create_app(cfg, orchestrator=orch, run_loop=False,
                           data_dir=tmp_path)
        with TestClient(fresh) as second:
            doc = second.get("/api/workspace").json()
        assert doc["symbol"] == "AMD" and doc["timeframe"] == "15m"

    def test_workspace_shares_settings_json_without_disturbing_it(self, client,
                                                                  tmp_path):
        """`settings.json` is the shared-writer entry in the sync inventory, and
        this is the concrete hazard it flags: a workspace write must not touch
        the trading mode or the watchlist."""
        client.post("/api/mode", json={"mode": "high_risk"})
        client.post("/api/workspace", json={"symbol": "TSLA"})
        doc = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert doc["trading_mode"] == "high_risk"
        assert doc["workspace"]["symbol"] == "TSLA"
        assert client.get("/api/status").json()["trading_mode"] == "high_risk"

    def test_garbage_is_absorbed_rather_than_rejected(self, client):
        """No 4xx in the middle of scrolling a chart."""
        for body in ({"timeframe": "nonsense"}, {"layouts": "not a dict"},
                     {"symbol": None}, {}, {"unknown": 1}):
            r = client.post("/api/workspace", json=body)
            assert r.status_code == 200
        assert client.get("/api/workspace").json()["timeframe"] == "1d"

    def test_reset_returns_to_defaults(self, client):
        client.post("/api/workspace", json={"symbol": "AMD",
                                            "layouts": {"x": {"a": 1}}})
        doc = client.delete("/api/workspace").json()
        assert doc["symbol"] == "SPY" and doc["layouts"] == {}

    def test_the_full_context_round_trips_through_the_transport(self, client):
        """UI V2 M1-C3. Symbol, timeframe, expiry, contract and Surface Level
        are what `UI_V2_DESIGN.md` §4.5 calls context; a client must be able to
        set and re-read all of it, and it must survive JSON in both
        directions."""
        contract = {"symbol": "SPY", "expiry": "2026-09-18",
                    "strike": 450.0, "right": "call"}
        client.post("/api/workspace", json={"symbol": "SPY", "timeframe": "15m",
                                            "expiry": "2026-09-18",
                                            "contract": contract,
                                            "surface_level": 1})
        doc = client.get("/api/workspace").json()
        assert doc["expiry"] == "2026-09-18"
        assert doc["contract"] == contract
        assert doc["surface_level"] == 1

    def test_a_symbol_change_over_the_wire_drops_the_selected_contract(self, client):
        client.post("/api/workspace", json={
            "symbol": "SPY",
            "contract": {"symbol": "SPY", "expiry": "2026-09-18",
                         "strike": 450.0, "right": "put"}})
        doc = client.post("/api/workspace", json={"symbol": "QQQ"}).json()
        assert doc["contract"] is None

    def test_surface_level_is_stored_outside_the_workspace_document(self, client):
        """Two keys in `settings.json`, because they have two sync policies:
        the workspace follows a user to a second device, Surface Level does
        not (`ROADMAP-UI-V2.md` §11-5)."""
        client.post("/api/workspace", json={"surface_level": 4,
                                            "symbol": "TSLA"})
        doc = json.loads((client.tmp_path / "settings.json")
                         .read_text(encoding="utf-8"))
        assert doc["surface_level"] == 4
        assert "surface_level" not in doc["workspace"]
        assert doc["workspace"]["symbol"] == "TSLA"

    def test_a_bad_surface_level_does_not_4xx_the_endpoint(self, client):
        r = client.post("/api/workspace", json={"surface_level": 99})
        assert r.status_code == 200
        assert r.json()["surface_level"] == 3

    def test_a_stored_expiry_is_one_the_chain_endpoint_accepts(self, client):
        """The same argument as the timeframe test below, for the same reason:
        this value is handed straight back to `/api/chain`, and a value that
        endpoint cannot parse is a 5xx in the middle of a chain load."""
        from datetime import date
        client.post("/api/workspace", json={"expiry": "2026-09-18"})
        stored = client.get("/api/workspace").json()["expiry"]
        assert date.fromisoformat(stored)

    def test_a_stored_timeframe_is_one_the_candles_endpoint_accepts(self, client):
        """The reason `timeframe` is validated where `tab` is not: this value is
        handed straight back to `/api/candles`."""
        client.post("/api/workspace", json={"timeframe": "5m"})
        tf = client.get("/api/workspace").json()["timeframe"]
        assert client.get(f"/api/candles?symbol=SPY&tf={tf}").status_code == 200


class TestHostEndpoint:
    def test_host_reports_capabilities_and_no_user_data(self, client):
        doc = client.get("/api/host").json()
        assert doc["implemented"] is True
        assert "bind_listener" in doc["capabilities"]
        assert set(doc) == {"host", "python_platform", "capabilities",
                            "missing", "notes", "implemented"}

    def test_sync_boundaries_are_exposed_and_name_the_never_list(self, client):
        doc = client.get("/api/diagnostics/sync").json()
        assert doc["never_sync"] == ["data/credentials.json"]
        assert doc["counts"]["inventory"] > 10

    def test_no_endpoint_leaks_an_api_key_shaped_value(self, client):
        """The `tests/test_credentials.py::TestNoLeak` discipline extended to
        the two new payloads: anything a user is invited to attach to a public
        bug report gets enumerated before it ships."""
        for route in ("/api/host", "/api/diagnostics/sync"):
            body = client.get(route).text
            assert "api_key" not in body.lower()


class TestExtractionRegressions:
    def test_learning_reads_the_SAME_weights_file_the_engine_loads(
            self, client, tmp_path):
        """The defect V0.7.0 found and fixed.

        `/api/learning` built its `WeightStore` from `Path("data")` — relative
        to the process CWD, which on any real install is not the storage root.
        The file simply did not exist, so the Learning tab reported no learned
        weights however much the engine had learned. `effective` came from the
        live scorer and WAS right, which is what made it look plausible for
        three milestones.
        """
        store = WeightStore(tmp_path / "learning" / "weights.json")
        store.save({"htf_trend": 0.42}, ["a measured edge"])

        doc = client.get("/api/learning").json()
        assert doc["weights"]["htf_trend"]["learned"] == 0.42, \
            "the Learning view is reading a different file from the engine"
        assert doc["weights_version"] == store.version()

    def test_status_positions_keep_their_pre_V070_shape(self, client):
        client.post("/api/scan", json={"wait": True})
        position = client.get("/api/status").json()["positions"][0]
        assert set(position) == {
            "contract", "underlying", "expiration", "strike", "right",
            "managed_by", "direction", "quantity", "avg_price", "mark",
            "unrealized", "entry_spot", "stop", "target", "opened_at"}

    def test_account_metrics_keep_their_pre_V070_shape(self, client):
        doc = client.get("/api/account/metrics").json()
        assert set(doc) == {
            "cash", "buying_power", "portfolio_value", "unrealized_pnl",
            "realized_pnl", "daily_pnl", "total_return_pct", "trades",
            "win_rate", "avg_win", "avg_loss", "profit_factor",
            "max_drawdown_pct", "equity_history"}

    def test_watchlist_add_result_keeps_its_pre_V070_shape(self, client):
        """`error` is ABSENT rather than null on success — the pre-extraction
        wire shape, and the frontend tests truthiness on it."""
        doc = client.post("/api/watchlist/add", json={"text": "SPY"}).json()
        assert "error" not in doc
        assert set(doc) == {"added", "invalid", "duplicates", "over_cap", "names"}

    def test_notifications_gained_severity_without_losing_a_field(self, client):
        """Additive only. A client that has never heard of `severity` reads the
        same four keys it always did."""
        client.orch.notifier.notify("trade_opened", "t", "b")
        item = client.get("/api/status").json()["notifications"][0]
        assert {"kind", "title", "body", "ts"} <= set(item)
        assert item["severity"] == "notice"

    def test_a_replaced_symbol_verifier_still_takes_effect(self, client):
        """The bug the extraction introduced and an existing test caught.

        `ServiceRegistry` first captured these bound methods at construction, so
        a later reassignment was silently ignored. They are bound late now, and
        this asserts the seam directly rather than through a watchlist case.
        """
        client.server._live_symbol_check = lambda s: False
        doc = client.post("/api/watchlist/add", json={"text": "ZZZZZ"}).json()
        assert doc["invalid"] == ["ZZZZZ"] and doc["added"] == []

        client.server._live_symbol_check = lambda s: True
        doc = client.post("/api/watchlist/add", json={"text": "ZZZZZ"}).json()
        assert doc["added"] == ["ZZZZZ"]

    def test_the_status_payload_is_still_json_safe(self, client):
        """`json.dumps(..., allow_nan=False)` is what the WebSocket digest and
        the browser both require; an `inf` reaching either is a dead page."""
        client.post("/api/scan", json={"wait": True})
        json.dumps(client.get("/api/status").json(), allow_nan=False)
