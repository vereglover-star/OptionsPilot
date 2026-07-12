import pytest

from optionspilot.broker import BrokerError, PaperBroker, create_broker
from optionspilot.config.settings import AppConfig


class TestBrokerRegistry:
    def test_paper_broker_constructed(self, tmp_path):
        b = create_broker(AppConfig(), tmp_path / "p.db", 25_000.0)
        assert isinstance(b, PaperBroker)
        assert b.get_account().cash == 25_000.0

    def test_unknown_broker_rejected(self, tmp_path):
        cfg = AppConfig()
        cfg.broker.name = "etrade"   # bypass config validation deliberately
        with pytest.raises(BrokerError, match="unknown broker"):
            create_broker(cfg, tmp_path / "p.db", 25_000.0)

    @pytest.mark.parametrize("name", ["alpaca", "tradier", "webull", "ibkr"])
    def test_live_slots_exist_but_refuse(self, name, tmp_path):
        cfg = AppConfig.model_validate({
            "broker": {"name": name, "live_trading_enabled": True,
                       "i_understand_the_risks": True},
        })
        with pytest.raises(BrokerError, match="extension slot"):
            create_broker(cfg, tmp_path / "p.db", 25_000.0)

    def test_live_gate_rechecked_in_depth(self, tmp_path):
        # Even if someone mutates config after validation, the factory refuses
        cfg = AppConfig()
        cfg.broker.name = "alpaca"
        with pytest.raises(BrokerError, match="live_trading_enabled"):
            create_broker(cfg, tmp_path / "p.db", 25_000.0)
