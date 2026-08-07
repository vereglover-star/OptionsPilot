"""Chain presentation conventions (UI V2 M4-C5).

Two figures §6.4 asks the chain for, and one flag PRODUCT_STANDARDS.md §3.3
records as debt. All three are conventions rather than measurements, which is
why they are here in Python and not in the chain renderer — a break-even the
chain and the review modal disagreed about would be the fifth
one-fact-two-owners defect this repository has paid for.
"""
from __future__ import annotations

from optionspilot.services import chain, review


class TestBreakeven:
    def test_a_call_breaks_even_above_its_strike_by_the_premium(self):
        assert chain.breakeven(strike=470.0, premium=3.99, right="call") == 473.99

    def test_a_put_breaks_even_below_its_strike_by_the_premium(self):
        assert chain.breakeven(strike=470.0, premium=3.99, right="put") == 466.01

    def test_no_premium_is_no_breakeven_rather_than_the_strike(self):
        # Returning the strike would say "you break even by doing nothing",
        # which is false for every long option ever listed.
        assert chain.breakeven(strike=470.0, premium=None, right="call") is None
        assert chain.breakeven(strike=470.0, premium=0.0, right="call") is None

    def test_the_review_modal_uses_this_function_not_a_copy_of_it(self):
        """One owner, asserted rather than assumed.

        This is the assertion that fails if someone re-inlines the arithmetic
        into `review.py` — which is how the two would drift apart while
        continuing to agree on the day it was done.
        """
        got = review.review(side="buy_to_open", kind="market", quantity=1,
                            symbol="SPY", strike=470.0, right="call",
                            expiration="2026-09-12", dte=7, bid=3.85, ask=3.95,
                            mid=3.90, spot=471.20, equity=10000.0,
                            slippage_pct=0.01)
        assert got.breakeven == chain.breakeven(
            strike=470.0, premium=got.premium, right="call")


class TestChanceITM:
    def test_it_reads_delta_as_a_percentage(self):
        assert chain.chance_itm(0.54) == 54.0

    def test_a_puts_negative_delta_is_read_by_magnitude(self):
        assert chain.chance_itm(-0.54) == 54.0

    def test_it_never_exceeds_certainty(self):
        # A deep-ITM contract can carry a delta fractionally over 1.0 from a
        # provider or from a solve; "104% chance" is not a thing.
        assert chain.chance_itm(1.04) == 100.0

    def test_an_unknown_delta_is_absent_not_zero_percent(self):
        # `OptionContract.delta` is 0.0 when nothing supplied or solved one.
        # "0% chance" would be a claim about a contract nobody measured.
        assert chain.chance_itm(0.0) is None
        assert chain.chance_itm(None) is None

    def test_the_approximation_is_stated_rather_than_implied(self):
        assert "not a forecast" in chain.CHANCE_ITM_NOTE
