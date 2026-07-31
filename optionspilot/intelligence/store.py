"""Persistence for the intelligence layer — which is deliberately almost nothing.

The only thing worth storing is what the *user* decided: their goals. Every
analysis output — scores, behaviors, patterns, achievements, reports — is
derived on read and stored nowhere, for the reason this codebase has now learned
three times (`data/health.py` in V0.5.3, the settings ranking in V0.5.7): two
objects tracking one fact will drift, and here the drift would take the form of
a dashboard showing yesterday's verdict about today's trades. Recomputing costs
milliseconds; being wrong costs trust.

`goals.json` is a file a user can open, and therefore a file a user will edit
badly. `apply` validates the *shape* of every field and drops what it cannot
read: the failure mode must be "you lose a goal", never "the app will not
start". That is the same rule `data/control.py::apply_control_state` was
rewritten to follow after `{"providers": [1,2]}` took the whole app down at the
composition root.
"""

from __future__ import annotations

import json
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.intelligence.goals import validate
from optionspilot.intelligence.models import Goal

log = get_logger("intelligence")

# A hard ceiling so a corrupted or scripted file cannot make every snapshot
# evaluate ten thousand goals.
MAX_GOALS = 50


class IntelligenceStore:
    """Owns `<data>/intelligence/goals.json`."""

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)
        self._path = self._dir / "goals.json"

    @property
    def path(self) -> Path:
        return self._path

    # ── goals ────────────────────────────────────────────────────────────

    def load_goals(self) -> list[Goal]:
        """Every readable goal in the file. Never raises: a missing file, a
        truncated write, a hand-edited list of integers and a JSON object where
        an array belongs all produce an empty list plus a log line."""
        if not self._path.exists():
            return []
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("goals.json unreadable (%s) — starting with no goals", exc)
            return []
        raw = doc.get("goals") if isinstance(doc, dict) else doc
        if not isinstance(raw, list):
            log.warning("goals.json has no goal list — starting with no goals")
            return []
        out: list[Goal] = []
        seen: set[str] = set()
        for entry in raw[:MAX_GOALS]:
            if not isinstance(entry, dict):
                continue
            goal = Goal.from_dict(entry)
            if goal is None or goal.id in seen:
                continue
            # Shape is not enough: a goal naming a metric this build does not
            # measure can never evaluate, and would sit on the page reading
            # "no data" forever with no way for the user to tell why.
            problem = validate(goal)
            if problem:
                log.warning("goals.json: dropping %r — %s", goal.id, problem)
                continue
            seen.add(goal.id)
            out.append(goal)
        if len(out) < len([e for e in raw if isinstance(e, dict)]):
            log.warning("goals.json: %d entr(y/ies) were malformed and skipped",
                        len(raw) - len(out))
        return out

    def save_goals(self, goals: list[Goal]) -> None:
        """Write the goal list. Creates the directory on demand — the
        intelligence directory is not created at startup, because a user who
        never opens the Goals panel should not accumulate empty directories."""
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "goals": [g.to_dict() for g in goals[:MAX_GOALS]]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        log.info("saved %d intelligence goal(s)", len(payload["goals"]))
