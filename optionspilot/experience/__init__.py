"""Experience Engine (V0.4.0) — the AI's long-term trading memory.

This subsystem is ADDITIVE: it records a rich, schema-versioned superset of
every completed paper trade into its own store (`data/experience.db`),
alongside — never instead of — the journal. The journal remains the system of
record and the sole input to the weight-learning system; the experience store
exists to answer *similarity* questions the journal was never shaped for
("show me the 43 trades most like this setup, and how they turned out").

Design invariants (see docs/ROADMAP-V0.4-EXPERIENCE.md for the full rationale):
  - Deterministic and auditable. Similarity is a hand-authored weighted
    distance, not a trained model. No LLM, no fitted parameters.
  - Advisory only. The calibrated-confidence number this subsystem produces is
    surfaced for explanation/dashboards; it is NEVER fed back into the live
    trading gate. The deterministic scorer stays the sole trading input.
  - Non-breaking. Recording is best-effort; a failure here can never disrupt
    journaling, risk accounting, or the trading path.
  - Expandable. New per-trade fields land in the `extra` JSON blob with no
    schema migration; structural changes go through the versioned migrations
    in ExperienceStore.
"""

from optionspilot.experience.engine import ExperienceEngine
from optionspilot.experience.models import (
    ExperienceRecord, SimilarityResult, SimilarTrade,
)
from optionspilot.experience.similarity import SimilarityEngine
from optionspilot.experience.snapshot import build_snapshot
from optionspilot.experience.store import ExperienceStore

__all__ = [
    "ExperienceEngine",
    "ExperienceRecord",
    "ExperienceStore",
    "SimilarityEngine",
    "SimilarityResult",
    "SimilarTrade",
    "build_snapshot",
]
