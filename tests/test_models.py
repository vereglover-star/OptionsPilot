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
        for s in ["1m", "2m", "3m", "5m", "10m", "15m", "30m",
                  "1h", "2h", "4h", "1d", "1w", "1mo"]:
            assert str(Timeframe.from_string(s)) == s

    def test_unknown_rejected(self):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            Timeframe.from_string("7m")

    def test_month_label_does_not_collide_with_minute(self):
        # from_string lowercases; the month label must therefore be "1mo"
        assert Timeframe.from_string("1M") is Timeframe.M1
        assert Timeframe.from_string("1mo") is Timeframe.MN1

    def test_every_member_fully_wired(self):
        # Adding a Timeframe member requires a fetch spec, a history window,
        # and a cache TTL — this is what makes "add an interval later" a
        # one-line-per-layer change instead of a scattered KeyError hunt.
        from optionspilot.data.cached import CANDLE_TTL
        from optionspilot.data.yfinance_provider import _FETCH_SPEC
        from optionspilot.orchestrator import WINDOW_DAYS

        for tf in Timeframe:
            assert tf in _FETCH_SPEC, f"{tf} missing from _FETCH_SPEC"
            assert tf in WINDOW_DAYS, f"{tf} missing from WINDOW_DAYS"
            assert tf in CANDLE_TTL, f"{tf} missing from CANDLE_TTL"
            assert str(tf), f"{tf} has no label"

    def test_members_sorted_by_duration(self):
        mins = [tf.minutes for tf in Timeframe]
        assert mins == sorted(mins)


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
