from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.ui.server import create_app
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles
from optionspilot.core.models import Timeframe


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Freeze both clocks inside the trading window (Friday 11:00 ET) —
    # /api/scan and the P&L day/week windows use wall time, and tests must
    # not depend on when they are run.
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    provider = FakeProvider(candles, spot, NOW.date())
    cfg = CFG.model_copy(deep=True)  # runtime settings mutate the live config
    orch = Orchestrator(
        cfg, provider=provider,
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    app = create_app(cfg, orchestrator=orch, run_loop=False, data_dir=tmp_path)
    with TestClient(app) as c:
        c.provider = provider
        c.orch = orch
        c.server = app.state.server
        yield c


class TestStatusAPI:
    def test_status_shape(self, client):
        s = client.get("/api/status").json()
        assert s["paper"] is True
        assert s["account"]["equity"] == 25_000.0
        assert s["watchlist"] == ["SPY"]
        assert s["min_confidence"] == 25
        assert s["risk"]["halted"] is False
        assert s["positions"] == []

    def test_index_serves_dashboard(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "OptionsPilot" in r.text and "PAPER TRADING" in r.text

    def test_config_endpoint(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["broker"]["name"] == "paper"
        assert cfg["broker"]["live_trading_enabled"] is False


class TestScanAPI:
    def test_scan_opens_position_and_status_reflects_it(self, client):
        summary = client.post("/api/scan").json()
        assert len(summary["opened"]) == 1
        s = client.get("/api/status").json()
        assert len(s["positions"]) == 1
        pos = s["positions"][0]
        assert pos["underlying"] == "SPY" and pos["quantity"] >= 1
        assert s["signals"]["SPY"]["confidence"] > 25
        assert any(n["kind"] == "trade_opened" for n in s["notifications"])
        assert len(s["equity_history"]) == 1

    def test_journal_endpoint_after_round_trip(self, client):
        client.post("/api/scan")
        position = client.orch.broker.get_positions()[0]
        client.provider.spot = position.stop_current - 1.0
        client.post("/api/scan")
        d = client.get("/api/journal").json()
        assert d["stats"]["trades"] == 1
        t = d["trades"][0]
        assert t["pnl"] < 0 and "stop hit" in t["exit_reason"]
        assert t["entry_reasons"]
        s = client.get("/api/status").json()
        assert s["pnl"]["week"] == pytest.approx(t["pnl"], abs=0.01)


class TestRiskAPI:
    def test_reset_halt(self, client):
        client.orch.risk.record_closed_trade(NOW - timedelta(hours=1), -5000.0)
        assert client.get("/api/status").json()["risk"]["halted"] is True
        out = client.post("/api/risk/reset_halt").json()
        assert out["halted"] is False


class TestLearningAPI:
    def test_learning_shape(self, client):
        d = client.get("/api/learning").json()
        assert "htf_trend" in d["weights"]
        w = d["weights"]["htf_trend"]
        assert w["effective"] == w["default"]      # nothing learned yet
        assert d["by_evidence"] == []


class TestWatchlistAPI:
    def add(self, client, text):
        return client.post("/api/watchlist/add", json={"text": text}).json()

    def test_quick_add_validates_and_uppercases(self, client):
        r = self.add(client, "aapl")
        assert r["added"] == ["AAPL"]
        assert "Apple" in r["names"]["AAPL"]
        assert client.get("/api/watchlist").json()["watchlist"] == ["SPY", "AAPL"]

    def test_bulk_add_mixed_separators(self, client):
        r = self.add(client, "TSLA, nvda\namd  META")
        assert r["added"] == ["TSLA", "NVDA", "AMD", "META"]
        assert r["invalid"] == [] and r["duplicates"] == []

    def test_duplicates_and_invalid_reported_without_blocking(self, client):
        client.server._live_symbol_check = lambda s: False   # directory only
        r = self.add(client, "SPY, ZZZZZ, tsla")
        assert r["added"] == ["TSLA"]          # valid one still added
        assert r["duplicates"] == ["SPY"]
        assert r["invalid"] == ["ZZZZZ"]

    def test_remove_and_reorder_and_pin(self, client):
        self.add(client, "AAPL TSLA")
        assert client.post("/api/watchlist/remove",
                           json={"symbol": "AAPL"}).status_code == 200
        r = client.post("/api/watchlist/reorder",
                        json={"symbols": ["TSLA", "SPY"]})
        assert r.status_code == 200
        assert client.get("/api/watchlist").json()["watchlist"] == ["TSLA", "SPY"]
        client.post("/api/watchlist/pin", json={"symbol": "SPY", "pinned": True})
        assert client.get("/api/watchlist").json()["pinned"] == ["SPY"]
        # reorder with wrong membership is rejected
        assert client.post("/api/watchlist/reorder",
                           json={"symbols": ["TSLA"]}).status_code == 422

    def test_favorites_and_presets(self, client):
        self.add(client, "AAPL")
        client.post("/api/watchlist/favorites", json={})
        presets = client.get("/api/watchlist/presets").json()
        assert presets["My Favorites"] == ["SPY", "AAPL"]
        assert "Magnificent 7" in presets and "Meme Stocks" in presets

    def test_symbol_search(self, client):
        hits = client.get("/api/symbols/search", params={"q": "app"}).json()["results"]
        assert {"APP", "APPF"} <= {h["symbol"] for h in hits}

    def test_persists_to_settings_store(self, client):
        self.add(client, "NVDA")
        doc = client.server.runtime._doc
        assert "NVDA" in doc["watchlist"]


class TestModeAPI:
    def test_switch_takes_effect_immediately(self, client):
        r = client.post("/api/mode", json={"mode": "high_risk"}).json()
        assert r["trading_mode"] == "high_risk"
        assert client.orch.engine.gate._cfg.trading_mode == "high_risk"  # live object
        s = client.get("/api/status").json()
        assert s["trading_mode"] == "high_risk"

    def test_custom_mode_applies_validated_values(self, client):
        r = client.post("/api/mode", json={
            "mode": "custom", "custom": {"min_confidence": 65,
                                         "daily_trade_limit": 8}})
        assert r.status_code == 200
        s = client.get("/api/status").json()
        assert s["trading_mode"] == "custom"
        assert s["min_confidence"] == 65
        assert s["risk_settings"]["daily_trade_limit"] == 8
        # back to conservative restores the config baseline (25 in test CFG)
        client.post("/api/mode", json={"mode": "conservative"})
        s = client.get("/api/status").json()
        assert s["min_confidence"] == 25

    def test_bad_values_rejected_with_422(self, client):
        r = client.post("/api/mode", json={
            "mode": "custom", "custom": {"risk_per_trade_pct": 99}})
        assert r.status_code == 422 and "error" in r.json()
        assert client.post("/api/mode", json={"mode": "yolo"}).status_code == 422


class TestWebSocket:
    def test_ws_pushes_status(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["paper"] is True and "account" in msg
