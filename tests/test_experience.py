"""Tests for the Experience Engine foundation: feature extraction, the
scalable/migratable store, and the ExperienceEngine façade."""

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Direction, TradeRecord
from optionspilot.experience import ExperienceEngine, ExperienceStore, SimilarTrade
from optionspilot.experience.features import (
    build_experience, build_feature_vector, build_query_record, market_regime,
)
from optionspilot.experience.models import ExperienceRecord
from optionspilot.experience.store import _migration_1, _to_payload

TS = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def make_trade(tid="t1", pnl_gross=100.0, symbol="SPY", strategy="confluence_v1",
               direction=Direction.LONG, confidence=85.0, entry_ts=TS,
               indicators=("htf_trend", "vwap"),
               conditions=None) -> TradeRecord:
    exit_price = 2.00 + pnl_gross / 100
    return TradeRecord(
        id=tid, symbol=symbol, contract_symbol="SPY260731C00100000",
        direction=direction, strategy=strategy, quantity=1,
        entry_ts=entry_ts, entry_price=2.00,
        exit_ts=entry_ts + timedelta(hours=1), exit_price=exit_price,
        commissions=0.0, confidence=confidence,
        entry_reasons=["+ trend up"], exit_reason="target reached",
        market_conditions=conditions if conditions is not None
        else {"htf_trend": "up", "dte": "21", "risk_reward": "2.0",
              "hour_et": "10", "setup_quality": "good", "mode": "conservative"},
        indicators_used=list(indicators),
    )


ENTRY_CONTEXT = {
    "htf_trend": "up",
    "confidence": 80.0,
    "entry_tf": {"rsi": 55.0, "adx": 25.0, "rvol": 1.5, "pressure": 0.3,
                 "trend": "up", "consolidating": False},
    "contract": {"dte": 21, "delta": 0.5, "iv": 0.35, "spread_pct": 0.05},
    "gate": {"mode": "conservative", "setup_quality": "good"},
    "hour_et": 10, "minute_et": 30,
}

# A full centralized snapshot (as experience/snapshot.py produces it).
SNAPSHOT = {
    "symbol": "SPY", "timeframe": "5m", "direction": "long",
    "confidence": 84.0, "deterministic_score": 84.0,
    "reasoning": "trend up\nabove VWAP", "htf_trend": "up",
    "operating_mode": "ai", "trading_mode": "conservative",
    "learning_mode": "normal",
    "entry_tf": {"rsi": 60.0, "adx": 28.0, "rvol": 1.6, "pressure": 0.4,
                 "trend": "up", "consolidating": False, "atr": 1.2,
                 "ema_stack": 1, "macd_hist": 0.2, "above_vwap": True,
                 "supertrend_dir": 1, "divergence": 0},
    "contract": {"dte": 21, "delta": 0.5, "iv": 0.35, "spread_pct": 0.05},
    "gate": {"mode": "conservative", "setup_quality": "good",
             "confirmations_passed": ["trend alignment"],
             "confirmations_failed": []},
    "evidence": [{"name": "htf_trend", "detail": "up", "score": 0.8, "weight": 2.0}],
    "evidence_names": ["htf_trend", "vwap"],
    "stop": 98.0, "target": 104.0, "entry": 2.05, "risk_reward": 2.0,
    "hour_et": 10, "minute_et": 30, "setup_quality": "good", "bollinger": None,
}


class TestFeatures:
    def test_build_from_trade_only(self):
        rec = build_experience(make_trade())
        assert rec.trade_id == "t1"
        assert rec.direction == "long"
        assert rec.managed_by == "ai"
        assert rec.is_win is True
        assert rec.pnl == pytest.approx(100.0)
        # entry 2.00 -> exit 3.00 => +50%
        assert rec.return_pct == pytest.approx(50.0)
        assert rec.evidence_names == ["htf_trend", "vwap"]
        # pulled from market_conditions
        assert rec.htf_trend == "up"
        assert rec.dte == 21
        assert rec.setup_quality == "good"
        assert rec.gate_mode == "conservative"
        assert rec.hour_et == 10
        # not available on an AI trade with no indicator snapshot
        assert rec.rsi is None and rec.adx is None

    def test_build_with_rich_entry_context(self):
        rec = build_experience(make_trade(strategy="manual"),
                               entry_context=ENTRY_CONTEXT)
        assert rec.managed_by == "manual"
        assert rec.rsi == 55.0
        assert rec.adx == 25.0
        assert rec.rvol == 1.5
        assert rec.pressure == 0.3
        assert rec.iv == 0.35
        assert rec.delta == 0.5
        assert rec.entry_trend == "up"
        assert rec.consolidating is False
        assert rec.market_session == "regular"

    def test_feature_vector_normalized(self):
        rec = build_experience(make_trade(), entry_context=ENTRY_CONTEXT)
        fv = build_feature_vector(rec)
        # confidence 85/100
        assert fv["confidence"] == pytest.approx(0.85)
        assert fv["rsi"] == pytest.approx(0.55)
        assert fv["dte"] == pytest.approx(21 / 90)
        # pressure range (-1,1): 0.3 -> 0.65
        assert fv["pressure"] == pytest.approx(0.65)
        for v in fv.values():
            assert 0.0 <= v <= 1.0

    def test_feature_vector_omits_missing(self):
        rec = build_experience(make_trade(conditions={}))
        fv = build_feature_vector(rec)
        # confidence always present; rsi/adx were never captured
        assert "confidence" in fv
        assert "rsi" not in fv and "adx" not in fv

    def test_zero_entry_price_no_div_by_zero(self):
        t = make_trade()
        t.entry_price = 0.0
        rec = build_experience(t)
        assert rec.return_pct == 0.0

    def test_market_session_classification(self):
        pre = build_experience(make_trade(),
                               entry_context={"hour_et": 8, "minute_et": 0})
        assert pre.market_session == "pre"
        post = build_experience(make_trade(),
                                entry_context={"hour_et": 16, "minute_et": 30})
        assert post.market_session == "post"

    def test_volatility_bucket(self):
        assert build_experience(make_trade(),
                                entry_context={"contract": {"iv": 0.1}}
                                ).volatility_bucket == "low"
        assert build_experience(make_trade(),
                                entry_context={"contract": {"iv": 0.45}}
                                ).volatility_bucket == "medium"
        assert build_experience(make_trade(),
                                entry_context={"contract": {"iv": 0.9}}
                                ).volatility_bucket == "high"
        assert build_experience(make_trade()).volatility_bucket == "unknown"


class TestStore:
    def test_record_get_roundtrip(self, tmp_path):
        store = ExperienceStore(tmp_path / "exp.db")
        rec = build_experience(make_trade(), entry_context=ENTRY_CONTEXT)
        store.record(rec)
        got = store.get("t1")
        assert got is not None
        assert got.direction == "long"
        assert got.rsi == 55.0
        assert got.features["confidence"] == pytest.approx(0.85)
        assert got.entry_ts == TS
        assert store.count() == 1

    def test_query_filters(self, tmp_path):
        store = ExperienceStore(tmp_path / "exp.db")
        store.record(build_experience(make_trade("w1", 100.0)))
        store.record(build_experience(make_trade("l1", -50.0)))
        store.record(build_experience(make_trade(
            "s1", 30.0, direction=Direction.SHORT, symbol="QQQ",
            entry_ts=TS + timedelta(days=1))))
        assert len(store.query(symbol="SPY")) == 2
        assert [r.trade_id for r in store.query(wins_only=True)] == ["w1", "s1"]
        assert [r.trade_id for r in store.query(wins_only=False)] == ["l1"]
        assert [r.trade_id for r in store.query(direction="short")] == ["s1"]
        assert [r.trade_id for r in store.query(
            start=TS + timedelta(hours=12))] == ["s1"]

    def test_persistence_across_reopen(self, tmp_path):
        path = tmp_path / "exp.db"
        s1 = ExperienceStore(path)
        s1.record(build_experience(make_trade(), entry_context=ENTRY_CONTEXT))
        s1.close()
        s2 = ExperienceStore(path)
        assert s2.count() == 1
        assert s2.get("t1").rsi == 55.0

    def test_schema_version_and_idempotent_migration(self, tmp_path):
        path = tmp_path / "exp.db"
        s1 = ExperienceStore(path)
        assert s1.schema_version == 2
        s1.close()
        # Re-opening must not re-run migrations or lose data.
        s2 = ExperienceStore(path)
        assert s2.schema_version == 2

    def test_upsert_replaces(self, tmp_path):
        store = ExperienceStore(tmp_path / "exp.db")
        store.record(build_experience(make_trade(confidence=50.0)))
        store.record(build_experience(make_trade(confidence=90.0)))
        assert store.count() == 1
        assert store.get("t1").confidence_entry == 90.0

    def test_forward_compat_drops_unknown_payload_keys(self, tmp_path):
        """A payload written by a newer build (extra top-level key) still loads."""
        store = ExperienceStore(tmp_path / "exp.db")
        store.record(build_experience(make_trade()))
        # Simulate a newer field by injecting a bogus key into the stored JSON.
        store._conn.execute(
            "UPDATE experiences SET payload = "
            "json_set(payload, '$.some_future_field', 1) WHERE trade_id='t1'")
        store._conn.commit()
        got = store.get("t1")
        assert got is not None and got.trade_id == "t1"

    def test_newer_schema_refuses_to_open(self, tmp_path):
        path = tmp_path / "exp.db"
        s1 = ExperienceStore(path)
        s1._conn.execute("PRAGMA user_version = 999")
        s1._conn.commit()
        s1.close()
        with pytest.raises(RuntimeError, match="newer than this build"):
            ExperienceStore(path)


class TestEngine:
    def test_record_trade_and_stats(self, tmp_path):
        eng = ExperienceEngine(tmp_path / "exp.db")
        eng.record_trade(make_trade("w1", 100.0))
        eng.record_trade(make_trade("l1", -40.0))
        stats = eng.stats()
        assert stats["total"] == 2
        assert stats["win_rate"] == pytest.approx(0.5)
        assert stats["by_management"] == {"ai": 2}
        assert stats["schema_version"] == 2

    def test_stats_empty(self, tmp_path):
        eng = ExperienceEngine(tmp_path / "exp.db")
        stats = eng.stats()
        assert stats["total"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["by_management"] == {}

    def test_exploration_counted(self, tmp_path):
        eng = ExperienceEngine(tmp_path / "exp.db")
        eng.record_trade(make_trade("e1"), exploration=True)
        eng.record_trade(make_trade("n1"))
        assert eng.stats()["exploration"] == 1

    def test_record_trade_is_best_effort(self, tmp_path, monkeypatch):
        """A failure inside recording must be swallowed, never raised."""
        eng = ExperienceEngine(tmp_path / "exp.db")

        def boom(*a, **k):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(eng.store, "record", boom)
        # Must not raise — the trading/journaling path depends on this.
        assert eng.record_trade(make_trade()) is None

    def test_extra_blob_survives_roundtrip(self, tmp_path):
        eng = ExperienceEngine(tmp_path / "exp.db")
        eng.record_trade(make_trade(), extra={"screenshot_ref": "shot1.png",
                                              "news": "CPI print"})
        got = eng.store.get("t1")
        assert got.extra["screenshot_ref"] == "shot1.png"
        assert got.extra["news"] == "CPI print"


class TestSnapshotFeatures:
    def test_build_from_ai_snapshot(self):
        rec = build_experience(make_trade(), entry_context=SNAPSHOT)
        assert rec.atr == 1.2
        assert rec.ema_state == 1
        assert rec.macd_hist == pytest.approx(0.2)
        assert rec.above_vwap is True
        assert rec.supertrend_dir == 1
        assert rec.stop == 98.0 and rec.target == 104.0
        assert rec.operating_mode == "ai"
        assert rec.trading_mode == "conservative"
        assert rec.market_regime.startswith("trending-up")
        assert rec.reasoning
        # verbose breakdown preserved without bloating columns
        assert rec.extra["snapshot"]["evidence"]

    def test_build_query_record(self):
        q = build_query_record(SNAPSHOT)
        assert q.direction == "long"
        assert q.confidence_entry == 84.0
        assert q.evidence_names == ["htf_trend", "vwap"]
        assert q.rsi == 60.0
        assert q.features["rsi"] == pytest.approx(0.6)
        assert q.market_regime.startswith("trending-up")

    def test_market_regime_labels(self):
        assert market_regime("up", 0.7) == "trending-up/high-vol"
        assert market_regime("downtrend", 0.2) == "trending-down/low-vol"
        assert market_regime("range", 0.45) == "ranging/medium-vol"
        assert market_regime("range", None) == "ranging/unknown-vol"
        assert market_regime(None, None) == "unknown-trend/unknown-vol"

    def test_null_handling_for_missing_snapshot_fields(self):
        rec = build_experience(make_trade(conditions={}))
        assert rec.atr is None and rec.ema_state is None
        assert rec.macd_hist is None and rec.above_vwap is None
        assert rec.stop is None and rec.target is None
        assert rec.operating_mode is None


class TestMigrationV1toV2:
    def test_backfills_new_columns_from_payload(self, tmp_path):
        import json
        import sqlite3

        path = tmp_path / "exp.db"
        conn = sqlite3.connect(str(path))
        _migration_1(conn)
        conn.execute("PRAGMA user_version = 1")
        rec = build_experience(make_trade(), entry_context=SNAPSHOT)
        doc = json.loads(_to_payload(rec))
        doc.pop("market_regime", None)   # simulate a genuine pre-Phase-3 payload
        conn.execute(
            "INSERT INTO experiences (trade_id,recorded_ts,entry_ts,symbol,"
            "direction,strategy,managed_by,setup_quality,market_session,"
            "volatility_bucket,exploration,is_win,confidence_entry,pnl,"
            "exit_reason,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.trade_id, rec.recorded_ts.isoformat(), rec.entry_ts.isoformat(),
             rec.symbol, rec.direction, rec.strategy, rec.managed_by,
             rec.setup_quality, rec.market_session, rec.volatility_bucket,
             0, 1 if rec.is_win else 0, rec.confidence_entry, rec.pnl,
             rec.exit_reason, json.dumps(doc)),
        )
        conn.commit()
        conn.close()

        store = ExperienceStore(path)   # triggers migration_2 + backfill
        assert store.schema_version == 2
        row = store._conn.execute(
            "SELECT market_regime, return_pct, hold_minutes FROM experiences "
            "WHERE trade_id=?", (rec.trade_id,)).fetchone()
        assert row[0] is not None and "vol" in row[0]      # recomputed regime
        assert row[1] == pytest.approx(rec.return_pct)      # backfilled from payload


class TestAggregatesAndQuery:
    def _store(self, tmp_path):
        store = ExperienceStore(tmp_path / "exp.db")
        store.record_many([
            build_experience(make_trade("w1", 100.0, entry_ts=TS),
                             entry_context=SNAPSHOT),
            build_experience(make_trade("l1", -50.0,
                                        entry_ts=TS + timedelta(hours=1)),
                             entry_context=SNAPSHOT),
            build_experience(make_trade("m1", 30.0, strategy="manual",
                                        entry_ts=TS + timedelta(hours=2))),
        ])
        return store

    def test_record_many(self, tmp_path):
        assert self._store(tmp_path).count() == 3

    def test_overview_sql(self, tmp_path):
        ov = self._store(tmp_path).overview()
        assert ov["total"] == 3
        assert ov["wins"] == 2
        assert ov["by_management"] == {"ai": 2, "manual": 1}

    def test_aggregate_by_strategy(self, tmp_path):
        rows = {r["group"]: r for r in self._store(tmp_path).aggregate("strategy")}
        assert rows["confluence_v1"]["trades"] == 2
        assert rows["manual"]["trades"] == 1

    def test_aggregate_by_regime(self, tmp_path):
        rows = self._store(tmp_path).aggregate("market_regime")
        assert any("trending-up" in r["group"] for r in rows)

    def test_aggregate_rejects_non_whitelisted_column(self, tmp_path):
        with pytest.raises(ValueError, match="cannot group by"):
            self._store(tmp_path).aggregate("payload")

    def test_recent_newest_first(self, tmp_path):
        recent = self._store(tmp_path).recent(limit=2)
        assert [r.trade_id for r in recent] == ["m1", "l1"]

    def test_exit_reason_counts(self, tmp_path):
        store = self._store(tmp_path)
        wins = store.exit_reason_counts(wins=True)
        losses = store.exit_reason_counts(wins=False)
        assert wins and wins[0]["count"] >= 1
        assert all(r["reason"] for r in wins + losses)

    def test_query_by_market_regime(self, tmp_path):
        store = self._store(tmp_path)
        regime = store.get("w1").market_regime
        assert len(store.query(market_regime=regime)) >= 1


class TestExperienceApi:
    def _engine(self, tmp_path):
        eng = ExperienceEngine(tmp_path / "exp.db")
        eng.store.record_many([
            build_experience(make_trade(f"w{i}", 80.0, entry_ts=TS), entry_context=SNAPSHOT)
            for i in range(5)
        ] + [
            build_experience(make_trade(f"l{i}", -60.0, entry_ts=TS), entry_context=SNAPSHOT)
            for i in range(2)
        ])
        return eng

    def test_statistics_bundle(self, tmp_path):
        stats = self._engine(tmp_path).statistics()
        assert stats["overview"]["total"] == 7
        assert stats["by_strategy"] and stats["by_regime"]
        assert "failure_modes" in stats and "success_patterns" in stats

    def test_strategy_and_regime_statistics(self, tmp_path):
        eng = self._engine(tmp_path)
        assert eng.strategy_statistics()[0]["trades"] == 7
        assert any("trending-up" in r["group"] for r in eng.regime_statistics())

    def test_similar_trades_view(self, tmp_path):
        eng = self._engine(tmp_path)
        rows = eng.similar_to_snapshot(SNAPSHOT, min_similarity=0.0)
        assert rows and isinstance(rows[0], SimilarTrade)
        assert rows[0].outcome in ("win", "loss")
        assert 0.0 <= rows[0].similarity <= 1.0

    def test_explain_setup_advisory(self, tmp_path):
        result = self._engine(tmp_path).explain_setup(SNAPSHOT, min_similarity=0.0)
        assert result.n_similar == 7
        assert result.has_evidence

    def test_failure_modes_and_success_patterns(self, tmp_path):
        eng = self._engine(tmp_path)
        assert eng.failure_modes()[0]["count"] == 2
        assert eng.success_patterns()[0]["count"] == 5


class TestPerformance:
    def test_similarity_stays_responsive_at_scale(self, tmp_path):
        """Bulk-load 20k experiences and confirm a similarity summarize stays
        well under budget — the indexed direction prefilter + a bounded
        Python distance pass is the design that holds at 100k+."""
        import time

        eng = ExperienceEngine(tmp_path / "exp.db")
        recs = [
            build_experience(
                make_trade(f"t{i}", 100.0 if i % 2 else -50.0, entry_ts=TS),
                entry_context=SNAPSHOT)
            for i in range(20_000)
        ]
        eng.store.record_many(recs)
        assert eng.store.count() == 20_000

        query = build_query_record(SNAPSHOT)
        start = time.perf_counter()
        result = eng.summarize_for(query, k=50, min_similarity=0.5)
        elapsed = time.perf_counter() - start
        assert result.n_similar > 0
        assert elapsed < 3.0, f"similarity summarize took {elapsed:.2f}s at 20k rows"

    def test_aggregate_is_sql_fast_at_scale(self, tmp_path):
        """Aggregate stats must not deserialize payloads — SQL only."""
        import time

        store = ExperienceStore(tmp_path / "exp.db")
        store.record_many([
            build_experience(make_trade(f"t{i}", 100.0 if i % 2 else -50.0),
                             entry_context=SNAPSHOT)
            for i in range(20_000)
        ])
        start = time.perf_counter()
        rows = store.aggregate("market_regime")
        elapsed = time.perf_counter() - start
        assert rows and rows[0]["trades"] > 0
        assert elapsed < 0.5, f"SQL aggregate took {elapsed:.2f}s at 20k rows"
