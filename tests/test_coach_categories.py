"""AI Coach 2.0 — per-trade category scorecard tests."""

from optionspilot.coach import TradeCoach
from optionspilot.coach.categories import CATEGORY_ORDER, score_categories
from optionspilot.coach.coach import Finding
from tests.test_coach import coach, ctx, stop_order
from tests.test_journal import make_trade


def _by_name(categories: list[dict]) -> dict[str, dict]:
    return {c["name"]: c for c in categories}


class TestScoreCategories:
    def test_clean_trade_scores_high_across_categories(self):
        findings = [
            Finding("setup quality", True, "engine read the setup as good (same direction, 70%)"),
            Finding("trend confirmation", True, "higher-timeframe trend was up"),
            Finding("stop in place", True, "1 protective order(s) during the trade"),
            Finding("position sizing", True, "premium outlay 1.5% of the account"),
        ]
        cats = _by_name([c.to_dict() for c in
                         score_categories(findings, [], verdict="won", had_context=True)])
        assert len(cats) == len(CATEGORY_ORDER)
        assert cats["Risk Management"]["score"] == 100
        assert cats["Trend Alignment"]["score"] == 100
        # explanation references actual data
        assert "1.5%" in cats["Position Size"]["explanation"]

    def test_each_mistake_lowers_its_category(self):
        cases = {
            "no_stop": "Risk Management",
            "counter_trend": "Trend Alignment",
            "oversized": "Position Size",
            "chased_entry": "Entry Quality",
            "open_chop": "Timing",
            "held_loser": "Exit Quality",
            "revenge_trade": "Emotional Discipline",
        }
        for tag, category in cases.items():
            cats = _by_name([c.to_dict() for c in score_categories(
                [], [tag], verdict="lost", had_context=True)])
            assert cats[category]["score"] < 100, f"{tag} did not lower {category}"
            # the suggestion becomes the mistake's exercise
            assert cats[category]["suggestion"]

    def test_context_only_categories_are_none_without_context(self):
        # No findings, no mistakes, no snapshot → context-only categories can't
        # be judged and must report None rather than a misleading perfect score.
        cats = _by_name([c.to_dict() for c in
                         score_categories([], [], verdict="scratch", had_context=False)])
        assert cats["Entry Quality"]["score"] is None
        assert cats["Trend Alignment"]["score"] is None
        assert cats["Entry Quality"]["grade"] == "—"
        # order/outcome categories stay assessable (absence of violation is data)
        assert cats["Risk Management"]["score"] == 100
        assert cats["Exit Quality"]["score"] == 100

    def test_multiple_mistakes_stack_penalties(self):
        cats = _by_name([c.to_dict() for c in score_categories(
            [], ["no_confirmation", "chased_entry"], verdict="lost", had_context=True)])
        # Entry Quality penalized by both (30 + 22) → floored appropriately
        assert cats["Entry Quality"]["score"] <= 100 - 30


class TestReviewIntegration:
    def test_review_populates_categories_and_outcome(self, tmp_path):
        trade = make_trade("c1", 120.0, strategy="manual")
        review = coach(tmp_path).review(
            trade, ctx(quality="good", spot=100.0, delta=0.45),
            {"spot": 101.5}, orders=[stop_order(level=98.0)],
            equity_at_entry=25_000.0,
        )
        d = review.to_dict()
        assert len(d["categories"]) == len(CATEGORY_ORDER)
        assert d["symbol"] == "SPY" and d["direction"] == "long"
        assert d["pnl"] == 120.0
        assert d["entry_ts"]
        # r_multiple derivable here: |100-98| * 0.45 * 100 * 1 = 90 risk → 120/90
        assert d["r_multiple"] is not None

    def test_r_multiple_none_without_stop_or_delta(self, tmp_path):
        trade = make_trade("c2", 50.0, strategy="manual")
        review = coach(tmp_path).review(
            trade, ctx(delta=0.0), None, orders=[], equity_at_entry=25_000.0)
        assert review.r_multiple is None

    def test_missing_context_still_produces_categories(self, tmp_path):
        trade = make_trade("c3", -40.0, strategy="manual")
        review = coach(tmp_path).review(trade, None, None, orders=[])
        cats = _by_name(review.categories)
        assert cats["Entry Quality"]["score"] is None
        # no orders → no stop was placed → Risk Management dinged
        assert cats["Risk Management"]["score"] < 100
