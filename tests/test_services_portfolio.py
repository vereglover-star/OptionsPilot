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
    ET, PortfolioService, max_drawdown_pct, open_risk, pnl_windows, setup_history,
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


class FakeContract:
    def __init__(self, symbol="SPY260918C00450000"):
        self.symbol = symbol


class FakePosition:
    """Only what `open_risk` reads. Long-only, because the broker is:
    `PaperBroker.open_position` refuses `quantity < 1`."""

    def __init__(self, symbol="SPY260918C00450000", quantity=1, avg_price=3.00):
        self.contract = FakeContract(symbol)
        self.quantity = quantity
        self.avg_price = avg_price


class TestOpenRisk:
    """M3-C1. The figure no current screen states, and the one Home puts third.

    It is a MAXIMUM, and that is the whole design: every position this broker
    can hold is long, so the premium in it is the most it can lose, and the
    mark is that premium. See `OpenRiskView` for why loss-to-stop was rejected
    (it needs a delta, and the only delta a Position persists is its entry
    snapshot — a live claim from a stale greek).
    """

    def test_it_is_the_premium_at_stake(self):
        risk = open_risk([FakePosition(quantity=2, avg_price=3.00)],
                         {"SPY260918C00450000": 4.20}, 10_000.0)
        assert risk.dollars == pytest.approx(840.0)     # 4.20 * 2 * 100
        assert risk.pct_of_account == pytest.approx(8.4)
        assert risk.positions == 1 and risk.marked == 1

    def test_no_positions_is_zero_and_not_a_percentage_of_nothing(self):
        risk = open_risk([], {}, 10_000.0)
        assert risk.dollars == 0.0
        assert risk.pct_of_account == 0.0
        assert risk.positions == 0 and risk.marked == 0

    def test_a_missing_mark_falls_back_to_the_entry_price(self):
        risk = open_risk([FakePosition(quantity=1, avg_price=2.50)], {}, 10_000.0)
        assert risk.dollars == pytest.approx(250.0)
        assert risk.marked == 0, "an un-marked position must be reported as such"

    def test_a_partially_marked_book_says_how_many_were_marked(self):
        positions = [FakePosition("AAA", 1, 1.00), FakePosition("BBB", 1, 2.00)]
        risk = open_risk(positions, {"AAA": 1.50}, 10_000.0)
        assert risk.dollars == pytest.approx(350.0)     # 1.50*100 + 2.00*100
        assert risk.positions == 2 and risk.marked == 1

    def test_zero_equity_has_no_percentage_rather_than_zero_percent(self):
        """"0% of your account is at risk" is false for an account holding
        positions with no equity. Same rule as `profit_factor`: insufficient
        evidence is `None`, never a comfortable number."""
        risk = open_risk([FakePosition(quantity=1, avg_price=5.00)], {}, 0.0)
        assert risk.dollars == pytest.approx(500.0)
        assert risk.pct_of_account is None

    def test_negative_equity_also_has_no_percentage(self):
        risk = open_risk([FakePosition()], {}, -250.0)
        assert risk.pct_of_account is None

    def test_a_negative_mark_cannot_offset_a_real_exposure(self):
        """An option does not trade below zero, but a provider is allowed to be
        wrong, and a negative contribution would quietly cancel out risk that
        genuinely exists somewhere else in the sum."""
        positions = [FakePosition("AAA", 1, 1.00), FakePosition("BBB", 1, 1.00)]
        risk = open_risk(positions, {"AAA": -5.00, "BBB": 2.00}, 10_000.0)
        assert risk.dollars == pytest.approx(200.0)

    def test_it_serialises_without_infinities(self):
        """`json.dumps` emits `Infinity`/`NaN`, neither of which is valid JSON
        and both of which kill a browser parse."""
        risk = open_risk([FakePosition()], {}, 0.0)
        assert json.loads(json.dumps(risk.to_dict()))["pct_of_account"] is None

    def test_the_service_reads_the_broker_under_its_lock(self):
        broker = FakeBroker(account=FakeAccount(equity=20_000.0),
                            positions=[FakePosition(quantity=1, avg_price=4.00)],
                            marks={"SPY260918C00450000": 6.00})
        risk = build(broker).open_risk()
        assert risk.dollars == pytest.approx(600.0)
        assert risk.pct_of_account == pytest.approx(3.0)

    def test_a_broker_without_marks_still_answers(self):
        """`current_marks` is optional on the duck-typed broker — a replay or a
        backtest host may not implement it, and `open_risk` guards with
        `hasattr` exactly like `positions()` and `performance()` do.

        Written as a standalone class rather than a `FakeBroker` subclass: a
        subclass that deletes the attribute still INHERITS it, so `hasattr`
        stays True and the guard is never reached. The first version of this
        test did that and passed while testing nothing.
        """
        class Markless:
            def __init__(self, account, positions):
                self._account, self._positions = account, positions

            def get_account(self):
                return self._account

            def get_positions(self):
                return list(self._positions)

        broker = Markless(FakeAccount(equity=10_000.0),
                          [FakePosition(quantity=1, avg_price=3.00)])
        assert not hasattr(broker, "current_marks"), "the guard is not exercised"
        risk = build(broker).open_risk()
        assert risk.dollars == pytest.approx(300.0)
        assert risk.marked == 0
