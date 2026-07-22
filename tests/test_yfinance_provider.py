from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

import optionspilot.data.yfinance_provider as yfmod
from optionspilot.core.models import OptionRight, Timeframe
from optionspilot.data.yfinance_provider import YFinanceProvider


def _bars():
    idx = pd.date_range("2026-07-17 14:30", periods=4, freq="1h", tz="UTC")
    return pd.DataFrame({
        "Open": [100.0, 101.0, 102.0, 103.0],
        "High": [101.0, 102.0, 103.0, 104.0],
        "Low": [99.0, 100.0, 101.0, 102.0],
        "Close": [100.5, 101.5, 102.5, 103.5],
        "Volume": [1000, 1100, 1200, 1300],
    }, index=idx)


class _FakeTicker:
    calls: list[str] = []

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.calls.append(symbol)

    def history(self, **kwargs):
        if self.symbol == "BRK-B":
            return _bars()
        return pd.DataFrame()

    @property
    def fast_info(self):
        if self.symbol == "BRK-B":
            return {
                "last_price": 103.5,
                "bid": 103.4,
                "ask": 103.6,
                "market_cap": 1_234_567_890,
            }
        raise LookupError(self.symbol)

    @property
    def options(self):
        if self.symbol == "BRK-B":
            return ["2026-08-21", "2026-09-18"]
        raise LookupError(self.symbol)

    def option_chain(self, expiration: str):
        if self.symbol != "BRK-B":
            raise LookupError(self.symbol)
        calls = pd.DataFrame([{
            "strike": 100.0,
            "bid": 1.0,
            "ask": 1.1,
            "lastPrice": 1.05,
            "volume": 10,
            "openInterest": 20,
            "impliedVolatility": 0.25,
        }])
        puts = pd.DataFrame([{
            "strike": 100.0,
            "bid": 0.9,
            "ask": 1.0,
            "lastPrice": 0.95,
            "volume": 12,
            "openInterest": 18,
            "impliedVolatility": 0.24,
        }])
        return SimpleNamespace(calls=calls, puts=puts)


class _FakeYF:
    Ticker = _FakeTicker


def test_symbol_alias_falls_back_to_hyphen_variant(monkeypatch):
    _FakeTicker.calls = []
    monkeypatch.setattr(yfmod, "_yf", lambda: _FakeYF())
    provider = YFinanceProvider(min_request_interval=0.0)
    start = datetime(2026, 7, 17, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    df = provider.get_candles("BRK.B", Timeframe.D1, start, end)

    assert not df.empty
    assert df.index.tz is not None
    assert _FakeTicker.calls[:2] == ["BRK.B", "BRK-B"]


def test_symbol_alias_resolution_covers_quote_chain_and_metadata(monkeypatch):
    _FakeTicker.calls = []
    monkeypatch.setattr(yfmod, "_yf", lambda: _FakeYF())
    provider = YFinanceProvider(min_request_interval=0.0)

    q = provider.get_quote("BRK.B")
    caps = provider.get_market_cap("BRK.B")
    exps = provider.get_expirations("BRK.B")
    chain = provider.get_option_chain("BRK.B", date(2026, 8, 21))

    assert q.last == 103.5 and q.symbol == "BRK.B"
    assert caps == 1_234_567_890
    assert exps == [date(2026, 8, 21), date(2026, 9, 18)]
    assert len(chain) == 2
    assert {c.right for c in chain} == {OptionRight.CALL, OptionRight.PUT}
    assert _FakeTicker.calls.count("BRK-B") >= 4


def test_intraday_history_requests_are_clamped_to_yahoo_window(monkeypatch):
    class RangeTrackingTicker:
        calls: list[tuple[datetime, datetime]] = []

        def __init__(self, symbol: str):
            self.symbol = symbol

        def history(self, **kwargs):
            self.__class__.calls.append((kwargs["start"], kwargs["end"]))
            return _bars()

        @property
        def fast_info(self):
            return {"last_price": 100.0, "bid": 99.0, "ask": 101.0}

        @property
        def options(self):
            return []

    class RangeTrackingYF:
        Ticker = RangeTrackingTicker

    monkeypatch.setattr(yfmod, "_yf", lambda: RangeTrackingYF())
    provider = YFinanceProvider(min_request_interval=0.0)
    end = datetime(2026, 7, 22, tzinfo=timezone.utc)
    start = end - timedelta(days=120)

    df = provider.get_candles("SPY", Timeframe.M15, start, end)

    assert not df.empty
    assert RangeTrackingTicker.calls[0][0] == end - timedelta(days=60)
    assert RangeTrackingTicker.calls[0][1] == end
