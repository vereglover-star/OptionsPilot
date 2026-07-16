"""CoachProfile — your strengths, weaknesses, and improvement trend,
aggregated from every TradeCoach review.

The profile is recomputed from the persisted reviews (data/coach/*.json), so
it survives restarts and never drifts from the underlying evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from optionspilot.coach.coach import MISTAKES


class CoachProfile:
    def __init__(self, reviews: list[dict]):
        self._reviews = sorted(reviews, key=lambda r: r.get("trade_id", ""))

    def build(self) -> dict:
        n = len(self._reviews)
        if n == 0:
            return {"trades_reviewed": 0}

        mistake_counts = Counter(
            m for r in self._reviews for m in r.get("mistakes", [])
        )
        scores = [r.get("score", 0) for r in self._reviews]
        by_quality: dict[str, list[str]] = defaultdict(list)
        for r in self._reviews:
            by_quality[r.get("setup_quality", "unknown")].append(
                r.get("verdict", ""))

        recurring = [
            {"tag": tag, "label": MISTAKES[tag][0], "count": count,
             "rate": round(count / n, 3),
             "coaching": MISTAKES[tag][1], "exercise": MISTAKES[tag][2]}
            for tag, count in mistake_counts.most_common()
            if tag in MISTAKES
        ]
        strengths = Counter(
            s for r in self._reviews for s in r.get("strengths", [])
        )

        half = max(n // 2, 1)
        early = scores[:half]
        late = scores[half:] or early
        trend = round(sum(late) / len(late) - sum(early) / len(early), 1)

        wins_by_quality = {
            q: {"trades": len(v),
                "win_rate": round(sum(1 for x in v if x == "won") / len(v), 3)}
            for q, v in by_quality.items() if v
        }

        return {
            "trades_reviewed": n,
            "avg_score": round(sum(scores) / n, 1),
            "score_trend": trend,       # + = improving process over time
            "recent_scores": scores[-10:],
            "recurring_mistakes": recurring[:6],
            "top_strengths": [{"strength": s, "count": c}
                              for s, c in strengths.most_common(5)],
            "by_setup_quality": wins_by_quality,
            "recommended_exercises": [m["exercise"] for m in recurring[:3]],
        }
