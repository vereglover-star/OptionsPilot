"""ExperienceEngine — the façade the orchestrator and API drive.

Owns the store and the similarity engine, and is the ONLY place outside the
store that talks to experience data — no SQL leaks past this layer. It exposes:
record a completed trade (best-effort — a failure here can never disrupt
journaling or trading); query historical memory for a setup (advisory); and the
aggregate statistics the AI Performance dashboard is built on.
"""

from __future__ import annotations

from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import TradeRecord
from optionspilot.experience.features import build_experience, build_query_record
from optionspilot.experience.models import (
    ExperienceRecord, SimilarityResult, SimilarTrade,
)
from optionspilot.experience.similarity import SimilarityEngine
from optionspilot.experience.store import ExperienceStore

log = get_logger("experience")


class ExperienceEngine:
    def __init__(self, db_path: str | Path):
        self.store = ExperienceStore(db_path)
        self.similarity = SimilarityEngine(self.store)

    def record_trade(
        self,
        trade: TradeRecord,
        entry_context: dict | None = None,
        exit_context: dict | None = None,
        *,
        timeframe: str | None = None,
        exploration: bool = False,
        extra: dict | None = None,
    ) -> ExperienceRecord | None:
        """Record a completed trade as an experience. Best-effort by contract:
        any failure is logged and swallowed so the caller's journaling/risk
        path is never affected. Returns the record, or None on failure."""
        try:
            rec = build_experience(
                trade, entry_context, exit_context,
                timeframe=timeframe, exploration=exploration, extra=extra,
            )
            self.store.record(rec)
            return rec
        except Exception as exc:  # noqa: BLE001 — recording must never break trading
            log.error("experience recording failed for %s: %s", trade.id, exc)
            return None

    # ── advisory queries (the Experience API) ─────────────────────────────

    def summarize_for(
        self, query: ExperienceRecord, **kwargs
    ) -> SimilarityResult:
        """Aggregate historical evidence for a setup record (advisory)."""
        return self.similarity.summarize(query, **kwargs)

    def explain_setup(self, snapshot: dict, **kwargs) -> SimilarityResult:
        """Advisory historical evidence for a LIVE decision snapshot
        (experience/snapshot.py). Never affects execution."""
        return self.similarity.summarize(build_query_record(snapshot), **kwargs)

    def similar_trades(
        self, query: ExperienceRecord, *, k: int = 20,
        min_similarity: float = 0.3,
    ) -> list[SimilarTrade]:
        """The Similar Trade Viewer's backing data for a setup record."""
        matches = self.similarity.find_similar(
            query, k=k, min_similarity=min_similarity)
        return [_to_similar_trade(rec, sim) for rec, sim in matches]

    def similar_to_snapshot(
        self, snapshot: dict, *, k: int = 20, min_similarity: float = 0.3,
    ) -> list[SimilarTrade]:
        """The Similar Trade Viewer's data for a LIVE decision snapshot."""
        return self.similar_trades(
            build_query_record(snapshot), k=k, min_similarity=min_similarity)

    def recent(self, limit: int = 50) -> list[ExperienceRecord]:
        """Most recent experiences, newest first."""
        return self.store.recent(limit)

    def strategy_statistics(self) -> list[dict]:
        return self.store.aggregate("strategy")

    def regime_statistics(self) -> list[dict]:
        return self.store.aggregate("market_regime")

    def statistics_by(self, dimension: str) -> list[dict]:
        """Per-group stats over any whitelisted indexed dimension (strategy,
        market_regime, market_session, volatility_bucket, setup_quality,
        direction, managed_by)."""
        return self.store.aggregate(dimension)

    def failure_modes(self, limit: int = 10) -> list[dict]:
        """Most frequent exit reasons among losing trades."""
        return self.store.exit_reason_counts(wins=False, limit=limit)

    def success_patterns(self, limit: int = 10) -> list[dict]:
        """Most frequent exit reasons among winning trades."""
        return self.store.exit_reason_counts(wins=True, limit=limit)

    def stats(self) -> dict:
        """Headline metrics for the AI Performance dashboard. SQL-backed, so it
        never drifts from the records and stays fast at scale."""
        return {**self.store.overview(),
                "schema_version": self.store.schema_version}

    def statistics(self) -> dict:
        """Full statistics bundle for the Experience API: headline overview plus
        per-dimension breakdowns and success/failure patterns."""
        return {
            "overview": self.stats(),
            "by_strategy": self.strategy_statistics(),
            "by_regime": self.regime_statistics(),
            "by_session": self.statistics_by("market_session"),
            "failure_modes": self.failure_modes(),
            "success_patterns": self.success_patterns(),
        }

    def close(self) -> None:
        self.store.close()


def _to_similar_trade(rec: ExperienceRecord, similarity: float) -> SimilarTrade:
    return SimilarTrade(
        trade_id=rec.trade_id,
        date=rec.entry_ts.date().isoformat(),
        symbol=rec.symbol,
        timeframe=rec.timeframe,
        direction=rec.direction,
        outcome="win" if rec.is_win else "loss",
        return_pct=rec.return_pct,
        confidence=rec.confidence_entry,
        similarity=similarity,
        failure_reason=(rec.exit_reason if not rec.is_win else ""),
        success_reason=(rec.exit_reason if rec.is_win else ""),
    )
