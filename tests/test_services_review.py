"""The review view model (UI V2 M4-C2).

`UI_V2_DESIGN.md` §6.5 names five elements and one honesty line. These tests
assert each of them as a separate fact, and assert the two properties that
make the screen trustworthy rather than merely populated:

  * the premium is the price the ENGINE will fill at, pinned against
    `broker/paper.py`'s own arithmetic rather than a copy of it, and
  * an element that does not apply is `None` WITH a reason, never a zero.
"""
from __future__ import annotations


from optionspilot.services import review as rv


BASE = dict(side="buy_to_open", kind="market", quantity=1, symbol="SPY",
            strike=470.0, right="call", expiration="2026-09-12", dte=7,
            bid=3.85, ask=3.95, mid=3.90, spot=471.20, equity=10000.0,
            slippage_pct=0.01)


def _review(**over):
    return rv.review(**{**BASE, **over})


class TestThePremiumIsTheEnginesPremium:
    def test_a_market_buy_crosses_to_the_ask_and_slippage_worsens_it(self):
        # broker/paper.py: round(contract.ask * (1 + slippage_pct), 4)
        got = _review()
        assert got.premium == round(3.95 * 1.01, 4)

    def test_a_market_sell_hits_the_bid_and_slippage_worsens_that_too(self):
        got = _review(side="sell_to_close")
        assert got.premium == round(3.85 * 0.99, 4)

    def test_it_matches_the_brokers_own_arithmetic_not_a_copy_of_it(self):
        # Pinned against the real implementation, so a change to the fill model
        # fails here rather than silently making the review describe a trade
        # the system no longer places.
        from optionspilot.broker.paper import PaperBroker
        import inspect
        src = inspect.getsource(PaperBroker)
        assert "contract.ask * (1 + self._cfg.slippage_pct)" in src
        assert "bid * (1 - self._cfg.slippage_pct)" in src

    def test_a_limit_order_fills_at_its_limit_so_slippage_does_not_apply(self):
        got = _review(kind="limit", limit=3.50)
        assert got.premium == 3.50

    def test_a_missing_quote_gives_no_premium_rather_than_the_mid(self):
        # Substituting a different price than the one being described is the
        # defect this module exists to prevent.
        got = _review(ask=None)
        assert got.premium is None and got.cost is None
        assert "no live quote" in got.cost_note


class TestElementOneTheSentence:
    def test_it_names_side_quantity_symbol_strike_right_and_expiry(self):
        s = _review().sentence
        for part in ("BUYING", "1", "SPY", "470", "call", "12 Sep"):
            assert part in s, f"{part!r} missing from {s!r}"

    def test_it_prints_days_remaining_beside_the_date(self):
        assert "7 days from now" in _review().sentence

    def test_expiring_today_is_said_in_words_not_as_zero_days(self):
        assert "expires today" in _review(dte=0).sentence

    def test_one_day_is_singular(self):
        assert "1 day from now" in _review(dte=1).sentence

    def test_no_abbreviation_on_this_line(self):
        # Section 6.5 is explicit. "C"/"P" and "DTE" are the usual offenders.
        s = _review().sentence
        assert "DTE" not in s and " C " not in s and " P " not in s

    def test_a_limit_order_says_the_condition_in_the_sentence(self):
        assert "$3.50" in _review(kind="limit", limit=3.50).sentence

    def test_a_stop_names_the_underlying_level_not_the_premium(self):
        s = _review(side="sell_to_close", kind="stop_loss", stop=465.0).sentence
        assert "falls to $465" in s

    def test_selling_says_selling(self):
        assert "SELLING" in _review(side="sell_to_close").sentence


class TestElementTwoCostAndMaxLoss:
    def test_cost_is_the_premium_times_quantity_times_one_hundred(self):
        got = _review(quantity=2)
        assert got.cost == round(got.premium * 2 * 100, 2)

    def test_max_loss_is_stated_even_though_it_equals_cost(self):
        # Especially then: "you can lose all of it" is the fact beginners most
        # often do not know.
        got = _review()
        assert got.max_loss == got.cost
        assert "100%" in got.max_loss_note

    def test_a_closing_order_has_proceeds_rather_than_a_cost(self):
        got = _review(side="sell_to_close")
        assert got.cost is None and got.proceeds is not None

    def test_a_closing_order_states_no_max_loss_and_says_why(self):
        got = _review(side="sell_to_close")
        assert got.max_loss is None
        assert "no new risk" in got.max_loss_note


class TestElementThreeBreakeven:
    def test_a_call_breaks_even_above_the_strike_by_the_premium(self):
        got = _review()
        assert got.breakeven == round(470.0 + got.premium, 2)

    def test_a_put_breaks_even_below_the_strike_by_the_premium(self):
        got = _review(right="put")
        assert got.breakeven == round(470.0 - got.premium, 2)

    def test_spot_travels_with_it_so_the_distance_needs_no_arithmetic(self):
        assert _review().spot == 471.20

    def test_a_closing_order_has_no_breakeven_and_says_why(self):
        got = _review(side="sell_to_close")
        assert got.breakeven is None and "closing" in got.breakeven_note


class TestElementFourPositionSize:
    def test_it_is_a_percentage_of_the_account(self):
        got = _review(quantity=1, equity=10000.0)
        # Rounded to 2dp at the boundary, like every other displayed figure.
        assert got.position_pct == round(got.cost / 10000.0 * 100, 2)

    def test_no_equity_means_no_percentage_rather_than_zero(self):
        got = _review(equity=None)
        assert got.position_pct is None and got.position_note

    def test_zero_equity_is_absence_not_a_denominator(self):
        got = _review(equity=0.0)
        assert got.position_pct is None


class TestElementFiveIfYouDoNothing:
    def test_a_long_call_expires_worthless_below_its_strike(self):
        s = _review().if_nothing
        assert "closes below $470" in s and "worthless" in s

    def test_a_long_put_expires_worthless_above_its_strike(self):
        assert "closes above $470" in _review(right="put").if_nothing

    def test_a_resting_day_order_expires_at_the_end_of_the_day(self):
        s = _review(kind="limit", limit=3.5, tif="day").if_nothing
        assert "end of today" in s

    def test_a_gtc_order_rests_until_filled_or_cancelled(self):
        s = _review(kind="limit", limit=3.5, tif="gtc").if_nothing
        assert "fills or you cancel" in s

    def test_every_order_type_produces_a_non_empty_passive_outcome(self):
        for kind in ("market", "limit", "stop_loss", "take_profit",
                     "trailing_stop"):
            for side in ("buy_to_open", "sell_to_close"):
                got = _review(kind=kind, side=side, limit=3.5, stop=465.0)
                assert got.if_nothing.strip(), f"{kind}/{side} said nothing"


class TestTheHonestyLine:
    def test_a_market_order_names_the_crossing_side_and_the_slippage(self):
        note = _review().fill_note
        assert "ask" in note and "1% slippage" in note

    def test_it_says_the_quote_is_delayed(self):
        assert "15-minute delayed" in _review().fill_note

    def test_a_limit_order_states_the_condition_it_waits_for(self):
        note = _review(kind="limit", limit=3.50).fill_note
        assert "ask is at or below" in note and "$3.50" in note

    def test_a_stop_says_it_sells_at_the_bid_when_it_triggers(self):
        note = _review(side="sell_to_close", kind="stop_loss",
                       stop=465.0).fill_note
        assert "bid" in note and "triggers" in note

    def test_every_order_type_produces_a_non_empty_fill_note(self):
        for kind in ("market", "limit", "stop_loss", "take_profit",
                     "trailing_stop"):
            got = _review(kind=kind, limit=3.5, stop=465.0)
            assert got.fill_note.strip(), f"{kind} explained no fill"


class TestSerialisation:
    def test_it_is_json_safe_primitives(self):
        import json
        json.dumps(_review().to_dict(), allow_nan=False)

    def test_every_element_of_section_6_5_is_present_in_the_payload(self):
        d = _review().to_dict()
        for field in ("sentence", "cost", "max_loss", "breakeven", "spot",
                      "position_pct", "if_nothing", "fill_note"):
            assert field in d
