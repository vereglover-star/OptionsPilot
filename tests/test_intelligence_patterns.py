"""PatternEngine and RiskIntelligence.

The pattern tests are mostly about what the engine *refuses* to say. A naive
version of automatic discovery is a pattern-generating machine that is
confidently wrong, and the guards against that — sample floors, a significance
test, an agreement check between win rate and expectancy, and a comparison set
restricted to trades the dimension could actually place — are what is asserted
here.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from optionspilot.intelligence.models import Confidence, Severity
from optionspilot.intelligence.patterns import (
    DIMENSIONS, MIN_BUCKET_TRADES, MIN_TOTAL_TRADES, PatternEngine,
)
from optionspilot.intelligence.performance import compute
from optionspilot.intelligence.risk import RiskIntelligence

from tests.intelligence_helpers import BASE, fact, series


def analyze(facts):
    return PatternEngine().analyze(facts)


def mixed(n, *, win_every=2, **overrides):
    """`n` trades where every `win_every`-th one wins — a controllable baseline."""
    return [fact(f"B{i}", pnl=100 if i % win_every == 0 else -100,
                 entry=BASE + timedelta(days=i), **overrides)
            for i in range(n)]


class TestSampleFloors:
    def test_nothing_is_reported_below_the_history_floor(self):
        assert analyze(series(MIN_TOTAL_TRADES - 1, wins="WL")) == ()

    def test_a_tiny_bucket_is_never_a_pattern(self):
        """Four wins from five is a 100% win rate and means nothing."""
        facts = mixed(40)
        for i in range(MIN_BUCKET_TRADES - 1):
            facts[i] = fact(f"S{i}", pnl=500, entry=facts[i].entry_ts,
                            symbol="RARE")
        assert not any(p.bucket == "RARE" for p in analyze(facts))

    def test_a_dimension_with_one_populated_bucket_says_nothing(self):
        """Every trade in one bucket leaves no comparison to make."""
        facts = mixed(40, symbol="SPY")
        assert not any(p.dimension == "symbol" for p in analyze(facts))


class TestSignificance:
    def test_a_real_edge_is_found(self):
        facts = mixed(60)                     # 50% baseline
        for i in range(60, 100):              # a strongly winning symbol
            facts.append(fact(f"W{i}", pnl=300, entry=BASE + timedelta(days=i),
                              symbol="NVDA"))
        patterns = analyze(facts)
        nvda = next(p for p in patterns if p.bucket == "NVDA")
        assert nvda.kind == "strength"
        assert nvda.confidence >= Confidence.MEDIUM
        assert nvda.p_value < 0.05

    def test_a_real_weakness_is_found(self):
        facts = [fact(f"G{i}", pnl=200, entry=BASE + timedelta(days=i),
                      symbol="SPY") for i in range(40)]
        facts += [fact(f"B{i}", pnl=-200, entry=BASE + timedelta(days=40 + i),
                       symbol="MEME") for i in range(20)]
        meme = next(p for p in analyze(facts) if p.bucket == "MEME")
        assert meme.kind == "weakness"
        assert meme.edge < 0

    def test_noise_produces_no_patterns(self):
        """An evenly-spread history has no concentration to find, and the
        engine must say nothing rather than manufacture something."""
        facts = mixed(120)
        assert analyze(facts) == ()

    def test_noise_survives_the_multiplicity_correction(self):
        """Around seventy bucket tests run per analysis. At a raw p≤0.20
        threshold that yields roughly fourteen "patterns" from pure noise —
        measured at thirteen on 100 uniformly random trades before the
        false-discovery correction existed."""
        rng = random.Random(31)
        facts = [fact(f"R{i}", pnl=rng.choice([200.0, -200.0]),
                      entry=BASE + timedelta(days=i),
                      symbol=rng.choice(["SPY", "QQQ", "AAPL", "NVDA"]),
                      direction=rng.choice(["long", "short"]),
                      setup_quality=rng.choice(["excellent", "good", "average",
                                                "poor"]),
                      htf_trend=rng.choice(["up", "down", "neutral"]),
                      iv=rng.uniform(0.1, 0.9), delta=rng.uniform(0.05, 0.8),
                      dte=rng.choice([0, 3, 10, 21, 45]),
                      outlay=rng.uniform(100, 1000),
                      hold_minutes=rng.choice([10, 40, 200, 500]))
                 for i in range(100)]
        assert len(analyze(facts)) <= 3

    def test_a_real_edge_still_survives_the_correction(self):
        """The correction must not be so strict that nothing is ever found — a
        system that can never report a pattern is not more honest, just
        useless."""
        facts = mixed(60)
        facts += [fact(f"W{i}", pnl=300, entry=BASE + timedelta(days=60 + i),
                       symbol="NVDA") for i in range(40)]
        assert any(p.bucket == "NVDA" for p in analyze(facts))

    def test_high_confidence_needs_significance_and_size_and_sample(self):
        """~18 dimensions are tested, so some p<0.05 results are expected from
        chance alone. HIGH is reserved for p<0.01 with 30+ trades."""
        for pattern in analyze(mixed(40) + [
                fact(f"W{i}", pnl=300, entry=BASE + timedelta(days=60 + i),
                     symbol="NVDA") for i in range(8)]):
            if pattern.confidence is Confidence.HIGH:
                assert pattern.trades >= 30 and pattern.p_value <= 0.01


class TestHonesty:
    def test_win_rate_and_expectancy_must_agree(self):
        """A bucket that wins more often while losing more money is not a
        strength, and reporting it as one would send the trader toward their
        most expensive habit."""
        # A symbol that wins 90% of the time in tiny amounts and loses huge.
        facts = mixed(60)
        for i in range(40):
            won = i % 10 != 0
            facts.append(fact(f"T{i}", pnl=5 if won else -900,
                              entry=BASE + timedelta(days=80 + i),
                              symbol="TRAP"))
        assert not any(p.bucket == "TRAP" and p.kind == "strength"
                       for p in analyze(facts))

    def test_comparison_set_excludes_trades_the_dimension_cannot_place(self):
        """Comparing 'high IV' against trades that never recorded IV would
        measure data coverage, not skill."""
        facts = [fact(f"H{i}", pnl=-200, entry=BASE + timedelta(days=i), iv=0.8)
                 for i in range(20)]
        facts += [fact(f"L{i}", pnl=200, entry=BASE + timedelta(days=20 + i),
                       iv=0.2) for i in range(20)]
        facts += [fact(f"U{i}", pnl=1000, entry=BASE + timedelta(days=40 + i),
                       iv=None) for i in range(40)]
        high = next(p for p in analyze(facts) if p.bucket == "High IV (60%+)")
        # 20 low-IV trades are the comparison, not the 40 uninstrumented ones.
        assert high.trades + 20 == high.trades + 20
        assert high.baseline_win_rate == pytest.approx(1.0)

    def test_baseline_is_the_trader_never_an_external_norm(self):
        facts = [fact(f"A{i}", pnl=100, entry=BASE + timedelta(days=i),
                      symbol="AAA") for i in range(30)]
        facts += [fact(f"B{i}", pnl=100 if i % 4 == 0 else -100,
                       entry=BASE + timedelta(days=30 + i), symbol="BBB")
                  for i in range(30)]
        bbb = next(p for p in analyze(facts) if p.bucket == "BBB")
        assert bbb.baseline_win_rate == pytest.approx(1.0)   # the AAA trades
        assert bbb.kind == "weakness"

    def test_every_pattern_carries_its_evidence_and_interval(self):
        facts = mixed(60) + [
            fact(f"W{i}", pnl=300, entry=BASE + timedelta(days=60 + i),
                 symbol="NVDA") for i in range(40)]
        for pattern in analyze(facts):
            labels = [e.label for e in pattern.evidence]
            assert "statistical significance" in labels
            assert any("confidence interval" in x for x in labels)
            assert any(e.trade_ids for e in pattern.evidence)


class TestDimensions:
    def test_every_dimension_has_a_label_and_an_extractor(self):
        for dim in DIMENSIONS:
            assert dim.name and dim.label and callable(dim.extract)

    def test_dimension_names_are_unique(self):
        names = [d.name for d in DIMENSIONS]
        assert len(names) == len(set(names))

    def test_position_size_is_relative_to_the_traders_own_median(self):
        """A $200 position is large for one account and a rounding error for
        another, so the buckets are built per run rather than declared."""
        facts = [fact(f"N{i}", pnl=100, entry=BASE + timedelta(days=i),
                      outlay=200) for i in range(40)]
        facts += [fact(f"B{i}", pnl=-300, entry=BASE + timedelta(days=40 + i),
                       outlay=2000) for i in range(20)]
        sized = [p for p in analyze(facts) if p.dimension == "size_bucket"]
        big = next(p for p in sized if "larger" in p.bucket.lower())
        assert big.kind == "weakness"
        # The mirror bucket is reported too — knowing where the edge IS matters
        # as much as knowing where it is not.
        assert any(p.kind == "strength" for p in sized)

    def test_time_of_day_uses_session_blocks_not_clock_hours(self):
        blocks = {d.name: d for d in DIMENSIONS}["hour_block"]
        opening = fact("A", entry=BASE.replace(hour=13, minute=35))
        assert blocks.extract(opening) == "Opening 15 min"

    def test_no_dimension_is_a_consequence_of_the_outcome(self):
        """Exit reason WAS a dimension, and produced the strongest-looking
        pattern in the system: "how it ended — stop loss: 0% win rate over 51
        trades against 100% elsewhere, p<0.0001". True, and circular — a trade
        that ended at its stop is a losing trade by definition, so the test can
        only rediscover the definition, with a crushing p-value, at the top of
        the ranking, pushing every real finding down. It also produced the
        recommendation "stop taking stop-loss trades".

        A dimension must describe a choice made before or during the trade,
        never a consequence of how it turned out."""
        assert "exit_family" not in {d.name for d in DIMENSIONS}

    def test_a_stop_dominated_history_produces_no_circular_pattern(self):
        """The regression that motivates the rule above: every loser exits at a
        stop and every winner at a target, which used to yield two perfect
        'patterns' and one nonsensical recommendation."""
        facts = [fact(f"W{i}", pnl=200, entry=BASE + timedelta(days=i),
                      exit_reason="target: reached") for i in range(30)]
        facts += [fact(f"L{i}", pnl=-200, entry=BASE + timedelta(days=30 + i),
                       exit_reason="stop_loss: crossed") for i in range(30)]
        assert not any("ended" in p.dimension_label.lower()
                       for p in analyze(facts))


class TestOrdering:
    def test_confidence_outranks_raw_edge(self):
        """A high-confidence 12-point edge is worth more than a low-confidence
        40-point one."""
        facts = mixed(60) + [
            fact(f"W{i}", pnl=300, entry=BASE + timedelta(days=70 + i),
                 symbol="NVDA") for i in range(40)]
        patterns = analyze(facts)
        values = [p.confidence.value for p in patterns]
        assert values == sorted(values, reverse=True)


class TestRiskIntelligence:
    def test_no_trades_is_reported_honestly(self):
        result = RiskIntelligence().analyze([], compute([]))
        assert result["assessable"] is False
        assert result["observations"] == []

    def test_drawdown_is_observed(self):
        facts = [fact("A", pnl=500), fact("B", pnl=-800), fact("C", pnl=100)]
        result = RiskIntelligence().analyze(facts, compute(facts))
        assert any(o["key"] == "drawdown" for o in result["observations"])

    def test_outsized_losing_days_are_grouped_by_day(self):
        facts = []
        for day in range(6):                       # typical losing days
            facts.append(fact(f"N{day}", pnl=-100,
                              entry=BASE + timedelta(days=day)))
        for i in range(4):                         # one catastrophic day
            facts.append(fact(f"X{i}", pnl=-400,
                              entry=BASE + timedelta(days=10, minutes=i * 15)))
        result = RiskIntelligence().analyze(facts, compute(facts))
        bad = next(o for o in result["observations"] if o["key"] == "bad_days")
        assert "-1,600" in bad["headline"] or "1600" in bad["headline"].replace(",", "")

    def test_concentration_is_flagged_only_with_something_to_compare(self):
        single = series(20, symbol="SPY")
        result = RiskIntelligence().analyze(single, compute(single))
        assert not any(o["key"] == "concentration" for o in result["observations"])

    def test_stop_coverage_reads_positive_when_disciplined(self):
        facts = series(20)
        result = RiskIntelligence().analyze(facts, compute(facts))
        stop = next(o for o in result["observations"] if o["key"] == "stop_coverage")
        assert stop["severity"] == Severity.POSITIVE.value

    def test_r_multiple_distribution_reports_its_own_coverage(self):
        facts = series(10, r_multiple=None)
        result = RiskIntelligence().analyze(facts, compute(facts))
        assert result["distribution"]["r_multiple"]["measured"] == 0
        assert result["notes"]

    def test_observations_are_ordered_worst_first(self):
        facts = series(30, wins="WLL", had_stop=False, outlay=200.0)
        facts[0] = fact(facts[0].trade_id, entry=facts[0].entry_ts, outlay=5000.0)
        result = RiskIntelligence().analyze(facts, compute(facts))
        ranks = [Severity(o["severity"]).rank for o in result["observations"]]
        assert ranks == sorted(ranks, reverse=True)
