"""The fact adapter — the single join across journal, experience and coach.

The tests that matter most here are the tolerance ones. `build_facts` reads a
user-editable JSON directory and a SQLite payload written by an older build, and
its contract is that it never raises: a record it cannot parse costs the user
that record, not their dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Direction, TradeRecord
from optionspilot.intelligence.facts import ET, build_facts

from tests.intelligence_helpers import review


@dataclass
class FakeExperience:
    """A duck-typed stand-in for ExperienceRecord. Deliberately NOT the real
    class: `facts.py` reads its sources structurally so the intelligence layer
    can stay below them in the layering, and this test asserts that property
    holds rather than quietly importing the thing it must not depend on."""

    trade_id: str = "T1"
    symbol: str = "spy"
    direction: str = "long"
    strategy: str = "confluence_v1"
    managed_by: str = "manual"
    quantity: int = 2
    entry_ts: datetime = datetime(2026, 3, 3, 14, 30, tzinfo=timezone.utc)
    exit_ts: datetime = datetime(2026, 3, 3, 15, 30, tzinfo=timezone.utc)
    entry_price: float = 2.5
    exit_price: float = 3.5
    pnl: float = 200.0
    is_win: bool = True
    hold_minutes: float = 60.0
    exit_reason: str = "target: reached"
    return_pct: float = 40.0
    hour_et: int | None = 9
    minute_et: int | None = 30
    market_session: str = "regular"
    confidence_entry: float = 72.0
    setup_quality: str = "good"
    market_regime: str = "trending-up/low-vol"
    htf_trend: str = "up"
    timeframe: str = "15m"
    risk_reward: float = 2.0
    rsi: float = 61.0
    adx: float = 28.0
    rvol: float = 1.4
    iv: float = 0.42
    delta: float = 0.5
    dte: int = 14
    spread_pct: float = 0.03
    risk_multiple: float | None = 0.9
    mistakes: list | None = None
    lessons: list | None = None
    evidence_names: list | None = None


def _trade(trade_id="J1", pnl_price=3.0) -> TradeRecord:
    return TradeRecord(
        id=trade_id, symbol="QQQ", contract_symbol="QQQ260320C00400000",
        direction=Direction.LONG, strategy="manual", quantity=1,
        entry_ts=datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc),
        entry_price=2.0,
        exit_ts=datetime(2026, 3, 4, 16, 0, tzinfo=timezone.utc),
        exit_price=pnl_price, commissions=1.0, confidence=55.0,
        entry_reasons=["a"], exit_reason="manual: closed",
        market_conditions={"dte": "10", "htf_trend": "down"},
        indicators_used=["rsi"],
    )


class TestBuildFromExperience:
    def test_carries_the_rich_context(self):
        facts = build_facts(experiences=[FakeExperience()])
        assert len(facts) == 1
        f = facts.facts[0]
        assert f.symbol == "SPY"          # normalised to upper case
        assert (f.iv, f.delta, f.dte, f.adx) == (0.42, 0.5, 14, 28.0)
        assert f.market_regime == "trending-up/low-vol"

    def test_outlay_is_premium_times_contracts_times_100(self):
        f = build_facts(experiences=[FakeExperience()]).facts[0]
        assert f.outlay == pytest.approx(2.5 * 100 * 2)

    def test_calendar_fields_are_exchange_time_not_utc(self):
        """A 14:30 UTC entry in March is 09:30 ET. Bucketing it as hour 14 would
        put the opening bell in the middle of the afternoon."""
        f = build_facts(experiences=[FakeExperience()]).facts[0]
        assert f.hour_et == 9
        assert f.weekday == "Tuesday"
        assert f.entry_date == "2026-03-03"

    def test_timestamp_is_the_fallback_when_context_hour_is_missing(self):
        """A context snapshot can be absent but is never wrong, so it wins —
        and the timestamp fills in when it isn't there."""
        rec = FakeExperience(hour_et=None, minute_et=None)
        f = build_facts(experiences=[rec]).facts[0]
        assert f.hour_et == rec.entry_ts.astimezone(ET).hour

    def test_naive_timestamps_are_treated_as_utc(self):
        rec = FakeExperience(entry_ts=datetime(2026, 3, 3, 14, 30),
                             exit_ts=datetime(2026, 3, 3, 15, 30))
        f = build_facts(experiences=[rec]).facts[0]
        assert f.entry_ts.tzinfo is not None


class TestReviewJoin:
    def test_order_observations_are_read_from_the_review(self):
        """had_stop / widened_stop / had_target come from findings the coach
        already computed off the real order history — the intelligence layer
        does not re-derive them, and does not depend on OrderManager."""
        facts = build_facts(experiences=[FakeExperience()],
                            reviews=[review("T1", had_stop=False, widened=True,
                                            had_target=False)])
        f = facts.facts[0]
        assert (f.had_stop, f.widened_stop, f.had_target) == (False, True, False)
        assert f.reviewed is True

    def test_widened_stop_is_false_when_only_one_stop_ever_existed(self):
        """A review that assessed placement but never had to assess discipline
        did not widen a stop. The absence of the finding is information."""
        doc = review("T1")
        doc["during"] = [d for d in doc["during"] if d["check"] != "stop discipline"]
        f = build_facts(experiences=[FakeExperience()], reviews=[doc]).facts[0]
        assert f.had_stop is True
        assert f.widened_stop is False

    def test_unreviewed_trade_has_tri_state_none_not_false(self):
        """False means 'observed, and there was no stop'. None means 'nobody
        looked'. Collapsing them would accuse an unreviewed trader of trading
        without stops."""
        f = build_facts(experiences=[FakeExperience()]).facts[0]
        assert f.had_stop is None
        assert f.reviewed is False

    def test_review_r_multiple_wins_over_the_experience_row(self):
        facts = build_facts(experiences=[FakeExperience(risk_multiple=0.9)],
                            reviews=[review("T1", r_multiple=2.5)])
        assert facts.facts[0].r_multiple == 2.5

    def test_experience_r_multiple_used_when_the_review_has_none(self):
        facts = build_facts(experiences=[FakeExperience(risk_multiple=0.9)],
                            reviews=[review("T1", r_multiple=None)])
        assert facts.facts[0].r_multiple == 0.9

    def test_category_scores_are_carried(self):
        facts = build_facts(
            experiences=[FakeExperience()],
            reviews=[review("T1", categories={"Entry Quality": 44})])
        assert facts.facts[0].category_scores["Entry Quality"] == 44

    def test_mistakes_are_the_union_of_both_sources(self):
        facts = build_facts(
            experiences=[FakeExperience(mistakes=["no_stop"])],
            reviews=[review("T1", mistakes=["chased_entry", "no_stop"])])
        assert facts.facts[0].mistakes == ("chased_entry", "no_stop")


class TestJournalFallback:
    def test_journal_only_trade_is_included(self):
        facts = build_facts(trades=[_trade()])
        assert len(facts) == 1
        assert facts.facts[0].symbol == "QQQ"

    def test_experience_wins_when_a_trade_is_in_both(self):
        """The journal row is a strict subset of the experience row, so
        preferring it would silently drop every indicator."""
        facts = build_facts(experiences=[FakeExperience(trade_id="X")],
                            trades=[_trade("X")])
        assert len(facts) == 1
        assert facts.facts[0].iv == 0.42      # only the experience row has this

    def test_journal_conditions_are_mined_for_context(self):
        f = build_facts(trades=[_trade()]).facts[0]
        assert f.dte == 10
        assert f.htf_trend == "down"

    def test_journal_pnl_includes_commissions(self):
        f = build_facts(trades=[_trade(pnl_price=3.0)]).facts[0]
        assert f.pnl == pytest.approx((3.0 - 2.0) * 1 * 100 - 1.0)


class TestOrderingAndDeduplication:
    def test_sorted_chronologically_with_a_stable_tiebreak(self):
        """Streaks, revenge windows and equity curves are all sequence
        sensitive; two trades sharing a timestamp must order the same way on
        every run or those analyses stop being reproducible."""
        ts = datetime(2026, 3, 3, 14, 0, tzinfo=timezone.utc)
        recs = [FakeExperience(trade_id="B", entry_ts=ts),
                FakeExperience(trade_id="A", entry_ts=ts),
                FakeExperience(trade_id="C", entry_ts=ts - timedelta(days=1))]
        ids = [f.trade_id for f in build_facts(experiences=recs)]
        assert ids == ["C", "A", "B"]

    def test_a_trade_is_never_counted_twice(self):
        facts = build_facts(experiences=[FakeExperience(trade_id="D")],
                            trades=[_trade("D"), _trade("E")])
        assert sorted(f.trade_id for f in facts) == ["D", "E"]


class TestCorruptInput:
    """`build_facts` must survive anything a hand-edited data directory can
    contain. Every case below is skipped and counted, never raised."""

    def test_missing_timestamps_are_skipped(self):
        facts = build_facts(experiences=[FakeExperience(entry_ts=None)])
        assert len(facts) == 0
        assert facts.skipped == 1

    def test_missing_trade_id_is_skipped(self):
        facts = build_facts(experiences=[FakeExperience(trade_id="")])
        assert len(facts) == 0 and facts.skipped == 1

    def test_unparseable_timestamp_string_is_skipped(self):
        facts = build_facts(experiences=[FakeExperience(entry_ts="not a date")])
        assert len(facts) == 0 and facts.skipped == 1

    def test_non_dict_reviews_are_skipped_not_fatal(self):
        facts = build_facts(experiences=[FakeExperience()],
                            reviews=["garbage", 42, None, {"no": "trade_id"}])
        assert len(facts) == 1
        assert facts.facts[0].reviewed is False

    def test_junk_numeric_fields_become_none_not_zero(self):
        """A fabricated 0.0 delta would turn a trade with no recorded delta
        into a 'lottery ticket' finding."""
        rec = FakeExperience(delta="", iv="n/a", dte=None, rsi=float("nan"))
        f = build_facts(experiences=[rec]).facts[0]
        assert (f.delta, f.iv, f.dte, f.rsi) == (None, None, None, None)

    def test_malformed_review_sections_are_tolerated(self):
        doc = review("T1")
        doc["during"] = "not a list"
        doc["categories"] = [None, 5, {"name": "Entry Quality", "score": "x"}]
        f = build_facts(experiences=[FakeExperience()], reviews=[doc]).facts[0]
        assert f.had_stop is None
        assert f.category_scores == {}

    def test_everything_broken_still_returns_a_valid_factset(self):
        facts = build_facts(experiences=[None, "x", 7],
                            trades=[None], reviews=[None])
        assert len(facts) == 0
        assert facts.skipped >= 4
        assert isinstance(facts.notes, tuple)


class TestNotes:
    def test_partial_review_coverage_is_reported_to_the_user(self):
        """A smaller denominator the user can see beats a silent one."""
        recs = [FakeExperience(trade_id=f"T{i}") for i in range(4)]
        facts = build_facts(experiences=recs, reviews=[review("T0")])
        assert any("no process review" in note for note in facts.notes)

    def test_skipped_records_are_reported(self):
        facts = build_facts(experiences=[FakeExperience(), FakeExperience(trade_id="")])
        assert any("could not be read" in note for note in facts.notes)

    def test_no_notes_when_everything_is_clean(self):
        facts = build_facts(experiences=[FakeExperience()], reviews=[review("T1")])
        assert facts.notes == ()
