from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.config.settings import RiskConfig
from optionspilot.risk import RiskManager
from tests.engine_helpers import make_call, make_plan

# 2026-07-10 is a Friday. 15:00 UTC = 11:00 ET (EDT) — inside the default
# 09:45–15:30 ET window.
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def manager(**overrides) -> RiskManager:
    return RiskManager(RiskConfig(**overrides))


class TestPositionSizing:
    def test_premium_based_size(self):
        # No delta info -> worst case premium risk: 1% of 25k = 250; 2.05*100=205 -> 1
        rm = manager()
        plan = make_plan(contract=make_call(100, 0.0), spot=0.0)
        d = rm.approve(plan, open_positions=0, now=NOW)
        assert d.approved and d.quantity == 1

    def test_delta_based_size(self):
        # delta 0.45, stop distance 2.25 -> est loss 0.45*2.25*100*1.25 = 126.56
        # budget 250 -> 1 contract; with risk 2% budget 500 -> 3
        rm = manager(risk_per_trade_pct=2.0)
        d = rm.approve(make_plan(), open_positions=0, now=NOW)
        assert d.approved and d.quantity == 3
        assert "est. loss/contract" in d.notes[0]

    def test_max_contracts_cap(self):
        rm = manager(risk_per_trade_pct=10.0, max_contracts=5)
        d = rm.approve(make_plan(), open_positions=0, now=NOW)
        assert d.approved and d.quantity == 5

    def test_budget_too_small_vetoes(self):
        rm = manager(starting_balance=1000.0)  # 1% = $10 < any option
        d = rm.approve(make_plan(), open_positions=0, now=NOW)
        assert not d.approved and "risk budget too small" in d.veto


class TestGates:
    def test_weekend_vetoed(self):
        saturday = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
        d = manager().approve(make_plan(), 0, saturday)
        assert not d.approved and "weekend" in d.veto

    def test_outside_hours_vetoed(self):
        early = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)  # 09:00 ET
        d = manager().approve(make_plan(), 0, early)
        assert not d.approved and "outside trading hours" in d.veto

    def test_daily_trade_limit(self):
        rm = manager(daily_trade_limit=2)
        rm.record_entry(NOW - timedelta(hours=1))
        rm.record_entry(NOW - timedelta(minutes=30))
        d = rm.approve(make_plan(), 0, NOW)
        assert not d.approved and "daily trade limit" in d.veto

    def test_max_open_positions(self):
        d = manager(max_open_positions=3).approve(make_plan(), 3, NOW)
        assert not d.approved and "max open positions" in d.veto

    def test_min_risk_reward(self):
        d = manager(min_risk_reward=1.5).approve(make_plan(rr=1.2), 0, NOW)
        assert not d.approved and "risk/reward" in d.veto

    def test_cooldown_after_loss(self):
        rm = manager(cooldown_minutes_after_loss=15)
        rm.record_closed_trade(NOW - timedelta(minutes=5), -50.0)
        d = rm.approve(make_plan(), 0, NOW)
        assert not d.approved and "cooldown" in d.veto
        # After the cooldown it approves again
        later = NOW + timedelta(minutes=20)
        assert rm.approve(make_plan(), 0, later).approved


class TestCircuitBreaker:
    def test_daily_loss_halts_until_next_day(self):
        rm = manager()  # 3% of 25000 = 750
        rm.record_closed_trade(NOW - timedelta(minutes=60), -400.0)   # 10:00 ET
        assert rm.approve(make_plan(), 0, NOW - timedelta(minutes=30)).approved
        rm.record_closed_trade(NOW - timedelta(minutes=20), -400.0)
        d = rm.approve(make_plan(), 0, NOW)
        assert not d.approved and "daily loss limit" in d.veto
        # Next trading day (Monday July 13) it trades again
        monday = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
        assert rm.approve(make_plan(), 0, monday).approved

    def test_consecutive_losses_halt(self):
        rm = manager(max_consecutive_losses=3, cooldown_minutes_after_loss=0)
        for i in range(3):
            rm.record_closed_trade(NOW - timedelta(hours=3 - i), -50.0)
        d = rm.approve(make_plan(), 0, NOW)
        assert not d.approved and "consecutive losses" in d.veto

    def test_win_resets_streak(self):
        rm = manager(max_consecutive_losses=3, cooldown_minutes_after_loss=0)
        rm.record_closed_trade(NOW - timedelta(hours=4), -50.0)
        rm.record_closed_trade(NOW - timedelta(hours=3), -50.0)
        rm.record_closed_trade(NOW - timedelta(hours=2), 80.0)   # win
        rm.record_closed_trade(NOW - timedelta(hours=1), -50.0)
        assert rm.approve(make_plan(), 0, NOW).approved

    def test_weekly_loss_halts_until_monday(self):
        rm = manager(cooldown_minutes_after_loss=0)  # 6% of 25k = 1500
        monday = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
        tuesday = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
        rm.record_closed_trade(monday, -700.0)   # under daily limit (750)
        rm.record_closed_trade(tuesday, -700.0)  # weekly total -1400, still ok
        assert rm.approve(make_plan(), 0, tuesday + timedelta(hours=1)).approved
        wednesday = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
        rm.record_closed_trade(wednesday, -200.0)  # weekly -1600 <= -1500
        d = rm.approve(make_plan(), 0, wednesday + timedelta(hours=1))
        assert not d.approved and "weekly loss limit" in d.veto
        next_monday = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
        assert rm.approve(make_plan(), 0, next_monday).approved

    def test_drawdown_requires_manual_reset(self):
        rm = manager()  # max drawdown 15%
        rm.update_equity(26_000.0, NOW - timedelta(days=1))   # new peak
        rm.update_equity(21_000.0, NOW)                       # -19.2% from peak
        d = rm.approve(make_plan(), 0, NOW)
        assert not d.approved and "drawdown" in d.veto
        # Time does NOT heal a drawdown halt
        much_later = NOW + timedelta(days=30)
        assert not rm.approve(make_plan(), 0, much_later).approved
        rm.reset_halt()
        assert rm.approve(make_plan(), 0, much_later + timedelta(days=-26)).approved

    def test_status_reports_halt(self):
        rm = manager()
        rm.record_closed_trade(NOW, -800.0)
        s = rm.status()
        assert s["halted"] and "daily loss" in s["halt_reason"]
