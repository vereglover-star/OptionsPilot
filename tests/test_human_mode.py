"""V2-3: operating modes (AI vs Human) and the manual-trade coaching loop."""

import json
from datetime import timedelta

import pytest

from optionspilot.broker.orders import OrderKind
from optionspilot.config.runtime import RuntimeSettings
from optionspilot.config.settings import AppConfig
from optionspilot.core.models import OptionRight, Timeframe
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles


def make_rig(tmp_path, operating_mode="ai"):
    cfg = CFG.model_copy(deep=True)
    cfg.engine.operating_mode = operating_mode
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    provider = FakeProvider(candles, spot, NOW.date())
    sink = CollectingNotifier()
    orch = Orchestrator(cfg, provider=provider,
                        notifier=NotificationCenter(cfg.notify, [sink]),
                        data_dir=tmp_path)
    return orch, provider, sink


def atm_call(provider):
    chain = provider.get_option_chain("SPY", None)
    calls = [c for c in chain if c.right is OptionRight.CALL]
    return min(calls, key=lambda c: abs(c.strike - provider.spot))


class TestHumanMode:
    def test_ai_never_enters_in_human_mode(self, tmp_path):
        orch, provider, sink = make_rig(tmp_path, operating_mode="human")
        summary = orch.run_cycle(NOW)
        assert summary["opened"] == []
        assert orch.broker.get_positions() == []
        # the signal is still computed and surfaced as advice
        assert summary["signals"]["SPY"]["confidence"] > 25
        assert "Human Mode" in summary["skipped"]["SPY"]
        assert any("advice only" in e.title for e in sink.events)

    def test_advice_not_spammed_per_cycle(self, tmp_path):
        orch, provider, sink = make_rig(tmp_path, operating_mode="human")
        orch.run_cycle(NOW)
        orch.run_cycle(NOW + timedelta(minutes=1))   # same last bar
        advice = [e for e in sink.events if "advice only" in e.title]
        assert len(advice) == 1

    def test_runtime_switch_is_instant_and_persists(self, tmp_path):
        cfg = AppConfig.model_validate({"engine": {"operating_mode": "ai"}})
        rt = RuntimeSettings(tmp_path / "settings.json", baseline=cfg)
        rt.set_operating_mode(cfg, "human")
        assert cfg.engine.operating_mode == "human"
        # switching the RISK mode must not flip the operating mode
        rt.set_mode(cfg, "high_risk")
        assert cfg.engine.operating_mode == "human"
        # and it survives a restart
        cfg2 = AppConfig.model_validate({"engine": {"operating_mode": "ai"}})
        rt2 = RuntimeSettings(tmp_path / "settings.json", baseline=cfg2)
        rt2.apply(cfg2)
        assert cfg2.engine.operating_mode == "human"
        with pytest.raises(ValueError):
            rt.set_operating_mode(cfg, "autopilot")


class TestManualTradeCoaching:
    def test_manual_round_trip_is_journaled_and_coached(self, tmp_path):
        orch, provider, sink = make_rig(tmp_path, operating_mode="human")
        contract = atm_call(provider)

        # user buys 1 ATM call (market)
        orch.orders.place(OrderKind.MARKET, "buy_to_open", contract, 1,
                          NOW, spot=provider.spot)
        orch.register_manual_entry(contract.symbol)
        orch.run_cycle(NOW + timedelta(minutes=1))   # context captured
        meta = json.loads(
            (tmp_path / "state" / "manual_trades.json").read_text())
        assert contract.symbol in meta
        assert meta[contract.symbol]["entry_context"] is not None
        assert meta[contract.symbol]["entry_context"]["gate"]

        # underlying rallies; user sells at market
        provider.spot += 3.0
        fresh = atm_call(provider)  # same strikes, fresh quotes
        fresh = next(c for c in provider.get_option_chain("SPY", None)
                     if c.symbol == contract.symbol)
        orch.orders.place(OrderKind.MARKET, "sell_to_close", fresh, 1,
                          NOW + timedelta(minutes=30))
        summary = orch.run_cycle(NOW + timedelta(minutes=31))

        # journaled as a manual trade with a coach review attached
        manual = [t for t in orch.journal.all() if t.strategy == "manual"]
        assert len(manual) == 1
        trade = manual[0]
        assert trade.pnl > 0
        assert "coach_score" in trade.market_conditions
        assert "no_stop" in trade.mistakes           # never placed a stop
        assert trade.lessons                          # exercises attached
        # review persisted to disk
        review = orch.coach.load(trade.id)
        assert review is not None and 5 <= review["score"] <= 95
        assert review["verdict"] == "won"
        # meta cleaned up; close surfaced with the score
        assert json.loads(
            (tmp_path / "state" / "manual_trades.json").read_text()) == {}
        assert any(c.get("coach_score") for c in summary["closed"])
        assert any("Coach review" in e.title for e in sink.events)

    def test_manual_loss_counts_toward_risk_limits(self, tmp_path):
        orch, provider, sink = make_rig(tmp_path, operating_mode="human")
        contract = atm_call(provider)
        orch.orders.place(OrderKind.MARKET, "buy_to_open", contract, 1,
                          NOW, spot=provider.spot)
        orch.run_cycle(NOW + timedelta(minutes=1))
        provider.spot -= 4.0
        fresh = next(c for c in provider.get_option_chain("SPY", None)
                     if c.symbol == contract.symbol)
        orch.orders.place(OrderKind.MARKET, "sell_to_close", fresh, 1,
                          NOW + timedelta(minutes=10))
        orch.run_cycle(NOW + timedelta(minutes=11))
        trade = orch.journal.all()[-1]
        assert trade.pnl < 0
        assert orch.risk.status()["consecutive_losses"] == 1
