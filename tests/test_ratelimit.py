"""Request budgeting — the gate that keeps a 25-per-day provider usable.

Reacting to a 429 is far too late when the whole day's allowance is 25
requests, so the budget is enforced *before* the request. These tests pin the
three properties that makes it trustworthy:

  1. the minute window is a real SLIDING window, not a bucket that lets 2x
     through across a boundary;
  2. the daily count survives a restart, because a desktop app restarts and a
     fresh in-memory counter would mint requests the plan never granted;
  3. nothing here can break a chart — a corrupt or unwritable ledger degrades
     to "no history", never to a failure.

Every test drives injected clocks. Nothing sleeps, and nothing waits for
midnight.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from optionspilot.data.ratelimit import (
    REFUSAL_DAILY, REFUSAL_MINUTE, QuotaStore, QuotaTracker, RateLimitPolicy,
    UNMETERED,
)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def tracker(per_minute=None, per_day=None, *, clock=None, wall=None,
            store=None, name="p") -> QuotaTracker:
    return QuotaTracker(name, RateLimitPolicy(per_minute, per_day),
                        store=store, clock=clock or FakeClock(),
                        wall_clock=wall or (lambda: datetime(2026, 7, 26,
                                                             tzinfo=timezone.utc)))


class TestUnmetered:
    """Keyless providers get a tracker that always says yes, so there is one
    code path everywhere rather than two."""

    def test_an_unmetered_tracker_always_allows(self):
        q = tracker()
        for _ in range(10_000):
            assert q.allow() == (True, "")
            q.record()

    def test_it_reports_itself_unmetered(self):
        state = tracker().state()
        assert state["metered"] is False
        assert state["remaining_today"] is None

    def test_it_contributes_no_ranking_pressure(self):
        assert tracker().pressure() == 0.0
        assert UNMETERED.metered is False


class TestDailyBudget:
    def test_it_allows_exactly_the_budget_then_refuses(self):
        q = tracker(per_day=3)
        for _ in range(3):
            assert q.allow()[0] is True
            q.record()
        allowed, reason = q.allow()
        assert allowed is False
        assert reason == REFUSAL_DAILY

    def test_remaining_counts_down(self):
        q = tracker(per_day=25)
        q.record()
        q.record()
        assert q.state()["remaining_today"] == 23
        assert q.state()["used_today"] == 2

    def test_the_count_resets_on_a_new_day(self):
        day = {"d": datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc)}
        q = tracker(per_day=2, wall=lambda: day["d"])
        q.record()
        q.record()
        assert q.allow()[0] is False
        day["d"] += timedelta(hours=2)
        assert q.allow()[0] is True
        assert q.state()["used_today"] == 0

    def test_a_provider_reported_exhaustion_beats_our_local_count(self):
        """A live 'you have run out of credits' is authoritative: our count can
        drift low when the same key is used by another install or process."""
        q = tracker(per_day=25)
        q.record()
        q.exhaust_day()
        assert q.allow() == (False, REFUSAL_DAILY)
        assert q.state()["remaining_today"] == 0

    def test_exhaust_day_is_a_no_op_without_a_daily_limit(self):
        q = tracker(per_minute=60)
        q.exhaust_day()
        assert q.allow()[0] is True


class TestMinuteWindow:
    def test_it_allows_the_burst_then_refuses(self):
        q = tracker(per_minute=3)
        for _ in range(3):
            q.record()
        assert q.allow() == (False, REFUSAL_MINUTE)

    def test_it_slides_rather_than_resetting_on_a_boundary(self):
        """A FIXED bucket lets 2x the limit through across a boundary — 8
        requests at 10:00:59 and 8 more at 10:01:00 — which is exactly the
        burst that gets an API key throttled."""
        clock = FakeClock()
        q = tracker(per_minute=3, clock=clock)
        for _ in range(3):
            q.record()
        clock.advance(30.0)                  # half a minute later: still full
        assert q.allow()[0] is False
        clock.advance(31.0)                  # the original three have aged out
        assert q.allow()[0] is True

    def test_capacity_returns_gradually_not_all_at_once(self):
        clock = FakeClock()
        q = tracker(per_minute=2, clock=clock)
        q.record()
        clock.advance(40.0)
        q.record()
        clock.advance(21.0)                  # only the FIRST has aged out
        assert q.state()["remaining_this_minute"] == 1

    def test_the_daily_refusal_takes_precedence_over_the_minute_one(self):
        """They have different remedies — tomorrow versus a moment — so the
        binding one has to be the one reported."""
        q = tracker(per_minute=10, per_day=2)
        q.record()
        q.record()
        assert q.allow()[1] == REFUSAL_DAILY


class TestPressure:
    """Pressure is what distributes load BETWEEN providers: a nearly-exhausted
    one drifts down the ranking before it is exhausted, not after."""

    def test_pressure_tracks_consumption(self):
        q = tracker(per_day=10)
        assert q.pressure() == 0.0
        for _ in range(5):
            q.record()
        assert q.pressure() == pytest.approx(0.5)
        for _ in range(5):
            q.record()
        assert q.pressure() == pytest.approx(1.0)

    def test_pressure_is_capped_at_one(self):
        q = tracker(per_day=2)
        for _ in range(50):
            q.record()
        assert q.pressure() == 1.0

    def test_a_minute_only_limit_creates_no_pressure(self):
        """A per-minute ceiling is a pacing problem, not a scarcity one — it
        recovers in seconds and should not reorder the chain."""
        q = tracker(per_minute=5)
        for _ in range(5):
            q.record()
        assert q.pressure() == 0.0


class TestPersistence:
    def test_a_restart_restores_todays_count(self, tmp_path):
        """A desktop app restarts. Without this, quitting and reopening would
        appear to grant a fresh 25 Alpha Vantage requests, and every request
        past the real limit would fail with an error the app cannot explain."""
        today = datetime(2026, 7, 26, tzinfo=timezone.utc)
        path = tmp_path / "quota.json"
        first = tracker(per_day=25, store=QuotaStore(path),
                        wall=lambda: today, name="av")
        for _ in range(5):
            first.record()

        second = tracker(per_day=25, store=QuotaStore(path),
                         wall=lambda: today, name="av")
        assert second.state()["used_today"] == 5
        assert second.state()["remaining_today"] == 20

    def test_yesterdays_count_does_not_eat_todays_budget(self, tmp_path):
        path = tmp_path / "quota.json"
        yesterday = datetime(2026, 7, 25, tzinfo=timezone.utc)
        today = datetime(2026, 7, 26, tzinfo=timezone.utc)
        old = tracker(per_day=25, store=QuotaStore(path),
                      wall=lambda: yesterday, name="av")
        for _ in range(25):
            old.record()

        fresh = tracker(per_day=25, store=QuotaStore(path),
                        wall=lambda: today, name="av")
        assert fresh.state()["used_today"] == 0
        assert fresh.allow()[0] is True

    def test_providers_do_not_share_a_count(self, tmp_path):
        path = tmp_path / "quota.json"
        store = QuotaStore(path)
        today = datetime(2026, 7, 26, tzinfo=timezone.utc)
        a = tracker(per_day=25, store=store, wall=lambda: today, name="a")
        b = tracker(per_day=25, store=store, wall=lambda: today, name="b")
        a.record()
        assert b.state()["used_today"] == 0

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        store = QuotaStore(tmp_path / "nope" / "quota.json")
        assert store.load("anything") == (0, None)

    def test_a_corrupt_file_degrades_to_no_history(self, tmp_path):
        """Losing the count costs a few over-spent requests; refusing to start
        costs the whole application."""
        path = tmp_path / "quota.json"
        path.write_text("{not json", encoding="utf-8")
        assert QuotaStore(path).load("av") == (0, None)

    def test_a_malformed_entry_is_ignored(self, tmp_path):
        path = tmp_path / "quota.json"
        path.write_text(json.dumps(
            {"version": 1, "providers": {"av": {"used": "lots"}}}),
            encoding="utf-8")
        assert QuotaStore(path).load("av") == (0, None)

    def test_an_unwritable_path_never_raises(self, tmp_path):
        """Budget accounting must never be able to break a chart."""
        store = QuotaStore(tmp_path / "quota.json")
        store.path = tmp_path / "missing-dir" / "x" / "quota.json"
        # A directory that cannot be created: writing must swallow the error.
        (tmp_path / "missing-dir").write_text("i am a file", encoding="utf-8")
        store.save("av", 3, date(2026, 7, 26))   # must not raise

    def test_the_file_is_written_atomically(self, tmp_path):
        """A crash mid-write must not leave a half-written ledger the next
        launch then refuses to parse."""
        path = tmp_path / "quota.json"
        store = QuotaStore(path)
        store.save("av", 7, date(2026, 7, 26))
        assert json.loads(path.read_text(encoding="utf-8"))["providers"]["av"]
        assert not list(tmp_path.glob("*.tmp"))


class TestReset:
    def test_reset_clears_both_windows(self):
        q = tracker(per_minute=2, per_day=2)
        q.record()
        q.record()
        assert q.allow()[0] is False
        q.reset()
        assert q.allow()[0] is True


class TestThreadSafety:
    def test_concurrent_recording_loses_no_count(self):
        import threading

        q = tracker(per_day=100_000)

        def work():
            for _ in range(200):
                q.record()

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert q.state()["used_today"] == 1600

    def test_allow_and_record_race_without_exceeding_by_more_than_the_racers(self):
        """`allow()` and `record()` are separate calls, so a burst CAN overrun
        slightly — bounded by the number of threads in flight, not unbounded.
        Documented rather than hidden: the adapter records before the network,
        which keeps the overrun to at most one request per concurrent caller."""
        import threading

        q = tracker(per_day=10)
        spent = []

        def work():
            if q.allow()[0]:
                q.record()
                spent.append(1)

        threads = [threading.Thread(target=work) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert 10 <= len(spent) <= 40


class TestBoundedMemory:
    """A tracker lives for the life of the process. Nothing in it may grow
    without a ceiling."""

    def test_the_minute_window_cannot_exceed_the_limit_it_enforces(self):
        q = tracker(per_minute=8, per_day=100_000)
        for _ in range(50_000):
            q.record()
        assert len(q._minute) <= 8

    def test_a_provider_with_no_minute_limit_tracks_no_window(self):
        """It would be a pure memory cost answering a question nobody asks."""
        q = tracker(per_day=100_000)
        for _ in range(50_000):
            q.record()
        assert len(q._minute) == 0

    def test_capping_the_window_does_not_break_the_limit(self):
        """The only question asked of the window is `len >= per_minute`, so a
        maxlen equal to the limit still refuses correctly."""
        clock = FakeClock()
        q = tracker(per_minute=3, clock=clock)
        for _ in range(100):
            q.record()
        assert q.allow() == (False, REFUSAL_MINUTE)
        clock.advance(61.0)
        assert q.allow()[0] is True
