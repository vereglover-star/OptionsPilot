"""The status line — the one sentence a user is entitled to trust completely.

`UI_V2_DESIGN.md` §5.3 calls this the most important sentence in the product,
because it is the first thing read on every launch. Its grammar is fixed:
*[time context]. [market context]. [what needs you].*

Three rules govern everything below.

**It never says "nothing needs you" unless nothing does.** That is the whole
value of the sentence. A status line that reassures by default is worse than no
status line, because a user who learns it lies stops reading it and then misses
the one time it was right. Every "quiet" phrasing in this module is reachable
only after every alarming case has been ruled out, and `needs_you` is carried on
the view model so a caller cannot render calm text next to an alarm badge.

**Precedence is a decision this module makes, and the design documents do not.**
§5.3 enumerates eight situations and the sentence each produces; it does not say
which wins when several are true at once, and in production several usually are.
The order in `_CASES` is by consequence, matching P4: a halt outranks a rejected
order, which outranks an approaching stop, which outranks degraded quotes,
because that is the order in which a user would want to learn them if they could
only learn one. The four "something is wrong" cases all outrank the welcome,
deliberately — a first-run greeting that hid a halt would be the status line
lying on the one launch where a user has the least context to catch it.

**It is pure, and its inputs are primitives.** No domain object is imported and
no collaborator is called: `StatusInputs` is a frozen record of the facts, and
`status_line` is a total function over it. That is what lets the eight cases be
tested as eight cases rather than as eight orchestrator states, and what will
let the tray tooltip, the notification summary and mobile reuse the sentence
rather than each inventing their own (§5.3, "the system's single self-report").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from optionspilot.services.viewmodels import StatusLineView

#: Case identifiers, in precedence order. The view model carries the winner so a
#: client can style by case without re-deriving which one applied — and so a
#: test can assert the ranking rather than infer it from prose.
HALTED = "halted"
REJECTED = "rejected"
STOP_NEAR = "stop_near"
DEGRADED = "degraded"
FIRST_RUN = "first_run"
HOLDING = "holding"
IDLE_OPEN = "idle_open"
IDLE_CLOSED = "idle_closed"

CASES = (HALTED, REJECTED, STOP_NEAR, DEGRADED, FIRST_RUN, HOLDING,
         IDLE_OPEN, IDLE_CLOSED)

#: Cases in which something is genuinely waiting for the user. `DEGRADED` is
#: deliberately absent: its sentence ends "Trading continues", so it is a notice
#: about the numbers rather than a task, and marking it as needing attention
#: would make the flag mean "something is unusual" instead of "act".
NEEDS_YOU = frozenset({HALTED, REJECTED, STOP_NEAR})


@dataclass(frozen=True, slots=True)
class StatusInputs:
    """Every fact the sentence can be built from. Primitives only.

    Defaults describe a quiet, closed market with an untouched account, so a
    test states only the fact it is about and a caller that cannot answer a
    question does not have to pretend it can.
    """

    #: "morning" | "afternoon" | "evening" — the caller localises the clock,
    #: because this module has no business deciding what timezone a user is in.
    part_of_day: str = "morning"
    market_open: bool = False
    #: Minutes until the next open, when known. `None` means "closed, and I do
    #: not know when" — which reads as "Markets are closed" rather than a
    #: fabricated countdown.
    minutes_to_open: int | None = None

    positions: int = 0
    today_pnl: float = 0.0
    cleared_setups: int = 0
    account_value: float = 0.0
    #: Has this account ever closed a trade? Distinguishes a genuinely new user
    #: from one who is merely flat today, which are different sentences.
    has_traded: bool = False

    halt_reason: str = ""
    #: Human-readable reason the most recent order was refused, e.g.
    #: "insufficient buying power". Empty when nothing was refused.
    rejected_reason: str = ""
    #: (symbol, dollars_away) for the position closest to its stop, when one is
    #: close enough to be worth a sentence. The caller owns "close enough" — it
    #: is a risk judgement, not a presentation one.
    nearest_stop: tuple[str, float] | None = None
    #: (provider, reason) when quotes are degraded, e.g. ("Yahoo", "rate limited").
    degraded: tuple[str, str] | None = field(default=None)


def _money(amount: float) -> str:
    """Whole dollars with a sign. The status line is prose, so it never shows
    cents — "$212" reads, "$212.40" is a table cell in the middle of a sentence."""
    return f"{'-' if amount < 0 else ''}${abs(amount):,.0f}"


def _market_clause(inputs: StatusInputs) -> str:
    if inputs.market_open:
        return "Markets are open."
    if inputs.minutes_to_open is not None and inputs.minutes_to_open > 0:
        minutes = inputs.minutes_to_open
        if minutes >= 60:
            hours, rest = divmod(minutes, 60)
            span = f"{hours}h" if not rest else f"{hours}h {rest}m"
        else:
            span = f"{minutes}m"
        return f"Markets open in {span}."
    return "Markets are closed."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _detect(inputs: StatusInputs) -> str:
    """Which case applies. First match in precedence order wins."""
    if inputs.halt_reason:
        return HALTED
    if inputs.rejected_reason:
        return REJECTED
    if inputs.nearest_stop is not None:
        return STOP_NEAR
    if inputs.degraded is not None:
        return DEGRADED
    if not inputs.has_traded and inputs.positions == 0:
        return FIRST_RUN
    if inputs.positions > 0:
        return HOLDING
    return IDLE_OPEN if inputs.market_open else IDLE_CLOSED


def _sentence(case: str, inputs: StatusInputs) -> str:
    greeting = f"Good {inputs.part_of_day}."
    market = _market_clause(inputs)

    if case == HALTED:
        # No greeting. A pleasantry in front of "trading is halted" reads as the
        # system not understanding what it just said.
        return (f"Trading is halted: {inputs.halt_reason}. "
                f"Positions are still managed.")
    if case == REJECTED:
        return (f"One order was rejected — {inputs.rejected_reason}. "
                f"Nothing else needs you.")
    if case == STOP_NEAR:
        symbol, away = inputs.nearest_stop
        return (f"{symbol} is ${away:,.2f} from your stop. "
                f"Everything else is steady.")
    if case == DEGRADED:
        provider, reason = inputs.degraded
        return (f"Quotes are delayed — {provider} is {reason} and retrying. "
                f"Trading continues.")
    if case == FIRST_RUN:
        return (f"Welcome. Your paper account has {_money(inputs.account_value)}. "
                f"None of it is real.")
    if case == HOLDING:
        held = _plural(inputs.positions, "position")
        if inputs.today_pnl > 0:
            return f"{market} You are up {_money(inputs.today_pnl)} today across {held}."
        if inputs.today_pnl < 0:
            return f"{market} You are down {_money(abs(inputs.today_pnl))} today across {held}."
        # Flat is its own sentence. "up $0" is technically true and reads as a
        # rounding artefact rather than as a state.
        return f"{market} You are flat today across {held}."
    if case == IDLE_OPEN:
        if inputs.cleared_setups:
            return (f"{market} You have no positions and "
                    f"{_plural(inputs.cleared_setups, 'setup')} cleared the gate.")
        return f"{market} You have no positions and nothing has cleared the gate."
    return f"{greeting} {market} Nothing needs you."


def status_line(inputs: StatusInputs) -> StatusLineView:
    """The sentence, the case that produced it, and whether it needs the user."""
    case = _detect(inputs)
    return StatusLineView(text=_sentence(case, inputs), case=case,
                          needs_you=case in NEEDS_YOU)
