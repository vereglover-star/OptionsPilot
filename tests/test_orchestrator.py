import json
from datetime import date, datetime, timedelta, timezone

import pytest

from optionspilot.analysis.options_metrics import bs_greeks
from optionspilot.config.settings import AppConfig, NotifyConfig
from optionspilot.core.models import (
    OptionContract, OptionRight, Quote, Timeframe, utcnow,
)
from optionspilot.data.base import MarketDataProvider
from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from tests.conftest import zigzag
from tests.test_notify import CollectingNotifier

# Friday 2026-07-10 15:00 UTC = 11:00 ET — inside the trading window
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)

CFG = AppConfig.model_validate({
    "data": {"watchlist": ["SPY"], "timeframes": ["1h", "5m"]},
    "engine": {"htf_trend_timeframes": ["1h"], "entry_timeframes": ["5m"],
               "min_confidence": 25},
    # cooldown left at a realistic 15m: it is what prevents instant re-entry
    # after a stop-out (verified by test_stop_out_closes_and_journals)
    "risk": {"min_risk_reward": 0.5, "cooldown_minutes_after_loss": 15},
})


class FakeProvider(MarketDataProvider):
    """Deterministic provider: preloaded candles, controllable spot, and a
    synthetic liquid chain built once so contract symbols stay stable."""

    name = "fake"

    def __init__(self, candles_by_tf, spot: float, today: date):
        self._candles = candles_by_tf
        self.spot = spot
        self._expiration = today + timedelta(days=21)
        # strikes fixed at construction so contract symbols stay stable;
        # prices recomputed from the CURRENT spot on every chain fetch,
        # like a real market
        self._strikes = [round(spot + k, 0) for k in range(-6, 7)]

    def _build_chain(self):
        t = 21 / 365
        chain = []
        for strike in self._strikes:
            for right in (OptionRight.CALL, OptionRight.PUT):
                g = bs_greeks(self.spot, strike, t, 0.20, right)
                price = max(g.price, 0.05)
                chain.append(OptionContract(
                    "SPY", self._expiration, strike, right,
                    bid=round(price * 0.99, 2), ask=round(price * 1.01, 2),
                    last=round(price, 2), volume=2000, open_interest=5000,
                    implied_volatility=0.20, delta=g.delta, gamma=g.gamma,
                    theta=g.theta, vega=g.vega,
                ))
        return chain

    def get_candles(self, symbol, timeframe, start, end):
        return self._candles[timeframe]

    def get_quote(self, symbol):
        return Quote(symbol, utcnow(), bid=self.spot, ask=self.spot, last=self.spot)

    def get_expirations(self, symbol):
        return [self._expiration]

    def get_option_chain(self, symbol, expiration):
        return self._build_chain()


def bullish_candles():
    df5 = zigzag([100, 104, 102, 107, 105, 111, 108, 114], bars_per_leg=8,
                 freq="5min", start="2026-07-10 09:00")
    df1h = zigzag([90, 98, 94, 104, 100, 112], bars_per_leg=9, freq="1h",
                  start="2026-07-07 09:00")
    return {Timeframe.M5: df5, Timeframe.H1: df1h}


@pytest.fixture
def rig(tmp_path):
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    provider = FakeProvider(candles, spot, NOW.date())
    sink = CollectingNotifier()
    orch = Orchestrator(
        CFG, provider=provider,
        notifier=NotificationCenter(NotifyConfig(), [sink]),
        data_dir=tmp_path,
    )
    return orch, provider, sink, tmp_path


class TestCycle:
    def test_opens_position_on_strong_signal(self, rig):
        orch, provider, sink, tmp = rig
        summary = orch.run_cycle(NOW)
        assert len(summary["opened"]) == 1
        assert summary["opened"][0]["symbol"] == "SPY"
        assert len(orch.broker.get_positions()) == 1
        assert [e.kind for e in sink.events] == ["trade_opened"]
        # journal context persisted for restart safety
        metas = json.loads((tmp / "state" / "open_trades.json").read_text())
        assert len(metas) == 1
        meta = next(iter(metas.values()))
        assert meta["confidence"] > 25 and meta["entry_reasons"]

    def test_does_not_double_enter_held_symbol(self, rig):
        orch, provider, sink, _ = rig
        orch.run_cycle(NOW)
        summary2 = orch.run_cycle(NOW + timedelta(minutes=1))
        assert summary2["opened"] == []
        assert len(orch.broker.get_positions()) == 1

    def test_stop_out_closes_and_journals(self, rig):
        orch, provider, sink, tmp = rig
        orch.run_cycle(NOW)
        position = orch.broker.get_positions()[0]
        provider.spot = position.stop_current - 1.0

        summary = orch.run_cycle(NOW + timedelta(minutes=5))
        assert orch.broker.get_positions() == []
        assert len(summary["closed"]) == 1
        trades = orch.journal.all()
        assert len(trades) == 1
        assert "stop hit" in trades[0].exit_reason
        assert trades[0].entry_reasons                     # context survived
        assert trades[0].pnl < 0
        assert any(e.kind == "trade_closed" for e in sink.events)
        # meta cleaned up
        metas = json.loads((tmp / "state" / "open_trades.json").read_text())
        assert metas == {}
        assert orch.risk.status()["consecutive_losses"] == 1

    def test_target_exit_wins(self, rig):
        orch, provider, sink, _ = rig
        orch.run_cycle(NOW)
        position = orch.broker.get_positions()[0]
        provider.spot = position.target + 1.0
        orch.run_cycle(NOW + timedelta(minutes=5))
        # target may fire a partial first depending on quantity; keep cycling
        for i in range(3):
            if not orch.broker.get_positions():
                break
            orch.run_cycle(NOW + timedelta(minutes=10 + i * 5))
        trades = orch.journal.all()
        assert trades and trades[-1].pnl > 0

    def test_halt_notified_once(self, rig):
        orch, provider, sink, _ = rig
        orch.risk.record_closed_trade(NOW - timedelta(hours=1), -5000.0)  # breach
        orch.run_cycle(NOW)
        orch.run_cycle(NOW + timedelta(minutes=1))
        halt_events = [e for e in sink.events if e.kind == "risk_limit"]
        assert len(halt_events) == 1
        assert "HALTED" in halt_events[0].title


class TestRestart:
    def test_open_trade_context_survives_restart(self, rig):
        orch, provider, sink, tmp = rig
        orch.run_cycle(NOW)
        assert len(orch.broker.get_positions()) == 1

        # simulate a process restart: brand-new orchestrator, same data dir
        sink2 = CollectingNotifier()
        orch2 = Orchestrator(
            CFG, provider=provider,
            notifier=NotificationCenter(NotifyConfig(), [sink2]),
            data_dir=tmp,
        )
        assert len(orch2.broker.get_positions()) == 1
        assert len(orch2._metas) == 1

        position = orch2.broker.get_positions()[0]
        provider.spot = position.stop_current - 1.0
        orch2.run_cycle(NOW + timedelta(minutes=10))
        trades = orch2.journal.all()
        assert len(trades) == 1
        assert trades[0].entry_reasons        # reasons survived the restart


class TestExtras:
    def test_market_open(self):
        assert Orchestrator.market_open(NOW)
        saturday = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
        assert not Orchestrator.market_open(saturday)
        after_hours = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)  # 17:00 ET
        assert not Orchestrator.market_open(after_hours)

    def test_parallel_fetch_returns_all_frames_and_reports_progress(self, rig):
        orch, provider, sink, _ = rig
        seen = []
        out = orch.fetch_watchlist_candles(
            ["SPY", "QQQ"], on_symbol=lambda s, frames: seen.append(s))
        assert set(out) == {"SPY", "QQQ"}
        for frames in out.values():
            assert set(frames) == {Timeframe.H1, Timeframe.M5}
            assert not frames[Timeframe.M5].empty
        assert sorted(seen) == ["QQQ", "SPY"]   # one callback per symbol

    def test_run_cycle_accepts_prefetched_candles(self, rig):
        orch, provider, sink, _ = rig
        prefetched = orch.fetch_watchlist_candles(["SPY"])
        provider._candles = {}   # any further fetch would now KeyError
        summary = orch.run_cycle(NOW, candles=prefetched)
        assert len(summary["opened"]) == 1

    def test_fetch_failure_yields_empty_frame_not_crash(self, rig):
        orch, provider, sink, _ = rig

        def boom(*a, **k):
            raise ConnectionError("network down")
        provider.get_candles = boom
        out = orch.fetch_watchlist_candles(["SPY"])
        assert out["SPY"][Timeframe.M5].empty

    def test_large_move_notified_and_deduped(self, rig):
        orch, provider, sink, _ = rig
        df5 = orch.provider._candles[Timeframe.M5]
        last = df5.index[-1]
        df5.loc[last, ["high", "low", "volume"]] = [
            df5.loc[last, "close"] + 5.0, df5.loc[last, "close"] - 5.0, 25_000.0,
        ]
        df5.loc[last, "open"] = df5.loc[last, "close"]
        orch.run_cycle(NOW)
        orch.run_cycle(NOW + timedelta(minutes=1))
        moves = [e for e in sink.events if e.kind == "large_move"]
        assert len(moves) == 1
        assert "SPY" in moves[0].title
