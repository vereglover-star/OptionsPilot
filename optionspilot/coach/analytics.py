"""AI Coach 2.0 dashboard analytics.

`build_dashboard(reviews)` turns the full set of persisted `CoachReview` dicts
into the mentor dashboard: headline performance, the category scorecard averaged
over time, four headline sub-scores (consistency / risk / execution /
discipline), win/loss streaks, pattern detection with a confidence level and
"developing vs recurring" language, a month-over-month improvement timeline, and
a short ranked action plan.

Pure and deterministic — no I/O, no clock, no randomness — so it is fully
testable and reproducible. Tolerant of reviews written before Coach 2.0: missing
`categories`/outcome fields are simply skipped, never guessed.

Two deliberate design points:
- **Patterns and action items are computed over a recent window** (the last
  `RECENT_WINDOW` reviews), not all of history, so a habit the user has stopped
  drops off the action plan automatically as newer clean trades push it out.
- **Confidence scales with evidence** (sample size + frequency): a one-off is
  "low / may be developing"; a frequent, consistent pattern is "high / recurring
  habit". See `_confidence`.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from optionspilot.coach.categories import CATEGORY_ORDER, CATEGORY_SPEC
from optionspilot.coach.coach import MISTAKES

RECENT_WINDOW = 25        # trades the pattern/action plan reflects
MIN_MONTH_TRADES = 3      # a month needs this many to enter a trend comparison
TREND_MIN_POINTS = 5      # category avg must move ≥ this to be called out
MAX_ACTIONS = 5

# Category groupings behind the four headline sub-scores.
_EXECUTION = ("Entry Quality", "Exit Quality", "Timing")
_DISCIPLINE = ("Emotional Discipline", "Rule Following", "Patience")


def _grade(score: float | None) -> str:
    if score is None:
        return "—"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _month(iso: str) -> str | None:
    return iso[:7] if iso and len(iso) >= 7 else None


def _confidence(count: int, total: int) -> str:
    """Confidence a pattern is real, from sample size and frequency. A single
    occurrence is always low confidence — one data point can't be a habit,
    however large its share of a tiny sample."""
    if count <= 1:
        return "low"
    rate = count / total if total else 0.0
    if count >= 4 and rate >= 0.30:
        return "high"
    return "medium"


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 1) if xs else None


def _sorted(reviews: list[dict]) -> list[dict]:
    """Chronological by entry time, with trade_id as a stable tiebreaker."""
    return sorted(reviews, key=lambda r: (r.get("entry_ts", ""),
                                          r.get("trade_id", "")))


# ── streaks ───────────────────────────────────────────────────────────────

def _streaks(verdicts: list[str]) -> dict:
    longest_win = longest_loss = 0
    run_kind: str | None = None
    run_len = 0
    for v in verdicts:
        kind = "win" if v == "won" else "loss" if v == "lost" else None
        if kind is None:
            run_kind, run_len = None, 0
            continue
        run_len = run_len + 1 if kind == run_kind else 1
        run_kind = kind
        if kind == "win":
            longest_win = max(longest_win, run_len)
        else:
            longest_loss = max(longest_loss, run_len)
    current = 0
    if run_kind == "win":
        current = run_len
    elif run_kind == "loss":
        current = -run_len
    return {"current": current, "longest_win": longest_win,
            "longest_loss": longest_loss}


# ── category aggregation + monthly trend ────────────────────────────────────

def _category_block(reviews_sorted: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """Returns (per-category rows, {name: avg}, improvement sentences)."""
    scores: dict[str, list[float]] = defaultdict(list)
    by_month: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in reviews_sorted:
        m = _month(r.get("entry_ts", ""))
        for c in r.get("categories", []):
            s = c.get("score")
            if s is None:
                continue
            scores[c["name"]].append(s)
            if m:
                by_month[c["name"]][m].append(s)

    rows: list[dict] = []
    averages: dict[str, float] = {}
    improvements: list[tuple[float, str]] = []
    for name in CATEGORY_ORDER:
        vals = scores.get(name, [])
        avg = _mean(vals)
        trend = None
        if avg is not None:
            averages[name] = avg
        # month-over-month trend for this category
        months = [(m, v) for m, v in sorted(by_month[name].items())
                  if len(v) >= MIN_MONTH_TRADES]
        if len(months) >= 2:
            prev = statistics.fmean(months[-2][1])
            latest = statistics.fmean(months[-1][1])
            trend = round(latest - prev, 1)
            if abs(trend) >= TREND_MIN_POINTS:
                verb = "improved" if trend > 0 else "declined"
                improvements.append((abs(trend),
                    f"{name} {verb} {abs(trend):.0f} points this month "
                    f"({prev:.0f} → {latest:.0f})."))
        rows.append({"name": name, "score": avg, "grade": _grade(avg),
                     "trend": trend, "n": len(vals)})
    improvements.sort(reverse=True)
    return rows, averages, [s for _, s in improvements[:6]]


# ── pattern detection ───────────────────────────────────────────────────────

def _patterns(recent: list[dict]) -> list[dict]:
    rn = len(recent)
    counts = Counter(m for r in recent for m in r.get("mistakes", []))
    out: list[dict] = []
    for tag, count in counts.most_common():
        if tag not in MISTAKES:
            continue
        conf = _confidence(count, rn)
        status = ("appears to be a recurring habit" if count >= 2
                  else "may be developing")
        out.append({
            "tag": tag, "label": MISTAKES[tag][0], "count": count,
            "rate": round(count / rn, 3) if rn else 0.0,
            "confidence": conf, "status": status,
            "coaching": MISTAKES[tag][1], "exercise": MISTAKES[tag][2],
        })
    return out


# ── action plan ─────────────────────────────────────────────────────────────

def _action_items(patterns: list[dict], averages: dict[str, float],
                  cat_n: dict[str, int], recent_n: int) -> list[dict]:
    actions: list[dict] = []
    seen: set[str] = set()

    # 1) behavior patterns first — the most specific, actionable signal.
    for p in patterns:
        if p["exercise"] in seen:
            continue
        seen.add(p["exercise"])
        actions.append({
            "text": p["exercise"], "confidence": p["confidence"],
            "focus": p["label"],
            "rationale": f"{p['label']} {p['status']} "
                         f"({p['count']}/{recent_n} recent trades).",
        })

    # 2) then persistently weak categories not already addressed.
    weak = sorted(((name, avg) for name, avg in averages.items() if avg < 60),
                  key=lambda kv: kv[1])
    for name, avg in weak:
        suggestion = CATEGORY_SPEC[name]["suggestion"]
        if suggestion in seen:
            continue
        seen.add(suggestion)
        n = cat_n.get(name, 0)
        conf = "high" if n >= 5 else "medium" if n >= 3 else "low"
        actions.append({
            "text": suggestion, "confidence": conf, "focus": name,
            "rationale": f"{name} is averaging {avg:.0f}/100 across {n} trades.",
        })

    return actions[:MAX_ACTIONS]


# ── assembly ────────────────────────────────────────────────────────────────

def build_dashboard(reviews: list[dict]) -> dict:
    n = len(reviews)
    if n == 0:
        return {"trades_reviewed": 0}

    rs = _sorted(reviews)
    verdicts = [r.get("verdict", "") for r in rs]
    scores = [r["score"] for r in rs if isinstance(r.get("score"), (int, float))]
    returns = [r["return_pct"] for r in rs if r.get("return_pct") is not None]
    holds = [r["hold_minutes"] for r in rs if r.get("hold_minutes")]
    rmults = [r["r_multiple"] for r in rs if r.get("r_multiple") is not None]
    pnls = [r["pnl"] for r in rs if r.get("pnl") is not None]

    wins = sum(1 for v in verdicts if v == "won")
    decided = sum(1 for v in verdicts if v in ("won", "lost"))

    cat_rows, averages, improvements = _category_block(rs)
    cat_n = {row["name"]: row["n"] for row in cat_rows}

    def group_avg(names: tuple[str, ...]) -> float | None:
        vals = [averages[x] for x in names if x in averages]
        return _mean(vals)

    consistency = (round(max(0.0, 100 - statistics.pstdev(scores)), 1)
                   if len(scores) >= 2 else None)

    # win rate by setup quality → best / worst
    by_q: dict[str, list[str]] = defaultdict(list)
    for r in rs:
        by_q[r.get("setup_quality", "unknown")].append(r.get("verdict", ""))
    q_wr = {q: sum(1 for x in v if x == "won") / len(v)
            for q, v in by_q.items() if v and q != "unknown"}
    best_q = max(q_wr, key=q_wr.get) if q_wr else None
    worst_q = min(q_wr, key=q_wr.get) if q_wr else None

    recent = rs[-RECENT_WINDOW:]
    patterns = _patterns(recent)
    action_items = _action_items(patterns, averages, cat_n, len(recent))

    scored = [(row["name"], row["score"]) for row in cat_rows
              if row["score"] is not None]
    strengths = sorted((c for c in scored if c[1] >= 70),
                       key=lambda kv: kv[1], reverse=True)[:3]
    weaknesses = sorted((c for c in scored if c[1] < 70), key=lambda kv: kv[1])[:3]

    return {
        "trades_reviewed": n,
        "recent_window": len(recent),
        "overall": {
            "avg_score": _mean(scores),
            "win_rate": round(wins / decided, 3) if decided else None,
            "wins": wins, "decided": decided,
            "total_pnl": round(sum(pnls), 2) if pnls else 0.0,
            "avg_return_pct": _mean(returns),
            "avg_hold_minutes": _mean(holds),
            "avg_r_multiple": _mean(rmults),
            "best_setup_quality": best_q,
            "worst_setup_quality": worst_q,
        },
        "scores": {
            "consistency": consistency,
            "risk": averages.get("Risk Management"),
            "execution": group_avg(_EXECUTION),
            "discipline": group_avg(_DISCIPLINE),
        },
        "category_scores": cat_rows,
        "strengths": [{"name": nm, "score": sc, "grade": _grade(sc)}
                      for nm, sc in strengths],
        "weaknesses": [{"name": nm, "score": sc, "grade": _grade(sc)}
                       for nm, sc in weaknesses],
        "streaks": _streaks(verdicts),
        "patterns": patterns[:6],
        "recent_improvements": improvements,
        "action_items": action_items,
    }
