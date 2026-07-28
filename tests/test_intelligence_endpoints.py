"""The `/api/intelligence/*` surface and its orchestrator composition.

Every route projects the SAME snapshot — that is the milestone's central claim,
and the tests below check it holds rather than trusting the docstrings: the
dashboard summary, the coach panel and the journal badges must all be readings
of one analysis, or two screens will eventually disagree about the same trader.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from optionspilot.config.settings import AppConfig
from optionspilot.core.models import Direction, TradeRecord
from optionspilot.ui.server import PERIOD_LIMITS, SUMMARY_METRICS, create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(AppConfig(), data_dir=tmp_path))


@pytest.fixture
def loaded(tmp_path):
    """A client whose journal holds a real history, written through the real
    TradeJournal so the fact adapter is exercised end to end."""
    app = create_app(AppConfig(), data_dir=tmp_path)
    server = app.state.server
    base = datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc)
    for i in range(60):
        won = i % 3 != 0
        entry = base + timedelta(days=i)
        server.orch.journal.record(TradeRecord(
            id=f"T{i:03d}", symbol="SPY" if i % 2 else "QQQ",
            contract_symbol=f"SPY26041{i % 9}C00500000",
            direction=Direction.LONG if i % 2 else Direction.SHORT,
            strategy="manual", quantity=1,
            entry_ts=entry, entry_price=2.0,
            exit_ts=entry + timedelta(hours=2),
            exit_price=4.0 if won else 1.0,
            commissions=1.0, confidence=60.0,
            entry_reasons=["structure"], exit_reason="target: reached",
            market_conditions={"dte": "14", "htf_trend": "up"},
            indicators_used=["rsi"],
        ))
    return TestClient(app)


class TestEmptyInstall:
    def test_every_route_answers_on_a_fresh_install(self, client):
        """A brand-new user must never see a 500 from the analytics layer."""
        for url in ("/api/intelligence", "/api/intelligence/summary",
                    "/api/intelligence/reports", "/api/intelligence/goals",
                    "/api/intelligence/trade/anything"):
            assert client.get(url).status_code == 200, url

    def test_summary_is_renderable_with_no_trades(self, client):
        doc = client.get("/api/intelligence/summary").json()
        assert doc["trades_analyzed"] == 0
        assert doc["data_sufficiency"] == "none"
        assert doc["metrics"]
        assert doc["notes"]

    def test_unknown_trade_is_a_200_with_a_reason_not_a_404(self, client):
        """The journal row exists in the UI; the analysis simply hasn't seen it.
        A 404 would read as 'this trade does not exist'."""
        doc = client.get("/api/intelligence/trade/nope").json()
        assert doc["available"] is False and doc["reason"]


class TestFullPayload:
    def test_carries_every_section(self, loaded):
        doc = loaded.get("/api/intelligence").json()
        for key in ("metrics", "periods", "behaviors", "patterns", "scores",
                    "risk", "goals", "recommendations", "lessons", "timeline",
                    "achievements", "reports", "notes"):
            assert key in doc, key
        assert doc["trades_analyzed"] == 60

    def test_period_series_are_trimmed_at_the_transport_boundary(self, loaded):
        """The snapshot holds every period; a five-year daily series is
        thousands of points. Trimming happens in the server so nothing in
        intelligence/ has to know a UI exists."""
        doc = loaded.get("/api/intelligence").json()
        for name, limit in PERIOD_LIMITS.items():
            assert len(doc["periods"][name]) <= limit

    def test_full_false_drops_only_the_unbounded_section(self, loaded):
        """The Coach tab draws behaviours, patterns, reports and the timeline;
        the one thing it never draws is a time series, which is also the only
        part of the payload that grows without bound."""
        doc = loaded.get("/api/intelligence?full=false").json()
        assert "periods" not in doc
        assert doc["scores"] and doc["behaviors"] and "reports" in doc

    def test_payload_is_valid_json_with_no_infinities(self, loaded):
        raw = loaded.get("/api/intelligence").text
        assert "Infinity" not in raw and "NaN" not in raw
        assert json.loads(raw)

    def test_duration_is_reported(self, loaded):
        assert loaded.get("/api/intelligence").json()["duration_ms"] >= 0


class TestSummaryProjection:
    def test_carries_the_declared_headline_metrics(self, loaded):
        doc = loaded.get("/api/intelligence/summary").json()
        keys = {m["key"] for m in doc["metrics"]}
        assert keys == set(SUMMARY_METRICS)

    def test_is_substantially_smaller_than_the_full_payload(self, loaded):
        full = len(loaded.get("/api/intelligence").text)
        summary = len(loaded.get("/api/intelligence/summary").text)
        assert summary < full

    def test_agrees_with_the_full_payload_on_shared_figures(self, loaded):
        """Two screens disagreeing about the same trader is the failure this
        whole layer exists to prevent."""
        full = loaded.get("/api/intelligence").json()
        summary = loaded.get("/api/intelligence/summary").json()
        assert summary["trades_analyzed"] == full["trades_analyzed"]
        assert summary["scores"] == full["scores"]
        assert summary["goals"] == full["goals"]
        full_expectancy = full["metrics"]["expectancy"]["value"]
        summary_expectancy = next(m["value"] for m in summary["metrics"]
                                  if m["key"] == "expectancy")
        assert summary_expectancy == full_expectancy


class TestCoachAndJournalIntegration:
    def test_coach_serves_the_same_analysis(self, loaded):
        coach = loaded.get("/api/coach").json()
        assert "intelligence" in coach
        assert coach["intelligence"]["scores"] == \
            loaded.get("/api/intelligence").json()["scores"]

    def test_coach_keeps_its_own_dashboard_beside_the_engine(self, loaded):
        """They answer different questions over different windows, so they sit
        side by side and the tab labels which is which."""
        coach = loaded.get("/api/coach").json()
        assert "dashboard" in coach and "intelligence" in coach

    def test_journal_rows_carry_their_finding_badges(self, loaded):
        doc = loaded.get("/api/journal?last=200").json()
        assert "findings" in doc
        assert all(isinstance(v, list) for v in doc["findings"].values())

    def test_findings_are_restricted_to_the_returned_rows(self, loaded):
        doc = loaded.get("/api/journal?last=5").json()
        returned = {t["id"] for t in doc["trades"]}
        assert set(doc["findings"]) <= returned

    def test_trade_insight_projects_a_real_trade(self, loaded):
        doc = loaded.get("/api/intelligence/trade/T030").json()
        assert doc["available"] is True
        assert doc["percentile"] is not None
        assert isinstance(doc["observations"], list)


class TestGoalRoutes:
    def test_templates_and_metric_vocabulary_are_offered(self, client):
        doc = client.get("/api/intelligence/goals").json()
        assert doc["templates"]
        assert doc["metrics"]
        assert all("key" in m and "label" in m for m in doc["metrics"])

    def test_add_list_and_delete(self, client):
        payload = {"id": "g1", "label": "R above 2", "metric": "avg_r",
                   "comparator": ">=", "target": 2.0,
                   "window": "last_20_trades", "unit": "R"}
        added = client.post("/api/intelligence/goals", json=payload)
        assert added.status_code == 200
        assert [g["id"] for g in added.json()["goals"]] == ["g1"]

        listed = client.get("/api/intelligence/goals").json()
        assert [g["id"] for g in listed["goals"]] == ["g1"]
        assert any(t["id"] == "avg_r_above_2" for t in listed["templates"])

        assert client.delete("/api/intelligence/goals/g1").status_code == 200
        assert client.get("/api/intelligence/goals").json()["goals"] == []

    def test_a_template_reports_whether_it_is_already_added(self, client):
        client.post("/api/intelligence/goals",
                    json={"id": "avg_r_above_2", "metric": "avg_r",
                          "comparator": ">=", "target": 2.0})
        templates = client.get("/api/intelligence/goals").json()["templates"]
        assert next(t for t in templates if t["id"] == "avg_r_above_2")["added"]

    @pytest.mark.parametrize("payload,reason", [
        ({"id": "x", "metric": "invented", "comparator": ">=", "target": 1},
         "unknown metric"),
        ({"id": "x", "metric": "avg_r", "comparator": "~", "target": 1},
         "comparator"),
        ({"id": "x", "metric": "avg_r", "comparator": ">=", "target": 1,
          "window": "last_century"}, "window"),
        ({"metric": "avg_r", "comparator": ">=", "target": 1}, "id"),
        ({"id": "x", "metric": "avg_r", "comparator": ">=", "target": "abc"},
         "id"),
    ])
    def test_a_bad_goal_is_refused_with_an_explanation(self, client, payload,
                                                       reason):
        response = client.post("/api/intelligence/goals", json=payload)
        assert response.status_code == 422
        assert reason in response.json()["error"]

    def test_deleting_a_goal_that_does_not_exist_is_a_404(self, client):
        assert client.delete("/api/intelligence/goals/ghost").status_code == 404

    def test_goals_survive_a_restart(self, tmp_path):
        first = TestClient(create_app(AppConfig(), data_dir=tmp_path))
        first.post("/api/intelligence/goals",
                   json={"id": "keep", "label": "Keep", "metric": "expectancy",
                         "comparator": ">=", "target": 0.0})
        second = TestClient(create_app(AppConfig(), data_dir=tmp_path))
        assert [g["id"] for g in
                second.get("/api/intelligence/goals").json()["goals"]] == ["keep"]


class TestOrchestratorComposition:
    def test_the_orchestrator_owns_one_intelligence_engine(self, tmp_path):
        from optionspilot.orchestrator import Orchestrator
        orch = Orchestrator(AppConfig(), data_dir=tmp_path)
        assert orch.intelligence is not None
        assert orch.intelligence_snapshot().trades_analyzed == 0

    def test_construction_runs_no_analysis(self, tmp_path):
        """Building the engine must add nothing measurable to startup — the
        fact provider is a closure it calls on the first read."""
        from optionspilot.orchestrator import Orchestrator
        orch = Orchestrator(AppConfig(), data_dir=tmp_path)
        assert orch.intelligence.last_duration_ms == 0.0

    def test_the_fingerprint_moves_when_a_trade_is_journaled(self, tmp_path):
        from optionspilot.orchestrator import Orchestrator
        orch = Orchestrator(AppConfig(), data_dir=tmp_path)
        before = orch._intelligence_fingerprint()
        orch.journal.record(TradeRecord(
            id="F1", symbol="SPY", contract_symbol="SPY", direction=Direction.LONG,
            strategy="manual", quantity=1,
            entry_ts=datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc),
            entry_price=1.0,
            exit_ts=datetime(2026, 4, 6, 15, 0, tzinfo=timezone.utc),
            exit_price=2.0, commissions=0.0, confidence=50.0))
        assert orch._intelligence_fingerprint() != before

    def test_a_journaled_trade_reaches_the_analysis(self, loaded):
        assert loaded.get("/api/intelligence").json()["trades_analyzed"] == 60

    def test_repeated_reads_do_not_reanalyse(self, loaded):
        """A page polling four intelligence routes must cost one analysis."""
        loaded.get("/api/intelligence")
        first = loaded.get("/api/intelligence").json()["duration_ms"]
        loaded.get("/api/intelligence/summary")
        second = loaded.get("/api/intelligence").json()["duration_ms"]
        assert first == second
