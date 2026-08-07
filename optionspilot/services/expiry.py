"""Expiry labelling — days to expiration, said the way a trading platform says it.

Audited in M4 against the convention every options platform uses, and the
audit found a real defect. `renderExpPills` in `index.html` computed:

    Math.max(0, Math.ceil((new Date(exp + "T16:00:00") - now) / 864e5))

which is a *time* delta rounded up, not a calendar-day difference. Measured
against the convention:

    expiry        now             pill showed   correct
    today         10:00           1d            0DTE      <- wrong
    today         15:59           1d            0DTE      <- wrong
    tomorrow      10:00           2d            1DTE      <- wrong
    3 days out    10:00           4d            3DTE      <- wrong
    30 days out   10:00           31d           30DTE     <- wrong

Every day except after 16:00 local on expiration day was off by one. Worse,
the same screen already displayed the CORRECT figure beside it: the chain rows
carry `dte` from `OptionContract.dte()`, which is `(expiration - today).days`
— a plain calendar difference, and right. So one screen showed "0 DTE" on a
row and "1d" on the pill for the same contract.

Two objects tracking one fact, which this codebase has paid for in
`data/health.py` (V0.5.3), the settings ranking (V0.5.7) and the guide
catalogue (V0.6.1). The fix is not a better JavaScript formula — it is
deleting the second calculation. This module is the one owner, it is pure,
and the label convention it produces is unit-tested rather than eyeballed.

The `T16:00:00` in the old code was also parsed as LOCAL time while options
expire at 16:00 *Eastern*, so the error grew with the user's distance from New
York. Calendar arithmetic has no such failure mode: a date is not an instant,
which is the same lesson `data/cache.py::_migration_3` was written for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Below this, the label says the word rather than the number. "0DTE" is the
#: term traders actually use for same-day expiry and "Today" is what a first
#: run needs to see; §1.4's anti-bifurcation rule says both audiences read the
#: same screen, so the label carries both.
_WORDS = {0: ("0DTE", "Today"), 1: ("1DTE", "Tomorrow")}


@dataclass(frozen=True, slots=True)
class Expiry:
    """One listed expiration, with everything a client needs to label it."""

    date: str
    dte: int
    label: str
    plain: str
    expired: bool = False

    def to_dict(self) -> dict:
        return {"date": self.date, "dte": self.dte, "label": self.label,
                "plain": self.plain, "expired": self.expired}


def days_to(expiration: str, today: date) -> int | None:
    """Calendar days from `today` to `expiration`. None if unparseable.

    A plain date difference, identical to `OptionContract.dte()`. Today is 0,
    tomorrow is 1, three days out is 3 — which is what every options platform
    means by DTE and what the rest of this codebase already computed.
    """
    try:
        return (date.fromisoformat(expiration) - today).days
    except (TypeError, ValueError):
        return None


def label(dte: int) -> tuple[str, str]:
    """The DTE label and its plain-language twin.

    Returns e.g. `("0DTE", "Today")`, `("1DTE", "Tomorrow")`, `("7DTE", "7
    days")`. A negative DTE is an expiry that has passed; it is labelled as
    such rather than clamped to zero, because clamping is what let the old
    code render an expired contract as though it expired today.
    """
    if dte < 0:
        n = abs(dte)
        return ("Expired", f"{n} day{'s' if n != 1 else ''} ago")
    if dte in _WORDS:
        return _WORDS[dte]
    return (f"{dte}DTE", f"{dte} days")


def describe(expirations, today: date) -> list[Expiry]:
    """Every listed expiration, labelled. Unparseable entries are dropped."""
    out: list[Expiry] = []
    for iso in expirations or []:
        dte = days_to(str(iso), today)
        if dte is None:
            continue
        short, plain = label(dte)
        out.append(Expiry(date=str(iso), dte=dte, label=short, plain=plain,
                          expired=dte < 0))
    return out
