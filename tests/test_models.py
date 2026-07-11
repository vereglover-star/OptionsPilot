from datetime import date, datetime, timezone

import pytest

from optionspilot.core.models import (
    Candle, Direction, OptionContract, OptionRight, Quote, Timeframe,
    TradeRecord,
)

TS = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


class TestCandle:
    def test_valid(self):
        c = Candle(TS, 100, 102, 99, 101, 5000)
        assert c.is_bullish and c.range == 3 and c.body == 1

    def test_rejects_naive_timestamp(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Candle(datetime(2026, 1, 5), 100, 102, 99, 101, 5000)

    def test_rejects_inconsistent_ohlc(self):
        with pytest.raises(ValueError, match="Inconsistent OHLC"):
            Candle(TS, 100, 99, 98, 101, 5000)  # high below open


class TestTimeframe:
    def test_roundtrip(self):
        for s in ["1m", "5m", "15m", "1h", "4h", "1d"]:
            assert str(Timeframe.from_string(s)) == s

    def test_unknown_rejected(self):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            Timeframe.from_string("3m")


class TestQuote:
    def test_spread_math(self):
        q = Quote("SPY", TS, bid=99.0, ask=101.0, last=100.0)
        assert q.mid == 100.0 and q.spread == 2.0
        assert q.spread_pct == pytest.approx(0.02)


class TestOptionContract:
    def test_occ_symbol(self):
        c = OptionContract("SPY", date(2026, 9, 18), 450.0, OptionRight.CALL)
        assert c.symbol == "SPY260918C00450000"

    def test_dte_and_liquidity(self):
        c = OptionContract("SPY", date(2026, 9, 18), 450.0, OptionRight.PUT,
                           bid=2.00, ask=2.10)
        assert c.dte(date(2026, 9, 8)) == 10
        assert c.spread_pct == pytest.approx(0.10 / 2.05)


class TestTradeRecord:
    def test_pnl(self):
        t = TradeRecord(
            id="t1", symbol="SPY", contract_symbol="SPY260918C00450000",
            direction=Direction.LONG, strategy="trend", quantity=2,
            entry_ts=TS, entry_price=2.00,
            exit_ts=TS.replace(hour=16, minute=0), exit_price=2.50, commissions=2.60,
            confidence=85.0,
        )
        # (2.50 - 2.00) * 2 contracts * 100 - 2.60 commissions
        assert t.pnl == pytest.approx(97.40)
        assert t.is_win
        assert t.hold_minutes == 90
