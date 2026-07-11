from datetime import timedelta

import pytest

from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Direction, Signal, Timeframe
from optionspilot.engine.contracts import ContractSelector
from optionspilot.engine.planner import TradePlanner
from tests.engine_helpers import (
    TODAY, TS, bullish_entry_view, bearish_entry_view, make_call, make_put, make_view,
)

ENGINE_CFG = AppConfig().engine


def make_signal(direction=Direction.LONG, confidence=85.0) -> Signal:
    return Signal(symbol="SPY", ts=TS, direction=direction, confidence=confidence,
                  evidence=(), strategy="test", timeframe=Timeframe.M5)


class TestContractSelector:
    def setup_method(self):
        self.sel = ContractSelector(ENGINE_CFG)

    def test_picks_closest_to_target_delta(self):
        chain = [make_call(110, 0.62), make_call(114, 0.46), make_call(118, 0.31)]
        res = self.sel.select(Direction.LONG, chain, spot=114.0, today=TODAY)
        assert res.contract is not None and res.contract.delta == 0.46

    def test_puts_for_short(self):
        chain = [make_call(114, 0.46), make_put(114, -0.47), make_put(110, -0.30)]
        res = self.sel.select(Direction.SHORT, chain, spot=114.0, today=TODAY)
        assert res.contract is not None
        assert res.contract.right.value == "put" and res.contract.delta == -0.47

    def test_rejects_dte_out_of_window(self):
        chain = [make_call(114, 0.45, expiration=TODAY + timedelta(days=2))]
        res = self.sel.select(Direction.LONG, chain, 114.0, TODAY)
        assert res.contract is None
        assert res.rejections == {"dte_out_of_window": 1}

    def test_rejects_wide_spread_and_thin_market(self):
        chain = [
            make_call(114, 0.45, bid=2.00, ask=2.60),         # 26% spread
            make_call(115, 0.44, oi=10),                      # OI below 200
            make_call(116, 0.43, volume=5),                   # volume below 50
        ]
        res = self.sel.select(Direction.LONG, chain, 114.0, TODAY)
        assert res.contract is None
        assert res.rejections == {
            "spread_too_wide": 1, "open_interest_too_low": 1, "volume_too_low": 1,
        }
        assert "all 3 contracts rejected" in res.reason

    def test_empty_chain(self):
        res = self.sel.select(Direction.LONG, [], 114.0, TODAY)
        assert res.contract is None and res.reason == "empty chain"

    def test_liquidity_tiebreak(self):
        a = make_call(114, 0.45, oi=300, volume=60)     # passes, mediocre liquidity
        b = make_call(115, 0.45, oi=5000, volume=2000)  # same delta, deep market
        res = self.sel.select(Direction.LONG, [a, b], 114.0, TODAY)
        assert res.contract is b


class TestTradePlanner:
    def setup_method(self):
        self.planner = TradePlanner(ENGINE_CFG)

    def test_long_plan_uses_structure(self):
        view = bullish_entry_view()  # swing low 98, swing high 105, atr 1.0
        plan = self.planner.plan(make_signal(), view, make_call(100, 0.45), spot=100.0)
        assert plan is not None
        assert plan.stop_underlying == pytest.approx(98.0 - 0.25)   # swing - buffer
        assert plan.target_underlying == pytest.approx(105.0)       # opposing swing
        risk = 100.0 - plan.stop_underlying
        assert plan.risk_reward == pytest.approx(5.0 / risk, abs=0.01)
        assert plan.partial_levels == (pytest.approx(100.0 + risk),)
        assert plan.entry_price == pytest.approx(2.05)              # contract mid
        assert "below" in plan.invalidation

    def test_short_plan_mirrors(self):
        view = bearish_entry_view()  # swing highs 105/102, swing low 94, atr 1.0
        plan = self.planner.plan(make_signal(Direction.SHORT), view,
                                 make_put(100, -0.45), spot=100.0)
        assert plan is not None
        assert plan.stop_underlying == pytest.approx(102.0 + 0.25)  # nearest high + buffer
        assert plan.target_underlying == pytest.approx(94.0)
        assert plan.stop_underlying > 100.0 > plan.target_underlying
        assert "above" in plan.invalidation

    def test_atr_fallback_when_no_swings(self):
        view = make_view(close=100.0, atr=2.0, swings=())
        plan = self.planner.plan(make_signal(), view, make_call(100, 0.45), spot=100.0)
        assert plan.stop_underlying == pytest.approx(100.0 - 3.0)   # 1.5 * ATR
        assert plan.target_underlying == pytest.approx(100.0 + 6.0)  # 2R fallback
        assert plan.risk_reward == pytest.approx(2.0)

    def test_rejects_undefined_atr(self):
        view = make_view(atr=float("nan"))
        assert self.planner.plan(make_signal(), view, make_call(100, 0.45), 100.0) is None

    def test_rejects_dead_contract(self):
        dead = make_call(100, 0.45, bid=0.0, ask=0.0)
        assert self.planner.plan(make_signal(), bullish_entry_view(), dead, 100.0) is None
