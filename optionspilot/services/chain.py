"""Chain presentation — the conventions the option chain reads by.

`UI_V2_DESIGN.md` §6.4 asks the chain for two figures the payload has never
carried: **break-even** at Guided level, and a plain-language *"chance of
finishing in the money"* reading of delta. §8.2 then makes the column set a
function of Surface Level, with the underlying data identical at every level.

Both figures are CONVENTIONS rather than market observations, which is why
they live in Python beside `expiry.py` rather than being three lines of
JavaScript in the chain renderer. This repository has paid four times for a
convention with two owners — provider health (V0.5.3), the settings ranking
(V0.5.7), the guide catalogue (V0.6.1) and days-to-expiry (M4) — and a
break-even that the chain and the review modal disagree about would be the
fifth, on the same screen, for the same contract.

Three decisions.

**Break-even is computed from the EXPECTED FILL, not the mid.** A chain row's
break-even answers "if I bought this now, where does it stop losing money",
and the price you buy at is the ask plus slippage — `PaperBroker`'s model,
mirrored by `review.estimate_premium`. `breakeven()` here is the single
implementation and `review.review()` calls it, so the number in the chain and
the number in the review modal are produced by one function rather than by
two that agree today.

**Chance-ITM is |delta|, and it is labelled as an approximation.** Delta is
N(d1); the risk-neutral probability of finishing in the money is N(d2), and
they are not the same number. The machinery to compute N(d2) exists in
`analysis/options_metrics`, and using it was rejected: §6.4 asks for "a
plain-language reading of DELTA", the delta is already on screen at Focused
level and above, and a second probability that disagrees with the delta beside
it would be two owners of one idea for no reader benefit. What honesty
requires instead is that the screen never calls it a forecast —
`CHANCE_ITM_NOTE` is that sentence, and it travels with the column.

**Derived greeks are marked.** `chain_payload` runs `enrich_greeks` when a
provider returns a zero delta, so some rows carry model output and some carry
market observation. PRODUCT_STANDARDS.md §3.3 records showing them
indistinguishably as debt (D3): "a screen showing a computed delta beside a
provider delta with no distinction is stating a model output as a market
observation". `greeks_derived` is the flag that closes it. It is a fact the
service already knows and was throwing away.
"""

from __future__ import annotations

#: The honesty line for the chance-ITM column. Delta is N(d1) and the
#: probability of finishing in the money is N(d2); the two diverge most
#: exactly where a beginner is most likely to be reading this column, which is
#: near the money on a short-dated contract.
CHANCE_ITM_NOTE = (
    "Approximated from delta. It is what the market's own pricing implies, "
    "not a forecast, and not a promise."
)

#: The provenance line for a row whose greeks were computed rather than
#: supplied. PRODUCT_STANDARDS.md §3.3.
DERIVED_GREEKS_NOTE = (
    "Calculated from this contract's price, because the data provider sent "
    "no greeks for it."
)


def breakeven(*, strike, premium, right: str):
    """Where an opening trade stops losing money at expiry.

    `premium` is the per-contract price the order is expected to FILL at, not
    the mid — see the module docstring. Returns `None` when there is no
    premium to add, because a break-even computed from an absent price is a
    confidently wrong number, and this codebase treats one of those as worse
    than an absent one.
    """
    if premium is None or not isinstance(premium, (int, float)) or premium <= 0:
        return None
    if not isinstance(strike, (int, float)):
        return None
    strike = float(strike)
    return round(strike + premium, 2) if right == "call" \
        else round(strike - premium, 2)


def chance_itm(delta):
    """A percentage reading of delta, or `None`.

    Zero is returned as `None` rather than `0.0`. A delta of exactly zero is
    what `OptionContract` carries when a provider sent no greeks AND
    `enrich_greeks` could not solve an implied volatility — so it means "not
    known", and printing "0% chance" would be this module asserting something
    about a contract nobody measured. §3.3: "a greek that cannot be computed
    is blank with a reason. Never `0.000`, which is a legitimate value."

    A genuine far-out-of-the-money delta rounds to 0.0% at one decimal and is
    displayed as such; the distinction preserved here is between *known and
    tiny* and *not known at all*.
    """
    if delta is None or not isinstance(delta, (int, float)):
        return None
    if delta == 0.0:
        return None
    return round(min(abs(float(delta)), 1.0) * 100, 1)
