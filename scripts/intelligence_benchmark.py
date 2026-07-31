"""Performance benchmark for the Trading Intelligence Engine.

The engine sits on a UI request path, so "how long does it take at 50,000
trades" is a correctness question, not a curiosity: a snapshot that takes ten
seconds is a dashboard that appears broken.

What this measures, and why each one is here:

* **Scaling.** The suite runs the full pipeline at several history sizes and
  reports the per-trade cost. A roughly flat per-trade cost means the pipeline
  is O(n log n); a rising one means something has gone quadratic. The
  revenge-window detector is the obvious candidate — the naive "for each trade,
  scan every earlier trade" version is O(n²), which is why `behavior.py` uses a
  sorted list and a binary search.
* **Per-engine cost.** Ten engines run per snapshot. Knowing which dominates is
  the difference between optimising the right one and guessing.
* **Payload size.** The snapshot is serialised to a browser. Evidence carries
  trade IDs, and unbounded that becomes megabytes.
* **Cache effectiveness.** A page polling four intelligence routes must cost one
  analysis, not four.

Deliberately not a pytest test: it is a measurement whose absolute numbers
depend on the machine. `tests/test_intelligence_engine.py::TestScale` holds the
*assertions* (a generous wall-clock ceiling and a non-quadratic bound) that must
hold everywhere; this script is what you run when you want the actual numbers.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from optionspilot.intelligence import TradingIntelligence  # noqa: E402
from optionspilot.intelligence.achievements import AchievementEngine  # noqa: E402
from optionspilot.intelligence.behavior import BehaviorEngine  # noqa: E402
from optionspilot.intelligence.confidence import (  # noqa: E402
    ConfidenceEngine, ScoreInput,
)
from optionspilot.intelligence.curriculum import CurriculumEngine  # noqa: E402
from optionspilot.intelligence.facts import FactSet, TradeFact, WEEKDAY_NAMES, ET  # noqa: E402
from optionspilot.intelligence.patterns import PatternEngine  # noqa: E402
from optionspilot.intelligence.performance import PerformanceEngine  # noqa: E402
from optionspilot.intelligence.risk import RiskIntelligence  # noqa: E402
from optionspilot.intelligence.timeline import TimelineEngine  # noqa: E402

SIZES = (100, 1_000, 5_000, 20_000, 50_000)
SYMBOLS = ("SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "META", "MSFT")


def make_facts(n: int, seed: int = 4) -> tuple[TradeFact, ...]:
    """A realistic history: mixed symbols, mixed outcomes, partial instrument
    coverage (a fifth of trades never recorded a delta, a fifth never had a
    review) so the "field was not recorded" branches are exercised too."""
    rng = random.Random(seed)
    start = datetime(2018, 1, 2, 15, 0, tzinfo=timezone.utc)
    out: list[TradeFact] = []
    for i in range(n):
        entry = start + timedelta(minutes=53 * i)
        et = entry.astimezone(ET)
        won = rng.random() < 0.46
        pnl = round(rng.uniform(20, 600) if won else -rng.uniform(20, 450), 2)
        reviewed = rng.random() < 0.8
        instrumented = rng.random() < 0.8
        out.append(TradeFact(
            trade_id=f"T{i:07d}",
            symbol=rng.choice(SYMBOLS),
            direction=rng.choice(("long", "short")),
            strategy="confluence_v1" if rng.random() < 0.5 else "manual",
            managed_by=rng.choice(("ai", "manual")),
            entry_ts=entry, exit_ts=entry + timedelta(minutes=rng.choice((15, 60, 240))),
            quantity=rng.choice((1, 1, 2, 5)),
            entry_price=round(rng.uniform(0.8, 6.0), 2), exit_price=2.0,
            pnl=pnl, is_win=pnl > 0,
            hold_minutes=float(rng.choice((15, 45, 120, 390))),
            exit_reason=rng.choice(("target: reached", "stop_loss: crossed",
                                    "manual: closed")),
            return_pct=round(pnl / 300 * 100, 2),
            entry_date=et.date().isoformat(),
            weekday=WEEKDAY_NAMES[et.weekday()],
            hour_et=et.hour, minute_et=et.minute, session="regular",
            confidence=rng.uniform(25, 95),
            setup_quality=rng.choice(("excellent", "good", "average", "poor")),
            market_regime=rng.choice(("trending-up/low-vol", "ranging/medium-vol",
                                      "trending-down/high-vol")),
            htf_trend=rng.choice(("up", "down", "neutral")),
            timeframe=rng.choice(("5m", "15m", "1h")),
            risk_reward=rng.uniform(0.8, 4.0),
            rsi=rng.uniform(15, 88) if instrumented else None,
            adx=rng.uniform(8, 50) if instrumented else None,
            rvol=rng.uniform(0.3, 3.5) if instrumented else None,
            iv=rng.uniform(0.12, 0.95) if instrumented else None,
            delta=round(rng.uniform(0.05, 0.8), 2) if instrumented else None,
            dte=rng.choice((0, 2, 7, 21, 45, 90)) if instrumented else None,
            outlay=round(rng.uniform(80, 1500), 2),
            r_multiple=round(pnl / 180, 2) if reviewed else None,
            process_score=rng.randint(20, 95) if reviewed else None,
            mistakes=tuple(m for m in ("no_stop", "chased_entry", "moved_stop")
                           if rng.random() < 0.12),
            evidence_names=("htf_trend_alignment",),
            category_scores={"Entry Quality": rng.uniform(35, 95),
                             "Risk Management": rng.uniform(25, 95),
                             "Exit Quality": rng.uniform(35, 95),
                             "Rule Following": rng.uniform(35, 95)} if reviewed else {},
            had_stop=(rng.random() < 0.82) if reviewed else None,
            widened_stop=(rng.random() < 0.08) if reviewed else None,
            had_target=(rng.random() < 0.6) if reviewed else None,
            reviewed=reviewed,
        ))
    out.sort(key=lambda f: (f.entry_ts, f.trade_id))
    return tuple(out)


def timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    return (time.perf_counter() - started) * 1000, result


def bench_engines(facts: tuple[TradeFact, ...]) -> list[tuple[str, float]]:
    """Per-engine wall clock, in the order the facade runs them."""
    rows: list[tuple[str, float]] = []

    ms, perf = timed("performance", lambda: PerformanceEngine().analyze(facts))
    rows.append(("PerformanceEngine", ms))

    recent = facts[-50:]
    ms, behaviors = timed("behavior",
                          lambda: BehaviorEngine().analyze(recent, facts[-100:-50]))
    rows.append(("BehaviorEngine", ms))
    behavior_map = {b.id: b for b in behaviors}

    ms, patterns = timed("patterns", lambda: PatternEngine().analyze(facts))
    rows.append(("PatternEngine", ms))

    ms, _ = timed("risk", lambda: RiskIntelligence().analyze(facts, perf.metrics))
    rows.append(("RiskIntelligence", ms))

    ms, scores = timed("scores", lambda: ConfidenceEngine().analyze(ScoreInput(
        facts=facts, metrics=perf.metrics, behaviors=behavior_map,
        monthly=perf.periods["month"], trends=perf.trends)))
    rows.append(("ConfidenceEngine", ms))

    ms, _ = timed("curriculum", lambda: CurriculumEngine().recommend(
        behavior_map, perf.metrics, {s.key: s for s in scores}))
    rows.append(("CurriculumEngine", ms))

    ms, _ = timed("timeline", lambda: TimelineEngine().build(facts))
    rows.append(("TimelineEngine", ms))

    ms, _ = timed("achievements",
                  lambda: AchievementEngine().evaluate(facts, perf.metrics))
    rows.append(("AchievementEngine", ms))

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="*", default=list(SIZES))
    ap.add_argument("--detail", type=int, default=20_000,
                    help="history size to break down per engine")
    args = ap.parse_args()

    print("Trading Intelligence Engine - performance benchmark")
    print("=" * 74)
    print(f"{'trades':>8} {'analysis':>11} {'per trade':>11} {'payload':>11} "
          f"{'behaviours':>11} {'patterns':>9}")
    print("-" * 74)

    per_trade: list[tuple[int, float]] = []
    for n in args.sizes:
        facts = make_facts(n)
        intel = TradingIntelligence(lambda f=facts: FactSet(facts=f))
        ms, snapshot = timed("snapshot", intel.snapshot)
        payload = len(json.dumps(snapshot.to_dict()))
        detected = sum(1 for b in snapshot.behaviors if b.detected)
        per_trade.append((n, ms / n * 1000))     # microseconds per trade
        print(f"{n:>8,} {ms:>9.0f}ms {ms / n * 1000:>9.1f}us "
              f"{payload / 1024:>9.0f}KB {detected:>11} {len(snapshot.patterns):>9}")

    print("-" * 74)
    first, last = per_trade[0][1], per_trade[-1][1]
    ratio = last / first if first else float("inf")
    verdict = ("FLAT — pipeline is sub-quadratic" if ratio < 3
               else "RISING — something may have gone quadratic")
    print(f"per-trade cost {first:.1f}us -> {last:.1f}us ({ratio:.1f}x): {verdict}")

    print(f"\nPer-engine breakdown at {args.detail:,} trades")
    print("-" * 74)
    facts = make_facts(args.detail)
    rows = bench_engines(facts)
    total = sum(ms for _, ms in rows)
    for label, ms in sorted(rows, key=lambda r: -r[1]):
        share = ms / total * 100 if total else 0
        print(f"  {label:<22} {ms:>8.0f}ms  {share:>5.1f}%  "
              f"{'#' * int(share / 2)}")
    print(f"  {'TOTAL':<22} {total:>8.0f}ms")

    print("\nCache effectiveness (a page polling four routes must cost one analysis)")
    print("-" * 74)
    intel = TradingIntelligence(lambda: FactSet(facts=facts),
                                fingerprint_provider=lambda: "v1")
    cold, _ = timed("cold", intel.snapshot)
    warm_total = 0.0
    for _ in range(4):
        ms, _ = timed("warm", intel.snapshot)
        warm_total += ms
    print(f"  cold analysis      {cold:>8.0f}ms")
    print(f"  4 cached reads     {warm_total:>8.2f}ms "
          f"({warm_total / max(cold, 1e-9) * 100:.3f}% of one analysis)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
