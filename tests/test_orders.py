from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.broker import (
    BrokerError, OrderKind, OrderManager, OrderStatus, PaperBroker, TIF,
)
from optionspilot.config.settings import BrokerConfig
from tests.engine_helpers import make_call, make_put

# Tuesday 2026-07-14 15:00 UTC = 11:00 ET
TS = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)
CFG = BrokerConfig(commission_per_contract=0.65, slippage_pct=0.01)


@pytest.fixture
def rig(tmp_path):
    broker = PaperBroker(CFG, tmp_path / "paper.db", starting_cash=25_000.0)
    om = OrderManager(broker, tmp_path / "orders.db")
    return broker, om


def quotes(spot=100.0, bid=2.00, ask=2.10):
    return (lambda underlying: spot), (lambda contract: (bid, ask))


def evaluate(om, spot=100.0, bid=2.00, ask=2.10, now=TS):
    get_spot, get_q = quotes(spot, bid, ask)
    return om.evaluate(now, get_spot, get_q)


class TestMarketOrders:
    def test_market_buy_fills_immediately(self, rig):
        broker, om = rig
        order, event = om.place(OrderKind.MARKET, "buy_to_open",
                                make_call(100, 0.45), 2, TS, spot=100.0)
        assert order.status is OrderStatus.FILLED
        assert event["event"] == "filled"
        pos = broker.get_positions()[0]
        assert pos.quantity == 2 and pos.managed_by == "manual"
        assert pos.direction.value == "long"          # call
        assert om.working() == []                     # market orders never rest

    def test_market_sell_scales_out(self, rig):
        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 4, TS)
        order, event = om.place(OrderKind.MARKET, "sell_to_close",
                                make_call(100, 0.45, bid=2.50, ask=2.60), 2,
                                TS + timedelta(minutes=5))
        assert order.status is OrderStatus.FILLED
        assert broker.get_positions()[0].quantity == 2

    def test_market_sell_without_position_rejected(self, rig):
        _, om = rig
        with pytest.raises(BrokerError, match="no open position"):
            om.place(OrderKind.MARKET, "sell_to_close", make_call(100, 0.45), 1, TS)


class TestLimitOrders:
    def test_limit_buy_waits_for_price(self, rig):
        broker, om = rig
        order, _ = om.place(OrderKind.LIMIT, "buy_to_open",
                            make_call(100, 0.45), 1, TS,
                            tif=TIF.GTC, limit_price=1.80)
        assert order.status is OrderStatus.WORKING
        assert evaluate(om, ask=2.10) == []            # above limit: no fill
        assert broker.get_positions() == []
        events = evaluate(om, ask=1.75)                # ask drops through limit
        assert events[0]["event"] == "filled"
        assert broker.get_positions()[0].quantity == 1
        assert om.working() == []

    def test_limit_sell_fills_at_or_above(self, rig):
        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS)
        om.place(OrderKind.LIMIT, "sell_to_close", make_call(100, 0.45), 1, TS,
                 tif=TIF.GTC, limit_price=2.60)
        assert evaluate(om, bid=2.40) == []
        events = evaluate(om, bid=2.65)
        assert events[0]["event"] == "filled"
        assert broker.get_positions() == []

    def test_limit_requires_price(self, rig):
        _, om = rig
        with pytest.raises(ValueError, match="limit_price"):
            om.place(OrderKind.LIMIT, "buy_to_open", make_call(100, 0.45), 1, TS)


class TestStopsAndTargets:
    def setup_position(self, om, right="call"):
        contract = make_call(100, 0.45) if right == "call" else make_put(100, -0.45)
        om.place(OrderKind.MARKET, "buy_to_open", contract, 2, TS, spot=100.0)
        return contract

    def test_stop_loss_triggers_on_underlying(self, rig):
        broker, om = rig
        self.setup_position(om)
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_call(100, 0.45), 2,
                 TS, tif=TIF.GTC, stop_level=98.0)
        assert evaluate(om, spot=99.0) == []           # above stop
        events = evaluate(om, spot=97.8, bid=1.40)
        assert events[0]["event"] == "filled"
        assert "stop_loss" in events[0]["order"]["result"] or \
               events[0]["order"]["kind"] == "stop_loss"
        assert broker.get_positions() == []

    def test_put_position_stop_is_mirrored(self, rig):
        broker, om = rig
        self.setup_position(om, right="put")
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_put(100, -0.45), 2,
                 TS, tif=TIF.GTC, stop_level=102.0)
        assert evaluate(om, spot=101.0) == []          # puts stop ABOVE
        events = evaluate(om, spot=102.5, bid=1.40)
        assert events[0]["event"] == "filled"

    def test_take_profit_triggers_opposite(self, rig):
        broker, om = rig
        self.setup_position(om)
        om.place(OrderKind.TAKE_PROFIT, "sell_to_close", make_call(100, 0.45), 2,
                 TS, tif=TIF.GTC, stop_level=105.0)
        assert evaluate(om, spot=104.0) == []
        events = evaluate(om, spot=105.2, bid=3.10)
        assert events[0]["event"] == "filled"

    def test_bracket_sl_tp_cannot_oversell(self, rig):
        """SL + TP on the full position both rest; whichever fires first wins,
        the loser auto-cancels because the position is gone."""
        broker, om = rig
        self.setup_position(om)
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_call(100, 0.45), 2,
                 TS, tif=TIF.GTC, stop_level=98.0)
        with pytest.raises(BrokerError, match="already reserved"):
            om.place(OrderKind.TAKE_PROFIT, "sell_to_close",
                     make_call(100, 0.45), 2, TS, tif=TIF.GTC, stop_level=105.0)
        # splitting the position between SL and TP is allowed
        om.cancel(om.working()[0].id)
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_call(100, 0.45), 1,
                 TS, tif=TIF.GTC, stop_level=98.0)
        om.place(OrderKind.TAKE_PROFIT, "sell_to_close", make_call(100, 0.45), 1,
                 TS, tif=TIF.GTC, stop_level=105.0)
        assert len(om.working()) == 2

    def test_exit_order_autocancels_when_position_closed(self, rig):
        broker, om = rig
        self.setup_position(om)
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_call(100, 0.45), 2,
                 TS, tif=TIF.GTC, stop_level=98.0)
        om.place(OrderKind.MARKET, "sell_to_close",
                 make_call(100, 0.45, bid=2.50), 2, TS)   # user closes manually
        events = evaluate(om, spot=100.0)
        assert events[0]["event"] == "cancelled"
        assert "position closed" in events[0]["order"]["result"]
        assert om.working() == []


class TestTrailingStops:
    def test_trails_favorable_moves_only(self, rig):
        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS,
                 spot=100.0)
        om.place(OrderKind.TRAILING_STOP, "sell_to_close", make_call(100, 0.45),
                 1, TS, tif=TIF.GTC, trail=2.0, spot=100.0)
        assert evaluate(om, spot=103.0) == []          # trail moves to 101
        assert evaluate(om, spot=105.0) == []          # trail moves to 103
        assert evaluate(om, spot=103.5) == []          # pullback above trail
        events = evaluate(om, spot=102.9, bid=2.80)    # crosses 103
        assert events[0]["event"] == "filled"
        assert broker.get_positions() == []

    def test_percent_trail(self, rig):
        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS,
                 spot=100.0)
        om.place(OrderKind.TRAILING_STOP, "sell_to_close", make_call(100, 0.45),
                 1, TS, tif=TIF.GTC, trail_pct=2.0, spot=100.0)
        assert evaluate(om, spot=110.0) == []          # trail = 107.8
        events = evaluate(om, spot=107.5, bid=4.00)
        assert events[0]["event"] == "filled"

    def test_requires_exactly_one_trail_spec(self, rig):
        _, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS)
        with pytest.raises(ValueError, match="exactly one"):
            om.place(OrderKind.TRAILING_STOP, "sell_to_close",
                     make_call(100, 0.45), 1, TS, trail=2.0, trail_pct=1.0)


class TestTimeInForce:
    def test_day_order_expires_at_close(self, rig):
        _, om = rig
        om.place(OrderKind.LIMIT, "buy_to_open", make_call(100, 0.45), 1, TS,
                 tif=TIF.DAY, limit_price=1.50)
        # 15:59 ET same day: still working
        before_close = datetime(2026, 7, 14, 19, 59, tzinfo=timezone.utc)
        assert evaluate(om, ask=2.10, now=before_close) == []
        # 16:00 ET: expired
        at_close = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
        events = evaluate(om, ask=2.10, now=at_close)
        assert events[0]["event"] == "expired"
        assert om.working() == []

    def test_gtc_survives_days(self, rig):
        _, om = rig
        om.place(OrderKind.LIMIT, "buy_to_open", make_call(100, 0.45), 1, TS,
                 tif=TIF.GTC, limit_price=1.50)
        two_days = TS + timedelta(days=2)
        assert evaluate(om, ask=2.10, now=two_days) == []
        assert len(om.working()) == 1


class TestPersistence:
    def test_working_orders_survive_restart(self, rig, tmp_path):
        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS)
        om.place(OrderKind.STOP_LOSS, "sell_to_close", make_call(100, 0.45), 1,
                 TS, tif=TIF.GTC, stop_level=98.0)
        om.close()

        om2 = OrderManager(broker, tmp_path / "orders.db")
        assert len(om2.working()) == 1
        restored = om2.working()[0]
        assert restored.kind is OrderKind.STOP_LOSS
        assert restored.stop_level == 98.0
        # and it still triggers, filling at the LIVE bid (stored bid is 0)
        events = evaluate(om2, spot=97.0, bid=1.30)
        assert events[0]["event"] == "filled"
        assert broker.get_positions() == []

    def test_history_records_lifecycle(self, rig):
        _, om = rig
        om.place(OrderKind.LIMIT, "buy_to_open", make_call(100, 0.45), 1, TS,
                 tif=TIF.GTC, limit_price=1.50)
        order_id = om.working()[0].id
        om.cancel(order_id)
        hist = om.history()
        assert any(h["id"] == order_id and h["status"] == "cancelled"
                   for h in hist)


class TestManualVsAI:
    def test_position_manager_ignores_manual_positions(self, rig):
        from optionspilot.broker import PositionManager

        broker, om = rig
        om.place(OrderKind.MARKET, "buy_to_open", make_call(100, 0.45), 1, TS,
                 spot=100.0)
        pos = broker.get_positions()[0]
        # even at absurd spots, the AI manager must not touch a manual position
        assert PositionManager().review(pos, spot=1.0, ts=TS) == []
        assert PositionManager().review(pos, spot=99999.0, ts=TS) == []
