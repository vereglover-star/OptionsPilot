from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Direction, Fill, TradeRecord
from optionspilot.journal import TradeJournal, build_trade_record
from tests.engine_helpers import make_plan

TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def make_trade(tid="t1", pnl_gross=100.0, symbol="SPY", strategy="confluence_v1",
               direction=Direction.LONG, confidence=85.0, entry_ts=TS,
               indicators=("htf_trend", "vwap")) -> TradeRecord:
    # entry 2.00, quantity 1 -> exit chosen to produce the requested gross pnl
    exit_price = 2.00 + pnl_gross / 100
    return TradeRecord(
        id=tid, symbol=symbol, contract_symbol="SPY260731C00100000",
        direction=direction, strategy=strategy, quantity=1,
        entry_ts=entry_ts, entry_price=2.00,
        exit_ts=entry_ts + timedelta(hours=1), exit_price=exit_price,
        commissions=0.0, confidence=confidence,
        entry_reasons=["+ trend up"], exit_reason="target reached",
        market_conditions={"htf_trend": "up", "dte": "21"},
        indicators_used=list(indicators),
    )


class TestJournal:
    def test_record_roundtrip(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        j.record(make_trade())
        t = j.get("t1")
        assert t is not None
        assert t.pnl == pytest.approx(100.0)
        assert t.direction is Direction.LONG
        assert t.entry_reasons == ["+ trend up"]
        assert t.market_conditions["htf_trend"] == "up"
        assert t.indicators_used == ["htf_trend", "vwap"]
        assert t.entry_ts == TS

    def test_query_filters(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        j.record(make_trade("w1", 100.0))
        j.record(make_trade("l1", -50.0))
        j.record(make_trade("q1", 30.0, symbol="QQQ",
                            entry_ts=TS + timedelta(days=1)))
        assert len(j.query(symbol="SPY")) == 2
        assert [t.id for t in j.query(wins_only=True)] == ["w1", "q1"]
        assert [t.id for t in j.query(wins_only=False)] == ["l1"]
        assert [t.id for t in j.query(start=TS + timedelta(hours=12))] == ["q1"]
        assert len(j.query(direction=Direction.SHORT)) == 0

    def test_stats(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        for tid, pnl in [("a", 100.0), ("b", 200.0), ("c", -100.0)]:
            j.record(make_trade(tid, pnl))
        s = j.stats()
        assert s["trades"] == 3 and s["wins"] == 2
        assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-3)
        assert s["profit_factor"] == pytest.approx(3.0)
        assert s["expectancy"] == pytest.approx(200 / 3, abs=0.01)

    def test_annotate(self, tmp_path):
        j = TradeJournal(tmp_path / "j.db")
        j.record(make_trade())
        j.annotate("t1", mistakes=["chased entry"], lessons=["wait for retest"])
        t = j.get("t1")
        assert t.mistakes == ["chased entry"]
        assert t.lessons == ["wait for retest"]
        with pytest.raises(KeyError):
            j.annotate("nope", mistakes=["x"])


class TestBuildTradeRecord:
    def test_aggregates_partial_exits(self):
        plan = make_plan()
        entry = Fill("o1", TS, quantity=4, price=2.10, commission=2.60)
        exits = [
            (Fill("o2", TS + timedelta(minutes=30), 2, 2.80, 1.30), "partial at 102.25"),
            (Fill("o3", TS + timedelta(hours=1), 2, 2.15, 1.30), "stop hit"),
        ]
        t = build_trade_record("id1", plan, 4, entry, exits)
        assert t.exit_price == pytest.approx((2.80 * 2 + 2.15 * 2) / 4)
        assert t.commissions == pytest.approx(2.60 + 1.30 + 1.30)
        assert t.exit_reason == "stop hit"
        assert t.exit_ts == TS + timedelta(hours=1)
        # (avg_exit - entry) * 4 * 100 - commissions
        expected = (t.exit_price - 2.10) * 400 - 5.20
        assert t.pnl == pytest.approx(expected)
        assert t.market_conditions["dte"] == "21"
        assert "hour_et" in t.market_conditions

    def test_requires_exits(self):
        entry = Fill("o1", TS, 1, 2.10, 0.65)
        with pytest.raises(ValueError, match="no exit fills"):
            build_trade_record("id", make_plan(), 1, entry, [])
