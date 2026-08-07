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
    #: What pressing this chip will do, in a sentence, BEFORE it is pressed
    #: (M4-C6). A shortcut whose rule is invisible is a shortcut a beginner
    #: cannot learn from and an experienced trader will not trust — §6.3's
    #: whole claim is that "the chain then teaches them what the chip chose",
    #: which requires the chip to have said what it was going to choose.
    #: It lives beside the rule it describes rather than in `index.html`,
    #: because a description that drifts from its rule is worse than none.
    description: str = ""


#: The four chips, in the order §6.3 lists them. One tuple, so the client
#: renders the catalogue rather than restating it — the drift `guide.py`'s
#: two-way catalogue assertion exists to prevent.
INTENTS: tuple[Intent, ...] = (
    Intent(ATM_CALL, "ATM call", right="call",
           description="The call whose strike is nearest the current price, "
                       "on the expiry you already have open."),
    Intent(ATM_PUT, "ATM put", right="put",
           description="The put whose strike is nearest the current price, "
                       "on the expiry you already have open."),
    #: 30 days is the conventional "about a month out" swing horizon, and the
    #: one §6.1 names as the overwhelmingly common intent.
    Intent(DAY_30, "30 day", target_dte=30,
           description="Moves to the listed expiry closest to 30 days out, "
                       "then takes the strike nearest the current price."),
    #: A week. Not "the nearest expiry": on a Thursday the nearest expiry can
    #: be tomorrow, and a chip labelled "Weekly" landing on a 1-DTE contract
    #: is not what it says on the chip.
    Intent(WEEKLY, "Weekly", target_dte=7,
           description="Moves to the listed expiry closest to 7 days out — "
                       "not simply the nearest one, which on a Thursday can "
                       "be tomorrow."),
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


def _explain(intent: Intent, *, symbol: str, strike: float, right: str,
             spot: float, dte) -> str:
    """Why THIS contract, in one sentence (M4-C6).

    §6.3's promise is that "the chain then teaches them what the chip chose",
    and a chip that produces a contract without saying how chose it is the
    magic the prompt for this milestone names as the thing to avoid. Both
    halves of the rule are stated, because an intent resolves two axes and a
    user who disagrees with the result needs to know which half to argue with.

    Built here rather than in the client for the same reason `description` is:
    it describes a rule this module owns, and §6.3 requires Pilot and the AI
    engine to express the same intents. A second wording in `index.html` would
    be the suggestion and the action explaining themselves differently.
    """
    word = "call" if right == "call" else "put"
    where = f"the {word} struck nearest {symbol} at ${spot:,.2f}"
    if intent.target_dte is None:
        when = "on the expiry you already had open"
    else:
        days = "expiring today" if dte == 0 else (
            f"{dte} day{'s' if dte != 1 else ''} out" if dte is not None
            else "on the chosen expiry")
        when = (f"{days} — the listed expiry closest to "
                f"{intent.target_dte} days")
    return f"${strike:g} — {where}, {when}."


def contract_for(intent: Intent, rows, spot, *, current_right: str = "call",
                 expiration: str = "", dte: int | None = None,
                 symbol: str = "") -> QuickPickView:
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
    resolved_dte = dte if dte is not None else row.get("dte")
    return QuickPickView(
        ok=True, intent=intent.key, right=right, expiration=expiration,
        dte=resolved_dte,
        strike=float(row["strike"]), mid=row.get("mid"), bid=row.get("bid"),
        ask=row.get("ask"), delta=row.get("delta"),
        explanation=_explain(intent, symbol=symbol or "the underlying",
                             strike=float(row["strike"]), right=right,
                             spot=float(spot), dte=resolved_dte))


def catalogue() -> list[dict]:
    """The four chips as primitives, for a client to render."""
    return [{"key": i.key, "label": i.label, "right": i.right or "",
             "target_dte": i.target_dte, "description": i.description}
            for i in INTENTS]
