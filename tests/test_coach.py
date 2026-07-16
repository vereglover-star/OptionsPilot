from datetime import datetime, timedelta, timezone

from optionspilot.coach import CoachProfile, TradeCoach
from tests.test_journal import make_trade

TS = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)


def ctx(quality="good", direction="long", confidence=70.0, htf="up",
        rsi=58.0, rvol=1.4, dte=21, iv=0.25, delta=0.45, spot=100.0,
        hour=11, minute=0):
    return {
        "captured_ts": TS.isoformat(), "spot": spot,
        "confidence": confidence, "direction": direction,
        "gate": {"setup_quality": quality},
        "htf_trend": htf,
        "entry_tf": {"rsi": rsi, "adx": 28.0, "rvol": rvol,
                     "pressure": 0.3, "trend": htf, "consolidating": False},
        "contract": {"dte": dte, "delta": delta, "iv": iv, "spread_pct": 0.02},
        "hour_et": hour, "minute_et": minute,
    }


def stop_order(level=98.0, filled=False):
    return {"kind": "stop_loss", "side": "sell_to_close", "stop_level": level,
            "status": "filled" if filled else "working"}


def coach(tmp_path) -> TradeCoach:
    return TradeCoach(tmp_path / "coach")


class TestDisciplinedTrade:
    def test_disciplined_loser_scores_well(self, tmp_path):
        """Process over outcome: a stopped-out loss on a good setup with a
        stop in place must score well."""
        trade = make_trade("t1", -80.0, strategy="manual")
        review = coach(tmp_path).review(
            trade, ctx(quality="excellent", confidence=80.0),
            {"spot": 99.0, "confidence": 10.0, "direction": "short"},
            orders=[stop_order(filled=True),
                    {"kind": "take_profit", "side": "sell_to_close",
                     "stop_level": 105.0, "status": "cancelled"}],
            equity_at_entry=25_000.0,
        )
        assert review.score >= 65
        assert review.verdict == "lost"
        assert review.mistakes == []
        assert any("stop" in s for s in review.strengths)
        assert "excellent" in review.summary

    def test_reckless_winner_scores_badly(self, tmp_path):
        """A lucky win taken against everything scores low with the tags."""
        trade = make_trade("t2", 150.0, strategy="manual")
        review = coach(tmp_path).review(
            trade,
            ctx(quality="poor", direction="short", confidence=55.0, htf="down",
                rsi=76.0, dte=2, iv=0.75, delta=0.12, hour=9, minute=32),
            None, orders=[], equity_at_entry=2_000.0,   # 200/2000 = 10% outlay
        )
        assert review.verdict == "won"
        assert review.score <= 30
        for tag in ("no_stop", "counter_trend", "chased_entry",
                    "theta_ignored", "high_iv_entry", "lottery_ticket",
                    "oversized", "open_chop"):
            assert tag in review.mistakes, tag
        assert review.pro_notes and review.improvements
        assert "negative expected value" in review.ev_note


class TestBehaviourTags:
    def test_moved_stop_detected(self, tmp_path):
        trade = make_trade("t3", -50.0, strategy="manual")
        orders = [stop_order(98.0), stop_order(96.0)]   # widened against a long
        review = coach(tmp_path).review(trade, ctx(), None, orders)
        assert "moved_stop" in review.mistakes

    def test_stop_moved_up_is_fine(self, tmp_path):
        trade = make_trade("t4", 60.0, strategy="manual")
        orders = [stop_order(98.0), stop_order(99.5)]   # trailing up: good
        review = coach(tmp_path).review(trade, ctx(), None, orders)
        assert "moved_stop" not in review.mistakes

    def test_averaged_down_detected(self, tmp_path):
        trade = make_trade("t5", -90.0, strategy="manual")
        orders = [
            {"kind": "market", "side": "buy_to_open", "status": "filled",
             "fill_price": 2.00},
            {"kind": "market", "side": "buy_to_open", "status": "filled",
             "fill_price": 1.40},
            stop_order(),
        ]
        review = coach(tmp_path).review(trade, ctx(), None, orders)
        assert "averaged_down" in review.mistakes

    def test_revenge_trade_detected(self, tmp_path):
        trade = make_trade("t6", -30.0, strategy="manual")
        review = coach(tmp_path).review(
            trade, ctx(), None, [stop_order()],
            recent_loss_minutes_before_entry=6.0)
        assert "revenge_trade" in review.mistakes

    def test_held_loser_detected(self, tmp_path):
        # entry 2.00 x1 -> outlay 200; pnl -120 = -60% of premium
        trade = make_trade("t7", -120.0, strategy="manual")
        review = coach(tmp_path).review(trade, ctx(), None, [stop_order()])
        assert "held_loser" in review.mistakes

    def test_cut_winner_early_detected(self, tmp_path):
        trade = make_trade("t8", 80.0, strategy="manual")
        review = coach(tmp_path).review(
            trade, ctx(),
            {"spot": 103.0, "confidence": 55.0, "direction": "long"},
            [stop_order()])
        assert "cut_winner_early" in review.mistakes
        assert any("scaling out" in line for line in review.after)


class TestMissingContext:
    def test_review_survives_no_context(self, tmp_path):
        trade = make_trade("t9", 40.0, strategy="manual")
        review = coach(tmp_path).review(trade, None, None, [])
        assert review.setup_quality == "unknown"
        assert "no_stop" in review.mistakes        # order discipline still checked
        assert "unknown" in review.ev_note
        assert 5 <= review.score <= 95


class TestPersistenceAndProfile:
    def test_reviews_persist_and_reload(self, tmp_path):
        c = coach(tmp_path)
        c.review(make_trade("p1", 50.0, strategy="manual"), ctx(), None,
                 [stop_order()])
        assert c.load("p1")["trade_id"] == "p1"
        assert len(c.load_all()) == 1

    def test_profile_aggregates_mistakes(self, tmp_path):
        c = coach(tmp_path)
        for i in range(4):   # habitual no-stop trader
            c.review(make_trade(f"a{i}", -20.0, strategy="manual"),
                     ctx(), None, [])
        c.review(make_trade("b1", 30.0, strategy="manual"), ctx(), None,
                 [stop_order()])
        profile = CoachProfile(c.load_all()).build()
        assert profile["trades_reviewed"] == 5
        top = profile["recurring_mistakes"][0]
        assert top["tag"] == "no_stop" and top["count"] == 4
        assert profile["recommended_exercises"]
        assert "avg_score" in profile and "score_trend" in profile

    def test_empty_profile(self):
        assert CoachProfile([]).build() == {"trades_reviewed": 0}
