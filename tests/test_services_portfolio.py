"""PortfolioService (V0.7.0) — the statistics that moved out of `ui/server.py`.

This is the extraction with the most arithmetic in it, so the tests are mostly
about the arithmetic being *unchanged* plus the boundary conditions the original
never had a test for. Two of those boundaries are load-bearing:

  * profit factor with no losing trades, which is genuinely infinite and must
    not be serialised as `Infinity`;
  * max drawdown for an account that has only ever lost money, which has no
    recorded peak above its opening balance.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.services.portfolio import (
    ET, PortfolioService, max_drawdown_pct, pnl_windows, setup_history,
)


class FakeTrade:
    def __init__(self, pnl, entry, exit_=None, quality=None, win=None):
        self.pnl = pnl
        self.entry_ts = entry
        self.exit_ts = exit_ or entry
        self.market_conditions = {"setup_quality": quality} if quality else {}
        self.is_win = pnl > 0 if win is None else win


class FakeAccount:
    def __init__(self, cash=10000.0, equity=10000.0, realized=0.0):
        self.cash, self.equity, self.realized_pnl = cash, equity, realized


class FakeBroker:
    def __init__(self, account=None, positions=(), history=(), marks=None):
        self._account = account or FakeAccount()
        self._positions = list(positions)
        self._history = list(history)
        self._marks = marks or {}

    def get_account(self):
        return self._account

    def get_positions(self):
        return list(self._positions)

    def current_marks(self):
        return dict(self._marks)

    def equity_history(self):
        return list(self._history)


def build(broker=None, trades=(), starting=10000.0):
    return PortfolioService(broker=broker or FakeBroker(),
                            trades=lambda: list(trades),
                            starting_balance=starting,
                            lock=threading.RLock())


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc).astimezone(ET)


class TestMaxDrawdown:
    def test_peak_is_seeded_at_the_starting_balance(self):
        """An account that has only ever lost money has no recorded peak above
        its opening balance. Seeding the peak at the first SAMPLE instead would
        report a 0% drawdown for exactly the account most in trouble."""
        history = [("t1", 9000.0), ("t2", 8000.0)]
        assert max_drawdown_pct(history, 10000.0) == pytest.approx(20.0)

    def test_recovery_does_not_erase_the_worst_point(self):
        history = [("a", 12000.0), ("b", 6000.0), ("c", 12500.0)]
        assert max_drawdown_pct(history, 10000.0) == pytest.approx(50.0)

    def test_an_empty_curve_is_zero_not_an_error(self):
        assert max_drawdown_pct([], 10000.0) == 0.0

    def test_a_zero_or_negative_peak_is_skipped_rather_than_dividing(self):
        assert max_drawdown_pct([("a", -50.0)], 0.0) == 0.0


class TestPerformance:
    def test_profit_factor_is_None_with_no_losses_not_infinity(self):
        """`inf` is legitimate and illegal JSON. `json.dumps` emits a bare
        `Infinity`, and a browser's `JSON.parse` dies on it — the same trap
        `intelligence/models._finite` exists for."""
        trades = [FakeTrade(100.0, NOW), FakeTrade(50.0, NOW)]
        view = build(trades=trades).performance(NOW)
        assert view.profit_factor is None
        json.dumps(view.to_dict(), allow_nan=False)

    def test_profit_factor_is_computed_when_there_are_losses(self):
        trades = [FakeTrade(300.0, NOW), FakeTrade(-100.0, NOW)]
        assert build(trades=trades).performance(NOW).profit_factor == pytest.approx(3.0)

    def test_a_breakeven_trade_counts_as_a_loss_for_win_rate(self):
        """`pnl > 0` is a win and `pnl <= 0` is a loss — the pre-V0.7.0 rule,
        preserved deliberately. A zero-P&L trade is not a win, and rounding it
        into one would inflate the number a trader judges themselves by."""
        view = build(trades=[FakeTrade(0.0, NOW), FakeTrade(100.0, NOW)]).performance(NOW)
        assert view.win_rate == pytest.approx(0.5)

    def test_no_trades_gives_zeroes_rather_than_a_division_error(self):
        view = build().performance(NOW)
        assert view.trades == 0 and view.win_rate == 0.0
        assert view.avg_win == 0.0 and view.avg_loss == 0.0

    def test_a_broker_without_optional_methods_still_works(self):
        """`current_marks` and `equity_history` are feature-detected because
        tests and embeddings inject bare broker doubles."""
        class Bare:
            def get_account(self):
                return FakeAccount()

            def get_positions(self):
                return []

        view = build(broker=Bare()).performance(NOW)
        assert view.equity_history == [] and view.max_drawdown_pct == 0.0

    def test_equity_history_is_trimmed(self):
        history = [(f"t{i}", 10000.0 + i) for i in range(900)]
        view = build(broker=FakeBroker(history=history)).performance(NOW)
        assert len(view.equity_history) == 500

    def test_daily_pnl_uses_EXIT_time_and_the_windows_use_ENTRY_time(self):
        """Not a typo, and not changed in V0.7.0. `daily_pnl` on the metrics
        card has always windowed on exit; the today/week/month card has always
        windowed on entry. V0.7.0 moves code — restating a number the user has
        been reading for six months is a separate, deliberate decision."""
        yesterday = NOW - timedelta(days=1)
        trade = FakeTrade(500.0, entry=yesterday, exit_=NOW)
        view = build(trades=[trade]).performance(NOW)
        assert view.daily_pnl == 500.0                      # exit was today
        assert pnl_windows([trade], NOW).today == 0.0       # entry was not

    def test_view_serializes_to_primitives(self):
        json.dumps(build(trades=[FakeTrade(1.0, NOW)]).performance(NOW).to_dict(),
                   allow_nan=False)


class TestPnLWindows:
    def test_week_starts_on_monday(self):
        monday = datetime(2026, 7, 27, 10, 0, tzinfo=ET)
        sunday = monday - timedelta(days=1)
        view = pnl_windows([FakeTrade(100.0, sunday)], monday)
        assert view.week == 0.0        # last week
        assert view.month == 100.0     # same month

    def test_month_starts_on_the_first(self):
        first = datetime(2026, 7, 1, 10, 0, tzinfo=ET)
        view = pnl_windows([FakeTrade(100.0, first - timedelta(days=1))], first)
        assert view.month == 0.0

    def test_no_trades_is_three_zeroes(self):
        view = pnl_windows([], NOW)
        assert (view.today, view.week, view.month) == (0.0, 0.0, 0.0)


class TestSetupHistory:
    def test_a_quality_with_no_trades_is_absent_not_zero(self):
        """Absence and a 0% win rate are different claims. `intelligence/`
        treats insufficient evidence as a first-class answer; so does this."""
        history = setup_history([FakeTrade(10.0, NOW, quality="A")])
        assert set(history) == {"A"}

    def test_trades_with_no_recorded_quality_are_ignored(self):
        assert setup_history([FakeTrade(10.0, NOW)]) == {}

    def test_win_rate_is_measured_not_assumed(self):
        trades = [FakeTrade(10.0, NOW, quality="B", win=True),
                  FakeTrade(-10.0, NOW, quality="B", win=False),
                  FakeTrade(-10.0, NOW, quality="B", win=False)]
        assert setup_history(trades)["B"] == {"trades": 3, "win_rate": 0.333}
