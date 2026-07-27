"""AI Coach 2.0 — dashboard analytics tests (pattern confidence, streaks,
trend timeline, action plan, headline scores)."""

from optionspilot.coach.analytics import _confidence, build_dashboard


def review(tid, *, verdict="won", score=75, mistakes=(), month="2026-06",
           day=1, cats=None, pnl=100.0, return_pct=25.0, hold=40.0,
           r=None, setup="good", ts=None):
    return {
        "trade_id": tid, "verdict": verdict, "score": score,
        "mistakes": list(mistakes), "setup_quality": setup,
        "entry_ts": ts or f"{month}-{day:02d}T14:30:00",
        "pnl": pnl, "return_pct": return_pct, "hold_minutes": hold,
        "r_multiple": r,
        "categories": cats or [],
    }


def cat(name, score):
    return {"name": name, "score": score, "grade": "B",
            "explanation": "x", "suggestion": "y"}


class TestConfidence:
    def test_single_occurrence_is_low(self):
        assert _confidence(1, 20) == "low"      # one data point is never a habit
        assert _confidence(1, 1) == "low"

    def test_scales_with_sample_and_frequency(self):
        assert _confidence(2, 20) == "medium"
        assert _confidence(5, 10) == "high"     # frequent + large enough
        assert _confidence(5, 100) == "medium"  # large count but rare → not high


class TestEmpty:
    def test_no_reviews(self):
        assert build_dashboard([]) == {"trades_reviewed": 0}


class TestPatterns:
    def test_one_off_is_developing_frequent_is_recurring(self):
        reviews = [review(f"t{i}", mistakes=["chased_entry"]) for i in range(5)]
        reviews.append(review("solo", mistakes=["oversized"]))
        d = build_dashboard(reviews)
        pats = {p["tag"]: p for p in d["patterns"]}
        assert pats["chased_entry"]["status"] == "appears to be a recurring habit"
        assert pats["chased_entry"]["confidence"] in ("high", "medium")
        assert pats["oversized"]["status"] == "may be developing"
        assert pats["oversized"]["confidence"] == "low"

    def test_pattern_drops_out_of_recent_window(self):
        # An old habit (30 trades ago) followed by 25 clean trades should not
        # appear in the recent-window patterns/actions anymore.
        old = [review(f"old{i}", mistakes=["no_stop"],
                      ts=f"2026-06-01T00:{i:02d}:00") for i in range(5)]
        clean = [review(f"new{i}", mistakes=[],
                        ts=f"2026-06-01T01:{i:02d}:00") for i in range(25)]
        d = build_dashboard(old + clean)
        assert all(p["tag"] != "no_stop" for p in d["patterns"])
        assert all("stop" not in a["focus"].lower() for a in d["action_items"])


class TestStreaks:
    def test_streaks_and_current(self):
        seq = ["won", "won", "lost", "lost", "lost", "won"]
        reviews = [review(f"t{i}", verdict=v, day=i + 1)
                   for i, v in enumerate(seq)]
        d = build_dashboard(reviews)
        assert d["streaks"]["longest_win"] == 2
        assert d["streaks"]["longest_loss"] == 3
        assert d["streaks"]["current"] == 1     # ended on a single win

    def test_scratch_breaks_streak(self):
        reviews = [review("a", verdict="won", day=1),
                   review("b", verdict="scratch", day=2),
                   review("c", verdict="won", day=3)]
        d = build_dashboard(reviews)
        assert d["streaks"]["longest_win"] == 1


class TestTrendTimeline:
    def test_month_over_month_improvement_sentence(self):
        # June: Risk avg 60; July: Risk avg 85 → +25 points improvement.
        june = [review(f"j{i}", month="2026-06", day=i + 1,
                       cats=[cat("Risk Management", 60)]) for i in range(3)]
        july = [review(f"y{i}", month="2026-07", day=i + 1,
                       cats=[cat("Risk Management", 85)]) for i in range(3)]
        d = build_dashboard(june + july)
        improvements = " ".join(d["recent_improvements"])
        assert "Risk Management improved" in improvements
        risk_row = next(c for c in d["category_scores"] if c["name"] == "Risk Management")
        assert risk_row["trend"] == 25.0

    def test_no_trend_when_months_too_small(self):
        # Only one trade per month → below MIN_MONTH_TRADES, no trend claimed.
        reviews = [review("a", month="2026-06", cats=[cat("Timing", 50)]),
                   review("b", month="2026-07", cats=[cat("Timing", 90)])]
        d = build_dashboard(reviews)
        assert d["recent_improvements"] == []


class TestScoresAndActions:
    def test_headline_scores_and_weak_category_action(self):
        cats = [cat("Risk Management", 40), cat("Entry Quality", 50),
                cat("Exit Quality", 55), cat("Timing", 45),
                cat("Emotional Discipline", 80), cat("Rule Following", 70),
                cat("Patience", 60)]
        reviews = [review(f"t{i}", cats=cats, score=50 + i) for i in range(5)]
        d = build_dashboard(reviews)
        assert d["scores"]["risk"] == 40
        assert d["scores"]["execution"] is not None   # mean of entry/exit/timing
        assert d["scores"]["discipline"] is not None
        assert d["scores"]["consistency"] is not None
        # weak categories (<60) generate action items, capped at 5
        assert 1 <= len(d["action_items"]) <= 5
        assert any(a["focus"] == "Risk Management" for a in d["action_items"])

    def test_overall_metrics(self):
        reviews = [review("w", verdict="won", pnl=200, return_pct=40, r=2.0),
                   review("l", verdict="lost", pnl=-100, return_pct=-50, r=-1.0)]
        d = build_dashboard(reviews)
        o = d["overall"]
        assert o["win_rate"] == 0.5
        assert o["total_pnl"] == 100.0
        assert o["avg_r_multiple"] == 0.5


class TestBackwardCompatibility:
    def test_old_reviews_without_new_fields(self):
        # Pre-Coach-2.0 reviews have no categories/outcome fields.
        old = [{"trade_id": "old1", "verdict": "won", "score": 70,
                "mistakes": [], "setup_quality": "good"}]
        d = build_dashboard(old)
        assert d["trades_reviewed"] == 1
        assert d["category_scores"]        # all rows present, scores None
        assert all(c["score"] is None for c in d["category_scores"])
