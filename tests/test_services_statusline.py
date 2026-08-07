"""The status line — one test per case, plus the precedence the docs omit.

`UI_V2_DESIGN.md` §5.3 enumerates eight situations and the sentence each
produces. It does not say which wins when several are true, and in production
several usually are, so the ranking is a decision `services/statusline.py` makes
and this file pins.

The rule the whole feature rests on — *it never says "nothing needs you" unless
nothing does* — is asserted directly in `TestItNeverLies`, not inferred from the
individual cases.
"""

from __future__ import annotations

import json

import pytest

from optionspilot.services.statusline import (
    CASES, DEGRADED, FIRST_RUN, HALTED, HOLDING, IDLE_CLOSED, IDLE_OPEN,
    REJECTED, STOP_NEAR, StatusInputs, status_line,
)


class TestTheEightCases:
    """One per row of §5.3's table, asserting the case AND the sentence."""

    def test_nothing_open_market_closed(self):
        view = status_line(StatusInputs(part_of_day="evening", has_traded=True))
        assert view.case == IDLE_CLOSED
        assert view.text == "Good evening. Markets are closed. Nothing needs you."

    def test_nothing_open_market_open(self):
        view = status_line(StatusInputs(market_open=True, has_traded=True,
                                        cleared_setups=2))
        assert view.case == IDLE_OPEN
        assert view.text == ("Markets are open. You have no positions and "
                             "2 setups cleared the gate.")

    def test_positions_open_and_healthy(self):
        view = status_line(StatusInputs(market_open=True, has_traded=True,
                                        positions=2, today_pnl=212.40))
        assert view.case == HOLDING
        assert view.text == ("Markets are open. You are up $212 today "
                             "across 2 positions.")

    def test_a_stop_is_close(self):
        view = status_line(StatusInputs(market_open=True, has_traded=True,
                                        positions=3, nearest_stop=("AAPL", 0.40)))
        assert view.case == STOP_NEAR
        assert view.text == ("AAPL is $0.40 from your stop. "
                             "Everything else is steady.")

    def test_an_order_was_rejected(self):
        view = status_line(StatusInputs(
            has_traded=True, rejected_reason="insufficient buying power"))
        assert view.case == REJECTED
        assert view.text == ("One order was rejected — insufficient buying "
                             "power. Nothing else needs you.")

    def test_trading_halted(self):
        view = status_line(StatusInputs(
            has_traded=True, positions=2,
            halt_reason="daily loss limit reached"))
        assert view.case == HALTED
        assert view.text == ("Trading is halted: daily loss limit reached. "
                             "Positions are still managed.")

    def test_a_provider_is_degraded(self):
        view = status_line(StatusInputs(market_open=True, has_traded=True,
                                        degraded=("Yahoo", "rate limited")))
        assert view.case == DEGRADED
        assert view.text == ("Quotes are delayed — Yahoo is rate limited and "
                             "retrying. Trading continues.")

    def test_first_launch_empty(self):
        view = status_line(StatusInputs(account_value=10_000.0))
        assert view.case == FIRST_RUN
        assert view.text == ("Welcome. Your paper account has $10,000. "
                             "None of it is real.")


class TestPrecedence:
    """The ranking §5.3 does not state. Ordered by consequence, per P4."""

    def test_every_case_is_ranked(self):
        assert len(CASES) == len(set(CASES)) == 8

    @pytest.mark.parametrize("winner,inputs", [
        (HALTED, StatusInputs(halt_reason="daily loss limit reached",
                              rejected_reason="insufficient buying power",
                              nearest_stop=("AAPL", 0.40),
                              degraded=("Yahoo", "rate limited"))),
        (REJECTED, StatusInputs(rejected_reason="insufficient buying power",
                                nearest_stop=("AAPL", 0.40),
                                degraded=("Yahoo", "rate limited"))),
        (STOP_NEAR, StatusInputs(nearest_stop=("AAPL", 0.40),
                                 degraded=("Yahoo", "rate limited"))),
        (DEGRADED, StatusInputs(degraded=("Yahoo", "rate limited"))),
    ])
    def test_the_more_consequential_case_wins(self, winner, inputs):
        assert status_line(inputs).case == winner

    def test_a_welcome_never_hides_something_wrong(self):
        """A brand-new account with a halted engine says HALTED, not "Welcome".

        This is the precedence decision that matters most: a first-run greeting
        is exactly when a user has the least context to notice that something
        is wrong on their own.
        """
        view = status_line(StatusInputs(account_value=10_000.0, has_traded=False,
                                        halt_reason="daily loss limit reached"))
        assert view.case == HALTED

    def test_holding_outranks_idle_regardless_of_market(self):
        assert status_line(StatusInputs(has_traded=True, positions=1)).case == HOLDING


class TestItNeverLies:
    """The one rule: *never say nothing needs you unless nothing does.*"""

    @pytest.mark.parametrize("inputs", [
        StatusInputs(halt_reason="daily loss limit reached"),
        StatusInputs(rejected_reason="insufficient buying power"),
        StatusInputs(nearest_stop=("AAPL", 0.40)),
    ])
    def test_a_case_that_needs_the_user_says_so(self, inputs):
        assert status_line(inputs).needs_you is True

    @pytest.mark.parametrize("inputs", [
        StatusInputs(has_traded=True),
        StatusInputs(has_traded=True, market_open=True),
        StatusInputs(has_traded=True, positions=2, today_pnl=50.0),
        StatusInputs(account_value=10_000.0),
        StatusInputs(has_traded=True, degraded=("Yahoo", "rate limited")),
    ])
    def test_a_quiet_case_does_not_claim_attention(self, inputs):
        assert status_line(inputs).needs_you is False

    def test_nothing_needs_you_appears_only_when_nothing_does(self):
        """Asserted over the phrase itself, across every case, because the
        phrase is the promise. A future case that reuses the wording without
        clearing the alarms fails here."""
        for inputs in (
            StatusInputs(halt_reason="x"), StatusInputs(rejected_reason="x"),
            StatusInputs(nearest_stop=("AAPL", 0.4)),
            StatusInputs(degraded=("Yahoo", "rate limited")),
            StatusInputs(has_traded=True), StatusInputs(account_value=10_000.0),
            StatusInputs(has_traded=True, positions=1),
        ):
            view = status_line(inputs)
            if "nothing needs you" in view.text.lower():
                assert view.needs_you is False, view.text

    def test_a_degraded_provider_is_a_notice_not_a_task(self):
        """Its sentence ends "Trading continues", so flagging it as needing the
        user would make `needs_you` mean "something is unusual" rather than
        "act" — and a flag that means two things means neither."""
        view = status_line(StatusInputs(has_traded=True,
                                        degraded=("Yahoo", "rate limited")))
        assert view.needs_you is False
        assert "Trading continues" in view.text


class TestGrammarDetails:
    def test_a_countdown_is_only_shown_when_it_is_known(self):
        assert "Markets are closed." in status_line(
            StatusInputs(has_traded=True, minutes_to_open=None)).text

    def test_minutes_to_open_reads_as_a_span(self):
        assert "Markets open in 42m." in status_line(
            StatusInputs(has_traded=True, minutes_to_open=42)).text

    def test_a_long_wait_reads_in_hours(self):
        text = status_line(StatusInputs(has_traded=True, positions=1,
                                        minutes_to_open=135)).text
        assert "Markets open in 2h 15m." in text

    def test_a_whole_number_of_hours_omits_the_minutes(self):
        assert "Markets open in 3h." in status_line(
            StatusInputs(has_traded=True, positions=1, minutes_to_open=180)).text

    def test_prose_never_shows_cents(self):
        """"$212.40" is a table cell in the middle of a sentence."""
        text = status_line(StatusInputs(has_traded=True, positions=1,
                                        today_pnl=212.40)).text
        assert "$212" in text and "212.40" not in text

    def test_a_loss_reads_as_down_rather_than_a_negative(self):
        text = status_line(StatusInputs(has_traded=True, positions=1,
                                        today_pnl=-88.0)).text
        assert "down $88" in text and "-$88" not in text

    def test_flat_is_its_own_sentence(self):
        """"up $0" is true and reads as a rounding artefact, not a state."""
        text = status_line(StatusInputs(has_traded=True, positions=1,
                                        today_pnl=0.0)).text
        assert "flat today across 1 position" in text

    def test_one_position_is_singular(self):
        assert "1 position." in status_line(
            StatusInputs(has_traded=True, positions=1, today_pnl=5.0)).text

    def test_one_setup_is_singular(self):
        assert "1 setup cleared" in status_line(
            StatusInputs(has_traded=True, market_open=True,
                         cleared_setups=1)).text

    def test_an_open_market_with_nothing_cleared_says_so(self):
        """Silence about the gate is indistinguishable from the gate not having
        run. `intelligence/`'s rule, applied to prose."""
        assert "nothing has cleared the gate" in status_line(
            StatusInputs(has_traded=True, market_open=True)).text

    def test_a_halt_carries_no_greeting(self):
        """A pleasantry in front of "trading is halted" reads as the system not
        understanding what it just said."""
        assert not status_line(
            StatusInputs(halt_reason="daily loss limit reached")
        ).text.startswith("Good ")

    def test_the_part_of_day_is_the_callers_to_decide(self):
        for part in ("morning", "afternoon", "evening"):
            assert f"Good {part}." in status_line(
                StatusInputs(has_traded=True, part_of_day=part)).text


class TestSerialisation:
    def test_it_survives_json(self):
        payload = json.loads(json.dumps(
            status_line(StatusInputs(has_traded=True)).to_dict()))
        assert set(payload) == {"text", "case", "needs_you"}

    def test_every_case_produces_a_non_empty_sentence(self):
        seen = set()
        for inputs in (
            StatusInputs(halt_reason="x"),
            StatusInputs(rejected_reason="x"),
            StatusInputs(nearest_stop=("AAPL", 0.4)),
            StatusInputs(degraded=("Yahoo", "rate limited")),
            StatusInputs(account_value=10_000.0),
            StatusInputs(has_traded=True, positions=1),
            StatusInputs(has_traded=True, market_open=True),
            StatusInputs(has_traded=True),
        ):
            view = status_line(inputs)
            seen.add(view.case)
            assert view.text.strip() and view.text.endswith(".")
        assert seen == set(CASES), "a case is unreachable from any input"
