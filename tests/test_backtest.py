import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from optionspilot.backtest import Backtester
from optionspilot.backtest.backtester import WARMUP_BARS, _slice_closed
from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Timeframe
from optionspilot.journal import TradeJournal
from tests.conftest import zigzag

# Permissive thresholds: the point is to exercise the machinery, not find alpha
CFG = AppConfig.model_validate({
    "engine": {
        "htf_trend_timeframes": ["1h"], "entry_timeframes": ["15m"],
        "min_confidence": 25,
    },
    "risk": {
        "min_risk_reward": 0.5, "risk_per_trade_pct": 2.0,
        "cooldown_minutes_after_loss": 0, "daily_trade_limit": 10,
    },
})


def rth_15m_frame(n_bars: int) -> pd.DataFrame:
    """A zigzagging uptrend re-indexed onto real 15m US session timestamps
    (13:30–19:45 UTC = 09:30–15:45 ET, weekdays), so the risk manager's
    trading-hours gate behaves exactly as it would live."""
    points = [100]
    level = 100.0
    rng = np.random.default_rng(9)
    while True:
        level += rng.uniform(2.5, 4.0)
        points += [round(level + 2, 2), round(level, 2)]
        df = zigzag(points, bars_per_leg=6)
        if len(df) >= n_bars:
            break
    df = df.iloc[:n_bars].copy()

    stamps = []
    day = pd.Timestamp("2026-07-06", tz="UTC")   # a Monday
    while len(stamps) < n_bars:
        if day.dayofweek < 5:
            for k in range(26):                   # 13:30 .. 19:45 UTC
                stamps.append(day + pd.Timedelta(hours=13, minutes=30 + 15 * k))
        day += pd.Timedelta(days=1)
    df.index = pd.DatetimeIndex(stamps[:n_bars], name="ts")
    return df


def hourly_from(df15: pd.DataFrame) -> pd.DataFrame:
    return df15.resample("1h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])


class TestSliceClosed:
    def test_excludes_forming_bars(self):
        idx = pd.date_range("2026-07-06 10:00", periods=3, freq="1h", tz="UTC")
        df = pd.DataFrame({"open": 1.0, "high": 1, "low": 1, "close": 1,
                           "volume": 1}, index=idx)
        now = pd.Timestamp("2026-07-06 12:30", tz="UTC")
        out = _slice_closed(df, now, Timeframe.H1)
        assert len(out) == 2                      # 12:00 bar still forming
        assert out.index[-1] == idx[1]

    def test_exact_close_included(self):
        idx = pd.date_range("2026-07-06 10:00", periods=2, freq="1h", tz="UTC")
        df = pd.DataFrame({"open": 1.0, "high": 1, "low": 1, "close": 1,
                           "volume": 1}, index=idx)
        out = _slice_closed(df, pd.Timestamp("2026-07-06 12:00", tz="UTC"),
                            Timeframe.H1)
        assert len(out) == 2


@pytest.fixture(scope="module")
def backtest_run(tmp_path_factory):
    df15 = rth_15m_frame(240)
    candles = {Timeframe.M15: df15, Timeframe.H1: hourly_from(df15)}
    journal = TradeJournal(tmp_path_factory.mktemp("bt") / "journal.db")
    rep = Backtester(CFG).run("SPY", candles, journal=journal)
    return rep, journal


class TestBacktester:
    @pytest.fixture
    def report(self, backtest_run):
        return backtest_run[0]

    def test_produces_trades(self, report):
        assert report.n_trades >= 1
        assert all(t.entry_reasons for t in report.trades)     # evidence recorded
        assert all(t.exit_reason for t in report.trades)

    def test_equity_accounting_is_consistent(self, report):
        assert report.final_equity == pytest.approx(
            report.initial_balance + sum(t.pnl for t in report.trades), abs=1.0,
        )
        assert report.net_profit == pytest.approx(
            report.final_equity - report.initial_balance, abs=0.01,
        )

    def test_metrics_are_sane(self, report):
        assert 0.0 <= report.win_rate <= 1.0
        assert report.max_drawdown_pct >= 0.0
        assert len(report.equity_curve) == 240 - WARMUP_BARS
        assert report.monthly_returns                          # at least one month
        assert report.notes                                    # limitations documented

    def test_trades_are_journaled(self, backtest_run):
        report, journal = backtest_run
        assert len(journal.all()) == report.n_trades

    def test_reports_serialize(self, report, tmp_path):
        jpath = report.save_json(tmp_path / "r.json")
        doc = json.loads(jpath.read_text(encoding="utf-8"))
        assert doc["n_trades"] == report.n_trades
        assert doc["net_profit"] == report.net_profit
        hpath = report.save_html(tmp_path / "r.html")
        html = hpath.read_text(encoding="utf-8")
        assert "Equity curve" in html and "polyline" in html

    def test_rejects_insufficient_data(self):
        df = rth_15m_frame(50)
        with pytest.raises(ValueError, match="need more than"):
            Backtester(CFG).run("SPY", {Timeframe.M15: df,
                                        Timeframe.H1: hourly_from(df)})
