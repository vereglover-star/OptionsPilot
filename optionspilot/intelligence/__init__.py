"""The Trading Intelligence Engine — OptionsPilot's analytical brain.

One layer, one job: turn everything the system already records about completed
trades into structured, evidence-backed insight that every other part of the
application consumes rather than recomputes.

    journal.db ─┐
  experience.db ─┼─► facts.build_facts ─► TradeFact ─► TradingIntelligence.analyze
  coach/*.json ─┘                                              │
                                                               ▼
                                                    IntelligenceSnapshot
                                                               │
        ┌────────────┬──────────┬──────────┬─────────┬─────────┴────────┐
     Dashboard    Coach     Journal    Learning   Reports        (future: mobile,
                                                                  cloud sync, ML)

The engines are pure functions of a fact list — no I/O, no clock beyond the one
timestamp passed in, no randomness — which is what makes the same code testable
offline, reusable per-window, and safe to run on a background thread.

**What this layer will not do**, and the reasons are load-bearing:

* It never places, blocks or modifies a trade. `risk/manager.py` is the gate;
  this is analysis. Nothing here is consulted on the trading hot path.
* It never invents. A metric that cannot be computed is None, a behaviour that
  cannot be assessed says so with the reason, and a conclusion without measured
  evidence is not produced at all.
* It never imports upward. `intelligence/` depends on `core` and `experience`
  only; the coach and the UI depend on *it*, which is what lets the AI Coach
  become a presentation layer over this engine rather than a parallel one.

Full design, data flow and extension points: `docs/TRADING_INTELLIGENCE.md`.
"""

from optionspilot.intelligence.achievements import AchievementEngine
from optionspilot.intelligence.behavior import BEHAVIORS, BehaviorEngine
from optionspilot.intelligence.confidence import ConfidenceEngine, ScoreInput
from optionspilot.intelligence.curriculum import CURRICULUM, CurriculumEngine
from optionspilot.intelligence.engine import (
    TradingIntelligence, build_evidence_index, confidence_of, empty_snapshot,
)
from optionspilot.intelligence.facts import FactSet, TradeFact, build_facts
from optionspilot.intelligence.goals import GoalEngine, TEMPLATES
from optionspilot.intelligence.models import (
    Achievement, BehaviorFinding, Confidence, Evidence, Goal, GoalProgress,
    Impact, IntelligenceSnapshot, LessonRecommendation, Metric, Pattern,
    PeriodStat, Recommendation, Report, ScoreCard, Severity, TimelineEntry,
    Trend,
)
from optionspilot.intelligence.patterns import DIMENSIONS, PatternEngine
from optionspilot.intelligence.performance import METRIC_SPECS, PerformanceEngine
from optionspilot.intelligence.recommend import RecommendationEngine
from optionspilot.intelligence.reports import ReportEngine
from optionspilot.intelligence.risk import RiskIntelligence
from optionspilot.intelligence.store import IntelligenceStore
from optionspilot.intelligence.timeline import TimelineEngine

__all__ = [
    "AchievementEngine", "Achievement", "BEHAVIORS", "BehaviorEngine",
    "BehaviorFinding", "CURRICULUM", "Confidence", "ConfidenceEngine",
    "CurriculumEngine", "DIMENSIONS", "Evidence", "FactSet", "Goal",
    "GoalEngine", "GoalProgress", "Impact", "IntelligenceSnapshot",
    "IntelligenceStore", "LessonRecommendation", "METRIC_SPECS", "Metric",
    "Pattern", "PatternEngine", "PeriodStat", "PerformanceEngine",
    "Recommendation", "RecommendationEngine", "Report", "ReportEngine",
    "RiskIntelligence", "ScoreCard", "ScoreInput", "Severity", "TEMPLATES",
    "TimelineEngine", "TimelineEntry", "TradeFact", "TradingIntelligence",
    "Trend", "build_evidence_index", "build_facts", "confidence_of",
    "empty_snapshot",
]
