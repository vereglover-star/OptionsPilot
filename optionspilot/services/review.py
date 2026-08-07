"""Review — the consequence restatement, as data.

`UI_V2_DESIGN.md` §6.5: review is not a confirmation dialog. It is the one
place in the product where the trade is described in the language a person
would use to explain it to a friend, and where the worst case is stated
before it can happen. Five elements, in order, always:

  1. A sentence — side, quantity, symbol, strike, right, expiry, days left.
     Never an abbreviation on this line.
  2. Cost and maximum loss, in dollars, with maximum loss stated even when it
     equals cost — *especially* then.
  3. Breakeven, with spot beside it so the distance needs no arithmetic.
  4. Position size as a percentage of the account.
  5. "If you do nothing" — the passive outcome.

Plus one honesty line about how the fill will actually happen.

Three things here are decisions.

**The numbers are the ENGINE's numbers, not the mid.** `broker/paper.py` fills
a market buy at `round(ask * (1 + slippage_pct), 4)` and a market sell at
`round(bid * (1 - slippage_pct), 4)`. Quoting the mid on this screen would be
the review stating a price the system will not use — and this codebase has a
standing rule that a confidently wrong number is worse than an absent one,
because the user acts on it. `estimate_premium` is the one place that model is
mirrored, and `tests/test_services_review.py` pins it against the broker's own
arithmetic rather than against a copy of it.

**An element that does not apply says so; it never prints a wrong number.**
§6.5's five elements are written for an opening buy. A `sell_to_close`
reduces exposure — it has proceeds rather than a cost, no new maximum loss,
and no breakeven — so those fields are `None` and carry a `*_note` explaining
why. Printing "Most you can lose: $0.00" on a closing order would be the same
class of error as `intelligence/` scoring a metric it could not measure.

**It is pure.** Primitives in, a frozen view model out. No broker, no
provider, no config object — `slippage_pct` arrives as a float, which is what
lets every branch be tested as a branch rather than as an orchestrator state.
"""

from __future__ import annotations

from datetime import date

from optionspilot.services import chain
from optionspilot.services.viewmodels import ReviewView

#: One option contract covers this many shares. Not a setting: it is the
#: definition of a listed US equity option.
MULTIPLIER = 100

#: Order kinds that OPEN exposure. Everything else reduces or exits it, and
#: the review's cost/max-loss/breakeven trio is about opening.
_OPENING_SIDES = frozenset({"buy_to_open"})

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pretty_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "an unknown date"
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _money(value: float) -> str:
    return f"${value:,.2f}"


def estimate_premium(*, side: str, kind: str, bid, ask, mid,
                     limit=None, slippage_pct: float = 0.0):
    """The per-contract premium this order is expected to fill at.

    Mirrors `broker/paper.py` exactly: an immediate buy crosses to the ask and
    slippage worsens it; an immediate sell hits the bid and slippage worsens
    that too. A limit order fills AT its limit by definition, so slippage does
    not apply — the limit is the price.

    Returns `None` when the quote needed is absent, rather than falling back to
    the mid. A review that silently substitutes a different price than the one
    it is describing is the defect this module exists to prevent.
    """
    if kind == "limit":
        return float(limit) if limit else None
    opening = side in _OPENING_SIDES
    quote = ask if opening else bid
    if quote is None or not isinstance(quote, (int, float)) or quote <= 0:
        return None
    factor = (1 + slippage_pct) if opening else (1 - slippage_pct)
    return round(float(quote) * factor, 4)


def _sentence(*, side, quantity, symbol, strike, right, expiration, dte,
              kind, limit, stop):
    """Element 1. No abbreviation anywhere on this line — §6.5 is explicit."""
    verb = {"buy_to_open": "BUYING", "sell_to_close": "SELLING"}.get(
        side, side.replace("_", " ").upper())
    plural = "contract" if quantity == 1 else "contracts"
    word = "call" if right == "call" else "put"
    when = _pretty_date(expiration)
    days = "" if dte is None else (
        " (expires today)" if dte == 0
        else f" ({dte} day{'s' if dte != 1 else ''} from now)")
    head = (f"You are {verb} {quantity} {symbol} ${strike:g} {word} "
            f"{plural.replace('contract', 'contract')} expiring {when}{days}.")
    if kind == "limit" and limit:
        head += f" Only if the price reaches {_money(float(limit))}."
    elif kind in ("stop_loss", "take_profit") and stop:
        cross = "falls to" if kind == "stop_loss" else "rises to"
        head += f" Only if {symbol} {cross} ${float(stop):g}."
    elif kind == "trailing_stop" and stop:
        head += (f" Only if {symbol} falls ${float(stop):g} from its best "
                 f"price after this order is placed.")
    return head


def _if_nothing(*, side, kind, symbol, strike, right, expiration, tif, dte):
    """Element 5. The passive outcome.

    Options are the instrument where doing nothing has a consequence, and no
    retail interface says so at the moment of commitment (§6.5).
    """
    when = _pretty_date(expiration)
    if kind in ("limit", "stop_loss", "take_profit", "trailing_stop"):
        rest = ("until it fills or you cancel it"
                if tif == "gtc" else "until the end of today, then expires")
        return f"If you do nothing, this order rests {rest}."
    if side in _OPENING_SIDES:
        direction = "below" if right == "call" else "above"
        return (f"If you do nothing and {symbol} closes {direction} "
                f"${strike:g} on {when}, this contract expires worthless and "
                f"you lose everything you paid for it.")
    return ("If you do nothing, this position stays open and keeps moving "
            f"with {symbol} until {when}.")


def _guided_note(*, side, kind, symbol, right, dte, tif):
    """The ONE term most worth explaining about this particular order (M4-C7).

    §6.5: "At the Guided Surface Level, Review adds one Pilot line explaining
    the single most consequential term in the order. At Full, it does not."

    *One* line, and which one is a ranking rather than a list — a review that
    explains four things at once has explained nothing, and §8.1-2's rule that
    Guided hides complexity but never consequence cuts both ways: piling
    caveats onto the beginner's screen is its own kind of hiding.

    The ranking is by how surprising the term is when it bites, not by how
    important it sounds:

      1. Expiring today, because a beginner reading "0DTE" often does not know
         it means *hours*.
      2. An exit order's trigger, because stop/target/trail fire on the
         UNDERLYING here and not on the premium — the single most common
         wrong assumption this product's guardrails already exist to catch.
      3. A limit order may never fill, which is the failure people read as the
         app being broken.
      4. Short-dated decay, which is the thing that takes money from a
         directionally correct trade.
      5. Otherwise decay in general, for a long option.

    A closing order gets none of these: nothing about exiting a position is
    the kind of surprise this line exists for.
    """
    if kind in ("stop_loss", "take_profit", "trailing_stop"):
        return (f"This triggers on the price of {symbol} itself, not on the "
                f"option's premium.")
    if kind == "limit":
        rest = "until you cancel it" if tif == "gtc" else "at the end of today"
        return (f"A limit order can go unfilled. If the price never reaches "
                f"yours, nothing happens and the order expires {rest}.")
    if side not in _OPENING_SIDES:
        return ""
    if dte == 0:
        return ("This contract expires TODAY. After the close it is worth "
                "whatever it is in the money by, or nothing at all.")
    if isinstance(dte, int) and 0 < dte <= 7:
        word = "call" if right == "call" else "put"
        return (f"With {dte} day{'s' if dte != 1 else ''} left, time decay is "
                f"the strongest force on this {word}'s price — being right "
                f"slowly is still a loss.")
    return ("An option loses value as expiry approaches even when the "
            "underlying does not move. Time is a cost you are paying.")


def _fill_note(*, kind, side, slippage_pct, limit, delayed_minutes=15):
    """The honesty line. How the fill will ACTUALLY happen.

    Not a disclaimer — accuracy. It is what prevents the "why didn't I get
    that price" confusion that follows every paper fill.
    """
    delay = (f"against a {delayed_minutes}-minute delayed quote"
             if delayed_minutes else "against the latest quote")
    if kind == "limit":
        side_word = "ask is at or below" if side in _OPENING_SIDES else \
            "bid is at or above"
        return (f"Fills on the next scan cycle, {delay}, when the "
                f"{side_word} {_money(float(limit))}.")
    if kind in ("stop_loss", "take_profit", "trailing_stop"):
        return (f"Rests until it triggers, then sells at the bid {delay} "
                f"on the next scan cycle.")
    crossing = "ask" if side in _OPENING_SIDES else "bid"
    slip = (f", worsened by {slippage_pct * 100:g}% slippage"
            if slippage_pct else "")
    return f"Fills on the next scan cycle at the {crossing}{slip}, {delay}."


def _buying_power(*, opening: bool, cost, buying_power):
    """The buying-power impact, as (pct, after, note).

    A separate question from position size, and one the ticket must answer
    before an order can be composed: position size asks *is this too big*,
    buying power asks *can I afford this at all*. They are computed from
    different denominators — equity carries the marked value of open
    positions, buying power is cash — so on any account holding something they
    disagree, and reporting one as the other would be a confidently wrong
    number in the place a user checks before spending.

    A closing order returns cash rather than consuming it, so it has no
    impact to state and says so instead of printing a negative percentage.
    """
    if not isinstance(buying_power, (int, float)) or buying_power <= 0:
        return None, None, ("Buying power is not available right now."
                            if opening else "")
    if not opening:
        return None, None, ("This order returns cash rather than using buying "
                            "power.")
    if cost is None:
        return None, None, ""
    return (round(cost / buying_power * 100, 2),
            round(buying_power - cost, 2),
            "" if cost <= buying_power else
            "This costs more than your available buying power.")


def review(*, side: str, kind: str, quantity: int, symbol: str, strike: float,
           right: str, expiration: str, dte=None, bid=None, ask=None, mid=None,
           spot=None, equity=None, buying_power=None, limit=None, stop=None,
           tif: str = "day", slippage_pct: float = 0.0,
           delayed_minutes: int = 15) -> ReviewView:
    """The five elements, in order, for any order this product can place."""
    quantity = max(1, int(quantity or 1))
    strike = float(strike)
    opening = side in _OPENING_SIDES

    premium = estimate_premium(side=side, kind=kind, bid=bid, ask=ask, mid=mid,
                               limit=limit, slippage_pct=slippage_pct)
    notional = None if premium is None else round(
        premium * quantity * MULTIPLIER, 2)

    cost = notional if opening else None
    proceeds = None if opening else notional

    if premium is None:
        cost_note = ("There is no live quote for this contract right now, so "
                     "the cost cannot be estimated.")
    elif kind == "limit":
        cost_note = "At your limit price. A fill can only be better, never worse."
    else:
        cost_note = ""

    if opening:
        max_loss = cost
        max_loss_note = ("100% of what you pay — a long option can expire "
                         "worthless." if cost is not None else "")
    else:
        max_loss = None
        max_loss_note = ("This order reduces an existing position, so it adds "
                         "no new risk.")

    if opening and premium is not None:
        # `services/chain.py` owns the rule, so the break-even in the chain
        # row and the break-even in this modal are produced by ONE function
        # rather than by two that happen to agree (M4-C5).
        breakeven = chain.breakeven(strike=strike, premium=premium, right=right)
        breakeven_note = ""
    else:
        breakeven = None
        breakeven_note = ("" if opening else
                          "Breakeven applies to opening a position, not to "
                          "closing one.")

    position_pct = None
    position_note = ""
    if opening and cost is not None and isinstance(equity, (int, float)) \
            and equity > 0:
        position_pct = round(cost / equity * 100, 2)
    elif opening:
        position_note = ("Position size needs an account value, which is not "
                         "available right now.")
    else:
        position_note = "This order closes exposure rather than adding it."

    bp_pct, bp_after, bp_note = _buying_power(
        opening=opening, cost=cost, buying_power=buying_power)

    return ReviewView(
        sentence=_sentence(side=side, quantity=quantity, symbol=symbol,
                           strike=strike, right=right, expiration=expiration,
                           dte=dte, kind=kind, limit=limit, stop=stop),
        opening=opening,
        premium=premium,
        cost=cost, cost_note=cost_note,
        proceeds=proceeds,
        max_loss=max_loss, max_loss_note=max_loss_note,
        breakeven=breakeven, breakeven_note=breakeven_note,
        spot=float(spot) if isinstance(spot, (int, float)) and spot > 0
        else None,
        position_pct=position_pct, position_note=position_note,
        buying_power=float(buying_power)
        if isinstance(buying_power, (int, float)) and buying_power > 0
        else None,
        buying_power_pct=bp_pct, buying_power_after=bp_after,
        buying_power_note=bp_note,
        if_nothing=_if_nothing(side=side, kind=kind, symbol=symbol,
                               strike=strike, right=right,
                               expiration=expiration, tif=tif, dte=dte),
        fill_note=_fill_note(kind=kind, side=side, slippage_pct=slippage_pct,
                             limit=limit, delayed_minutes=delayed_minutes),
        guided_note=_guided_note(side=side, kind=kind, symbol=symbol,
                                 right=right, dte=dte, tif=tif),
    )
