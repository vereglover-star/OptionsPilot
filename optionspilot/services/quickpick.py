"""Quick picks — turning an intent into a contract.

`UI_V2_DESIGN.md` §6.3: four chips in the empty ticket — **ATM call**, **ATM
put**, **30 day**, **Weekly** — each of which resolves an *intent* into a
concrete contract using the current symbol and spot, so that "buy a call on
this" is one click instead of a table of forty rows a beginner must first
learn to read. The chain stays fully available; a chip is a shortcut, never a
replacement.

Three things about this module are decisions rather than mechanics.

**An intent has two axes, and they are resolved separately.** "ATM call" picks
a STRIKE within whatever expiry is loaded; "30 day" picks an EXPIRY and leaves
the strike rule alone. Collapsing both into one function would force the
caller to have the chain for an expiration it has not chosen yet — which is
impossible, because choosing it is the first half of the job. So
`expiration_for` takes dates only, the caller fetches that chain, and
`contract_for` takes rows only. Each half is total over its own inputs and
testable without the other.

**It cannot fail silently.** Every outcome carries a `reason` when it did not
resolve, and the reasons are sentences a user could read: "this symbol has no
listed expirations", not `None`. A quick pick that quietly does nothing when
the chain is thin is worse than one that says why, because the user's next
move is to click it again. This mirrors `intelligence/`'s rule that
insufficient evidence is a first-class answer, and it is the same reason
`statusline.py` carries `needs_you` on its view model.

**It is pure, and its inputs are primitives.** No provider, no `OptionContract`,
no clock. `expiration_for` takes ISO strings and a date; `contract_for` takes
the same dict rows `TradingService.chain_payload` already emits. That is what
lets Pilot (M8) and the AI engine's opportunities express themselves as the
same four intents without either importing the trade screen — §6.3's "the
suggestion and the action are the same object".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from optionspilot.services.viewmodels import QuickPickView

#: Intent keys. Stable identifiers — the client names a chip by one of these,
#: exactly as `guide.py` names a tutorial by id rather than by its prose.
ATM_CALL = "atm_call"
ATM_PUT = "atm_put"
DAY_30 = "day_30"
WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class Intent:
    """What a chip means, as data.

    `right` and `target_dte` are both optional and both mean the same thing
    when absent: *leave this axis as the user set it*. "ATM call" says nothing
    about expiry and "30 day" says nothing about calls versus puts, so a chip
    that overrode the axis it does not name would silently undo a choice the
    user had already made.
    """

    key: str
    label: str
    right: str | None = None
    target_dte: int | None = None


#: The four chips, in the order §6.3 lists them. One tuple, so the client
#: renders the catalogue rather than restating it — the drift `guide.py`'s
#: two-way catalogue assertion exists to prevent.
INTENTS: tuple[Intent, ...] = (
    Intent(ATM_CALL, "ATM call", right="call"),
    Intent(ATM_PUT, "ATM put", right="put"),
    #: 30 days is the conventional "about a month out" swing horizon, and the
    #: one §6.1 names as the overwhelmingly common intent.
    Intent(DAY_30, "30 day", target_dte=30),
    #: A week. Not "the nearest expiry": on a Thursday the nearest expiry can
    #: be tomorrow, and a chip labelled "Weekly" landing on a 1-DTE contract
    #: is not what it says on the chip.
    Intent(WEEKLY, "Weekly", target_dte=7),
)

BY_KEY: dict[str, Intent] = {i.key: i for i in INTENTS}


@dataclass(frozen=True, slots=True)
class ExpiryChoice:
    """Which expiration an intent selects, and why it could not."""

    expiration: str = ""
    dte: int | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.expiration)


def _dte(iso: str, today: date) -> int | None:
    try:
        return (date.fromisoformat(iso) - today).days
    except (TypeError, ValueError):
        return None


def expiration_for(intent: Intent, expirations, today: date,
                   current: str = "") -> ExpiryChoice:
    """The expiration this intent selects, from ISO date strings alone.

    An intent with no `target_dte` keeps `current` — that is what makes "ATM
    call" a strike shortcut rather than a whole-order shortcut. If `current` is
    absent or is not among the listed expirations (a stale workspace, a symbol
    that just changed) the earliest listed one is used, because a shortcut that
    refuses to act on a recoverable inconsistency is a shortcut nobody presses
    twice.

    Ties break toward the LONGER expiry. With 28 and 32 days both a fortnight
    from a 30-day target, more time is the safer half of the tie for a control
    whose entire audience is people taking the shortcut, and the same rule
    keeps "Weekly" off a 0-DTE contract whenever a real week exists.
    """
    dated = [(e, _dte(e, today)) for e in (expirations or [])]
    dated = [(e, d) for e, d in dated if d is not None and d >= 0]
    if not dated:
        return ExpiryChoice(reason="this symbol has no listed expirations")

    if intent.target_dte is None:
        for e, d in dated:
            if e == current:
                return ExpiryChoice(expiration=e, dte=d)
        e, d = dated[0]
        return ExpiryChoice(expiration=e, dte=d)

    target = intent.target_dte
    best_e, best_d = min(dated, key=lambda ed: (abs(ed[1] - target), -ed[1]))
    return ExpiryChoice(expiration=best_e, dte=best_d)


def contract_for(intent: Intent, rows, spot, *, current_right: str = "call",
                 expiration: str = "", dte: int | None = None) -> QuickPickView:
    """The contract this intent selects, from `chain_payload`'s own rows.

    Nearest strike to spot, among the rows of the right the intent names (or
    the right the user already had, for the two intents that do not name one).

    A one-row chain resolves to that row. That is deliberate rather than an
    accident of `min()`: the thin-chain case is exactly when a user most needs
    the shortcut to work, and "nearest to spot" is still true of a set of one.
    """
    right = (intent.right or current_right or "call").lower()
    if spot is None or not isinstance(spot, (int, float)) or spot <= 0:
        return QuickPickView(
            intent=intent.key, right=right, expiration=expiration, dte=dte,
            reason="there is no spot price for this symbol right now, so "
                   "there is no at-the-money strike to pick")

    candidates = [r for r in (rows or [])
                  if str(r.get("right", "")).lower() == right
                  and isinstance(r.get("strike"), (int, float))]
    if not candidates:
        return QuickPickView(
            intent=intent.key, right=right, expiration=expiration, dte=dte,
            reason=f"this expiry lists no {right}s")

    row = min(candidates, key=lambda r: (abs(r["strike"] - spot), r["strike"]))
    return QuickPickView(
        ok=True, intent=intent.key, right=right, expiration=expiration,
        dte=dte if dte is not None else row.get("dte"),
        strike=float(row["strike"]), mid=row.get("mid"), bid=row.get("bid"),
        ask=row.get("ask"), delta=row.get("delta"))


def catalogue() -> list[dict]:
    """The four chips as primitives, for a client to render."""
    return [{"key": i.key, "label": i.label, "right": i.right or "",
             "target_dte": i.target_dte} for i in INTENTS]
