"""Learning system: slice historical performance, recommend evidence-weight
adjustments, persist them versioned.

Guard rails (deliberate, and load-bearing):
  - **Sample-size aware.** No slice with fewer than `min_sample` trades moves
    anything. Ten trades of "VWAP is great" is noise, not knowledge.
  - **Bounded steps.** One update cycle changes a weight by at most ±20%
    (win-rate lift vs. baseline, scaled and clamped), so no single lucky week
    rewires the scorer.
  - **Bounded range.** A learned weight can never leave
    [0.25x, 2.0x] of its DEFAULT_WEIGHTS value — evidence can be de-emphasized
    but never silently deleted, boosted but never allowed to dominate alone.
  - **Versioned and auditable.** Every update appends to the WeightStore
    history with its rationale; `engine.evidence_weights` in config still
    overrides everything (human > machine).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import TradeRecord
from optionspilot.engine.scorer import DEFAULT_WEIGHTS
from optionspilot.journal import TradeJournal

log = get_logger("journal")

ET = ZoneInfo("America/New_York")

MAX_STEP = 0.20          # one cycle moves a weight at most ±20%
MIN_FACTOR = 0.25        # floor vs default
MAX_FACTOR = 2.0         # ceiling vs default
LIFT_SCALE = 2.0         # win-rate lift of 10pp -> 20% step (pre-clamp)


@dataclass(frozen=True, slots=True)
class SliceStats:
    label: str
    trades: int
    win_rate: float
    total_pnl: float
    expectancy: float
    profit_factor: float

    @staticmethod
    def from_trades(label: str, trades: list[TradeRecord]) -> "SliceStats":
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        return SliceStats(
            label=label,
            trades=len(trades),
            win_rate=round(len(wins) / len(trades), 4) if trades else 0.0,
            total_pnl=round(sum(pnls), 2),
            expectancy=round(sum(pnls) / len(trades), 2) if trades else 0.0,
            profit_factor=round(sum(wins) / gross_loss, 2) if gross_loss else float("inf"),
        )


class LearningEngine:
    def __init__(self, journal: TradeJournal, min_sample: int = 20):
        self._journal = journal
        self.min_sample = min_sample

    # ── performance slicing ──────────────────────────────────────────────────

    def slice_by(self, label_fn: Callable[[TradeRecord], Iterable[str] | str],
                 ) -> list[SliceStats]:
        """Group trades by label(s) — a trade may belong to several slices
        (e.g. one per supporting evidence name)."""
        groups: dict[str, list[TradeRecord]] = {}
        for t in self._journal.all():
            labels = label_fn(t)
            if isinstance(labels, str):
                labels = [labels]
            for label in labels:
                groups.setdefault(label, []).append(t)
        return sorted(
            (SliceStats.from_trades(k, v) for k, v in groups.items()),
            key=lambda s: s.expectancy, reverse=True,
        )

    def by_evidence(self) -> list[SliceStats]:
        return self.slice_by(lambda t: t.indicators_used)

    def by_hour_et(self) -> list[SliceStats]:
        return self.slice_by(lambda t: f"{t.entry_ts.astimezone(ET).hour:02d}:00 ET")

    def by_dte(self) -> list[SliceStats]:
        def bucket(t: TradeRecord) -> str:
            dte = int(t.market_conditions.get("dte", -1))
            if dte < 0:
                return "unknown"
            for lo, hi in ((0, 7), (8, 14), (15, 30), (31, 60)):
                if lo <= dte <= hi:
                    return f"{lo}-{hi} DTE"
            return "60+ DTE"
        return self.slice_by(bucket)

    def by_confidence(self) -> list[SliceStats]:
        return self.slice_by(
            lambda t: f"{int(t.confidence // 10) * 10}-{int(t.confidence // 10) * 10 + 9}%"
        )

    def by_direction(self) -> list[SliceStats]:
        return self.slice_by(lambda t: t.direction.value)

    def by_symbol(self) -> list[SliceStats]:
        return self.slice_by(lambda t: t.symbol)

    def by_exit_reason(self) -> list[SliceStats]:
        return self.slice_by(lambda t: t.exit_reason.split(":")[0] or "unknown")

    # ── weight recommendation ────────────────────────────────────────────────

    def recommend_weights(
        self, current: dict[str, float] | None = None
    ) -> tuple[dict[str, float], list[str]]:
        """Adjust each evidence weight by how much better/worse trades that had
        it as supporting evidence performed vs. the overall baseline win rate.
        Returns (new_weights, rationale)."""
        current = {**DEFAULT_WEIGHTS, **(current or {})}
        trades = self._journal.all()
        rationale: list[str] = []
        if len(trades) < self.min_sample:
            rationale.append(
                f"insufficient history ({len(trades)} < {self.min_sample}) — no changes"
            )
            return current, rationale

        baseline = sum(1 for t in trades if t.is_win) / len(trades)
        new_weights = dict(current)
        for stats in self.by_evidence():
            name = stats.label
            if name not in DEFAULT_WEIGHTS:
                continue
            if stats.trades < self.min_sample:
                rationale.append(
                    f"{name}: only {stats.trades} trades (< {self.min_sample}) — unchanged"
                )
                continue
            lift = stats.win_rate - baseline
            step = max(-MAX_STEP, min(MAX_STEP, lift * LIFT_SCALE))
            proposed = current[name] * (1 + step)
            lo = DEFAULT_WEIGHTS[name] * MIN_FACTOR
            hi = DEFAULT_WEIGHTS[name] * MAX_FACTOR
            bounded = max(lo, min(hi, proposed))
            if abs(bounded - current[name]) < 1e-9:
                continue
            new_weights[name] = round(bounded, 4)
            rationale.append(
                f"{name}: win rate {stats.win_rate:.0%} vs baseline {baseline:.0%} "
                f"over {stats.trades} trades -> weight {current[name]:.2f} -> "
                f"{new_weights[name]:.2f}"
            )
        return new_weights, rationale


class WeightStore:
    """Versioned persistence for learned weights: data/learning/weights.json
    holds the current set plus the full audit history."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def current(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        return doc.get("weights", {})

    def version(self) -> int:
        if not self._path.exists():
            return 0
        return json.loads(self._path.read_text(encoding="utf-8")).get("version", 0)

    def save(self, weights: dict[str, float], rationale: list[str]) -> int:
        doc = {"version": 0, "history": []}
        if self._path.exists():
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        version = doc.get("version", 0) + 1
        entry = {
            "version": version,
            "updated": datetime.now(timezone.utc).isoformat(),
            "weights": weights,
            "rationale": rationale,
        }
        doc["version"] = version
        doc["weights"] = weights
        doc["updated"] = entry["updated"]
        doc.setdefault("history", []).append(entry)
        self._path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        log.info("learned weights v%d saved (%d rationale items)", version, len(rationale))
        return version
