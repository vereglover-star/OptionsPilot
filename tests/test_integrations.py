import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Timeframe
from optionspilot.integrations import parse_alert
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.ui.server import create_app
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles

SECRET = "a-sufficiently-long-secret"


class TestParseAlert:
    def test_valid_alert(self):
        a = parse_alert({"secret": SECRET, "symbol": "spy", "note": "1h BOS"},
                        SECRET)
        assert a.symbol == "SPY" and a.note == "1h BOS"

    def test_exchange_prefix_stripped(self):
        assert parse_alert({"secret": SECRET, "symbol": "NASDAQ:AAPL"},
                           SECRET).symbol == "AAPL"

    def test_wrong_secret_rejected(self):
        with pytest.raises(ValueError, match="invalid secret"):
            parse_alert({"secret": "nope", "symbol": "SPY"}, SECRET)

    def test_no_configured_secret_rejected(self):
        with pytest.raises(ValueError, match="not configured"):
            parse_alert({"secret": "", "symbol": "SPY"}, "")

    def test_missing_or_garbage_symbol_rejected(self):
        with pytest.raises(ValueError, match="missing symbol"):
            parse_alert({"secret": SECRET}, SECRET)
        with pytest.raises(ValueError, match="invalid symbol"):
            parse_alert({"secret": SECRET, "symbol": "DROP TABLE;"}, SECRET)

    def test_non_dict_payload_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            parse_alert(["not", "a", "dict"], SECRET)

    def test_note_truncated(self):
        a = parse_alert({"secret": SECRET, "symbol": "SPY", "note": "x" * 999},
                        SECRET)
        assert len(a.note) == 200


class TestConfigGate:
    def test_enabling_webhook_requires_real_secret(self):
        with pytest.raises(ValidationError, match="at least"):
            AppConfig.model_validate({
                "integrations": {"tradingview_webhook": True,
                                 "tradingview_secret": "short"},
            })

    def test_disabled_by_default(self):
        cfg = AppConfig()
        assert cfg.integrations.tradingview_webhook is False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    cfg = CFG.model_copy(deep=True)
    cfg.integrations.tradingview_webhook = True
    cfg.integrations.tradingview_secret = SECRET
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    provider = FakeProvider(candles, spot, NOW.date())
    orch = Orchestrator(
        cfg, provider=provider,
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    app = create_app(cfg, orchestrator=orch, run_loop=False)
    with TestClient(app) as c:
        c.orch = orch
        yield c


class TestWebhookEndpoint:
    def test_disabled_returns_403(self, tmp_path, monkeypatch):
        monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
        candles = bullish_candles()
        provider = FakeProvider(candles,
                                float(candles[Timeframe.M5]["close"].iloc[-1]),
                                NOW.date())
        orch = Orchestrator(
            CFG, provider=provider,
            notifier=NotificationCenter(CFG.notify, [CollectingNotifier()]),
            data_dir=tmp_path,
        )
        with TestClient(create_app(CFG, orchestrator=orch, run_loop=False)) as c:
            r = c.post("/webhook/tradingview",
                       json={"secret": SECRET, "symbol": "SPY"})
            assert r.status_code == 403 and "disabled" in r.json()["error"]

    def test_wrong_secret_returns_403(self, client):
        r = client.post("/webhook/tradingview",
                        json={"secret": "wrong", "symbol": "SPY"})
        assert r.status_code == 403
        assert client.orch.broker.get_positions() == []

    def test_malformed_returns_422(self, client):
        r = client.post("/webhook/tradingview", json={"secret": SECRET})
        assert r.status_code == 422

    def test_alert_runs_full_pipeline(self, client):
        r = client.post("/webhook/tradingview",
                        json={"secret": SECRET, "symbol": "NASDAQ:SPY",
                              "note": "manual alert"})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "tradingview" and body["symbol"] == "SPY"
        # Bullish rig + permissive config -> the scan actually opened a trade,
        # proving the alert went through engine + risk, not around them.
        assert len(body["opened"]) == 1
        assert client.orch.broker.get_positions()[0].contract.underlying == "SPY"

    def test_alert_for_held_symbol_is_skipped(self, client):
        client.post("/webhook/tradingview",
                    json={"secret": SECRET, "symbol": "SPY"})
        r = client.post("/webhook/tradingview",
                        json={"secret": SECRET, "symbol": "SPY"})
        assert r.json()["skipped"]["SPY"] == "position already open"
        assert len(client.orch.broker.get_positions()) == 1
