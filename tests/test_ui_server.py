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
    orch = Orchestrator(
        CFG, provider=provider,
        notifier=NotificationCenter(CFG.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    app = create_app(CFG, orchestrator=orch, run_loop=False)
    with TestClient(app) as c:
        c.provider = provider
        c.orch = orch
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


class TestWebSocket:
    def test_ws_pushes_status(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["paper"] is True and "account" in msg
