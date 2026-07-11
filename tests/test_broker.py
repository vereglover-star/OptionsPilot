from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.broker import BrokerError, PaperBroker, PositionManager
from optionspilot.config.settings import BrokerConfig
from optionspilot.core.models import Direction
from tests.engine_helpers import make_call, make_plan, make_put

TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
CFG = BrokerConfig(commission_per_contract=0.65, slippage_pct=0.01)


def broker(tmp_path, cash=25_000.0) -> PaperBroker:
    return PaperBroker(CFG, tmp_path / "paper.db", starting_cash=cash)


class TestFills:
    def test_open_fills_at_ask_plus_slippage(self, tmp_path):
        b = broker(tmp_path)
        fill = b.open_position(make_plan(), quantity=2, ts=TS)
        assert fill.price == pytest.approx(2.10 * 1.01)      # ask worsened 1%
        assert fill.commission == pytest.approx(1.30)
        cost = fill.price * 200 + 1.30
        assert b.get_account().cash == pytest.approx(25_000 - cost, abs=0.01)
        pos = b.get_positions()[0]
        assert pos.quantity == 2
        assert pos.stop_current == pytest.approx(97.75)      # copied from plan
        assert pos.partials_remaining == (102.25,)

    def test_close_realizes_pnl(self, tmp_path):
        b = broker(tmp_path)
        entry = b.open_position(make_plan(), quantity=2, ts=TS)
        fill, realized = b.close_position(
            make_plan().contract.symbol, 2, bid=2.50, ts=TS + timedelta(hours=1),
            reason="target",
        )
        assert fill.price == pytest.approx(2.50 * 0.99)      # bid worsened 1%
        expected = (fill.price - entry.price) * 200 - 1.30   # exit commission
        assert realized == pytest.approx(expected, abs=0.01)
        assert b.get_positions() == []
        assert b.get_account().realized_pnl == pytest.approx(expected, abs=0.01)

    def test_partial_close_keeps_remainder(self, tmp_path):
        b = broker(tmp_path)
        b.open_position(make_plan(), quantity=4, ts=TS)
        symbol = make_plan().contract.symbol
        _, realized = b.close_position(symbol, 2, bid=2.60, ts=TS, reason="partial")
        assert realized > 0
        assert b.get_positions()[0].quantity == 2

    def test_equity_tracks_marks(self, tmp_path):
        b = broker(tmp_path)
        fill = b.open_position(make_plan(), quantity=2, ts=TS)
        symbol = make_plan().contract.symbol
        b.mark_positions({symbol: 3.00})
        acct = b.get_account()
        assert acct.equity == pytest.approx(acct.cash + 600.0, abs=0.01)


class TestGuards:
    def test_insufficient_cash_rejected(self, tmp_path):
        b = broker(tmp_path, cash=100.0)
        with pytest.raises(BrokerError, match="insufficient cash"):
            b.open_position(make_plan(), quantity=1, ts=TS)

    def test_oversell_rejected(self, tmp_path):
        b = broker(tmp_path)
        b.open_position(make_plan(), quantity=1, ts=TS)
        with pytest.raises(BrokerError, match="invalid close quantity"):
            b.close_position(make_plan().contract.symbol, 2, bid=2.5, ts=TS)

    def test_unknown_position_rejected(self, tmp_path):
        with pytest.raises(BrokerError, match="no open position"):
            broker(tmp_path).close_position("NOPE", 1, bid=1.0, ts=TS)

    def test_dead_quote_rejected(self, tmp_path):
        b = broker(tmp_path)
        dead = make_plan(contract=make_call(100, 0.45, bid=0.0, ask=0.0))
        with pytest.raises(BrokerError, match="no ask price"):
            b.open_position(dead, quantity=1, ts=TS)


class TestPersistence:
    def test_account_and_positions_survive_restart(self, tmp_path):
        b1 = broker(tmp_path)
        b1.open_position(make_plan(), quantity=2, ts=TS)
        cash_before = b1.get_account().cash
        b1.close()

        b2 = PaperBroker(CFG, tmp_path / "paper.db", starting_cash=99_999.0)
        assert b2.get_account().cash == pytest.approx(cash_before)  # not re-seeded
        pos = b2.get_positions()[0]
        assert pos.quantity == 2
        assert pos.direction is Direction.LONG
        assert pos.stop_current == pytest.approx(97.75)
        assert pos.target == pytest.approx(105.0)
        assert pos.partials_remaining == (102.25,)
        assert pos.contract.symbol == make_plan().contract.symbol

    def test_management_updates_persist(self, tmp_path):
        b1 = broker(tmp_path)
        b1.open_position(make_plan(), quantity=2, ts=TS)
        pos = b1.get_positions()[0]
        pos.stop_current = 100.0
        pos.partials_remaining = ()
        b1.update_position_management(pos)
        b1.close()

        b2 = PaperBroker(CFG, tmp_path / "paper.db", starting_cash=0.0)
        restored = b2.get_positions()[0]
        assert restored.stop_current == 100.0
        assert restored.partials_remaining == ()


class TestPositionManager:
    def setup_method(self):
        self.pm = PositionManager()

    def _position(self, tmp_path, quantity=4, direction=Direction.LONG):
        b = broker(tmp_path)
        plan = make_plan(direction=direction) if direction is Direction.LONG else \
            make_plan(direction=direction, stop=102.25, target=95.0, partials=(97.75,))
        b.open_position(plan, quantity=quantity, ts=TS)
        return b, b.get_positions()[0]

    def test_stop_hit_closes_all(self, tmp_path):
        _, pos = self._position(tmp_path)
        intents = self.pm.review(pos, spot=97.50, ts=TS)
        assert len(intents) == 1
        assert intents[0].kind == "stop" and intents[0].quantity == 4

    def test_target_hit_closes_all(self, tmp_path):
        _, pos = self._position(tmp_path)
        intents = self.pm.review(pos, spot=105.2, ts=TS)
        assert intents[0].kind == "target" and intents[0].quantity == 4

    def test_choch_invalidation(self, tmp_path):
        _, pos = self._position(tmp_path)
        intents = self.pm.review(pos, spot=100.5, ts=TS, opposing_choch=True)
        assert intents[0].kind == "invalidation" and intents[0].quantity == 4

    def test_partial_takes_half_and_moves_stop(self, tmp_path):
        _, pos = self._position(tmp_path)
        intents = self.pm.review(pos, spot=102.30, ts=TS)   # partial level 102.25
        assert intents[0].kind == "partial" and intents[0].quantity == 2
        assert pos.stop_current == pytest.approx(100.0)     # breakeven (entry spot)
        assert pos.partials_remaining == ()
        # Next review at the same spot: nothing more to do
        assert self.pm.review(pos, spot=102.30, ts=TS) == []

    def test_short_direction_mirrors(self, tmp_path):
        _, pos = self._position(tmp_path, direction=Direction.SHORT)
        assert self.pm.review(pos, spot=102.50, ts=TS)[0].kind == "stop"
        assert self.pm.review(pos, spot=94.8, ts=TS)[0].kind == "target"
        intents = self.pm.review(pos, spot=97.70, ts=TS)
        assert intents[0].kind == "partial"
        assert pos.stop_current == pytest.approx(100.0)

    def test_one_lot_skips_partial(self, tmp_path):
        _, pos = self._position(tmp_path, quantity=1)
        assert self.pm.review(pos, spot=102.30, ts=TS) == []
        assert pos.partials_remaining == ()                 # level consumed anyway

    def test_no_action_in_the_middle(self, tmp_path):
        _, pos = self._position(tmp_path)
        assert self.pm.review(pos, spot=100.5, ts=TS) == []


class TestLifecycle:
    def test_full_trade_lifecycle(self, tmp_path):
        """Open -> partial at +1R with stop to breakeven -> stop out remainder
        at breakeven: the classic 'free trade' sequence."""
        b = broker(tmp_path)
        pm = PositionManager()
        plan = make_plan()
        b.open_position(plan, quantity=4, ts=TS)
        pos = b.get_positions()[0]
        symbol = pos.contract.symbol

        # Price runs to +1R: partial fires
        [partial] = pm.review(pos, spot=102.30, ts=TS + timedelta(minutes=30))
        _, realized_partial = b.close_position(
            symbol, partial.quantity, bid=2.80, ts=TS + timedelta(minutes=30),
            reason=partial.reason,
        )
        b.update_position_management(pos)
        assert realized_partial > 0
        assert b.get_positions()[0].quantity == 2

        # Price falls back to breakeven: stop (now at entry spot) fires
        [stop] = pm.review(pos, spot=99.9, ts=TS + timedelta(hours=1))
        assert stop.kind == "stop"
        _, realized_rest = b.close_position(
            symbol, stop.quantity, bid=2.15, ts=TS + timedelta(hours=1),
            reason=stop.reason,
        )
        assert b.get_positions() == []
        total = realized_partial + realized_rest
        assert total > 0                        # partial locked in the win
        assert b.get_account().realized_pnl == pytest.approx(total, abs=0.01)
