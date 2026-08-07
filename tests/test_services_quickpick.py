"""Quick-pick intent resolution (UI V2 M4-C1).

Each test states one fact about one axis. The module resolves expiry and
strike separately precisely so they can be tested separately, and the failure
cases are tested for their REASON rather than for falsiness — a chip that
resolves to nothing and says nothing is the defect this module was written to
avoid.
"""
from __future__ import annotations

from datetime import date

import pytest

from optionspilot.services import quickpick as qp


TODAY = date(2026, 8, 7)


def _row(strike, right="call", mid=3.5, dte=7):
    return {"strike": strike, "right": right, "bid": mid - 0.05,
            "ask": mid + 0.05, "mid": mid, "delta": 0.5, "dte": dte}


class TestCatalogue:
    def test_the_four_chips_of_section_6_3_in_order(self):
        assert [i.key for i in qp.INTENTS] == [
            qp.ATM_CALL, qp.ATM_PUT, qp.DAY_30, qp.WEEKLY]

    def test_a_strike_intent_names_no_expiry_and_an_expiry_intent_no_right(self):
        # The axis a chip does not name is the axis it must not override.
        assert qp.BY_KEY[qp.ATM_CALL].target_dte is None
        assert qp.BY_KEY[qp.ATM_PUT].target_dte is None
        assert qp.BY_KEY[qp.DAY_30].right is None
        assert qp.BY_KEY[qp.WEEKLY].right is None

    def test_the_catalogue_serialises_to_primitives(self):
        for entry in qp.catalogue():
            assert set(entry) == {"key", "label", "right", "target_dte"}
            for value in entry.values():
                assert value is None or isinstance(value, (str, int))


class TestExpirationChoice:
    def test_a_strike_intent_keeps_the_expiry_the_user_is_on(self):
        got = qp.expiration_for(qp.BY_KEY[qp.ATM_CALL],
                                ["2026-08-14", "2026-09-18"], TODAY,
                                current="2026-09-18")
        assert got.expiration == "2026-09-18"
        assert got.dte == 42

    def test_a_stale_current_expiry_falls_back_rather_than_refusing(self):
        # A symbol that just changed leaves a workspace expiry that no longer
        # exists. Refusing here would make the chip fail for a reason the user
        # cannot see and cannot act on.
        got = qp.expiration_for(qp.BY_KEY[qp.ATM_CALL],
                                ["2026-08-14", "2026-09-18"], TODAY,
                                current="2026-01-01")
        assert got.expiration == "2026-08-14"

    def test_thirty_day_picks_the_nearest_expiry_to_thirty_days(self):
        got = qp.expiration_for(qp.BY_KEY[qp.DAY_30],
                                ["2026-08-14", "2026-09-04", "2026-11-20"],
                                TODAY)
        assert got.expiration == "2026-09-04"   # 28 days
        assert got.dte == 28

    def test_a_tie_breaks_toward_the_longer_expiry(self):
        # 2026-08-31 is 24 days, 2026-09-12 is 36; both 6 from the target.
        got = qp.expiration_for(qp.BY_KEY[qp.DAY_30],
                                ["2026-08-31", "2026-09-12"], TODAY)
        assert got.expiration == "2026-09-12"

    def test_weekly_does_not_land_on_tomorrow_when_a_real_week_exists(self):
        got = qp.expiration_for(qp.BY_KEY[qp.WEEKLY],
                                ["2026-08-08", "2026-08-14"], TODAY)
        assert got.expiration == "2026-08-14"

    def test_expiries_in_the_past_are_not_candidates(self):
        got = qp.expiration_for(qp.BY_KEY[qp.WEEKLY],
                                ["2026-07-01", "2026-08-14"], TODAY)
        assert got.expiration == "2026-08-14"

    def test_no_expirations_says_so(self):
        got = qp.expiration_for(qp.BY_KEY[qp.DAY_30], [], TODAY)
        assert not got.ok
        assert "no listed expirations" in got.reason

    def test_unparseable_dates_are_ignored_rather_than_raising(self):
        got = qp.expiration_for(qp.BY_KEY[qp.WEEKLY],
                                ["not-a-date", "2026-08-14"], TODAY)
        assert got.expiration == "2026-08-14"


class TestContractChoice:
    def test_atm_call_takes_the_strike_nearest_spot(self):
        rows = [_row(465), _row(470), _row(475)]
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], rows, 471.20)
        assert got.ok and got.strike == 470.0 and got.right == "call"

    def test_atm_put_reads_the_put_side_not_the_call_side(self):
        rows = [_row(470, "call"), _row(475, "put")]
        got = qp.contract_for(qp.BY_KEY[qp.ATM_PUT], rows, 471.20)
        assert got.ok and got.right == "put" and got.strike == 475.0

    def test_an_expiry_intent_keeps_the_right_the_user_already_had(self):
        rows = [_row(470, "call"), _row(470, "put")]
        got = qp.contract_for(qp.BY_KEY[qp.DAY_30], rows, 471.0,
                              current_right="put")
        assert got.right == "put"

    def test_a_one_row_chain_resolves_to_that_row(self):
        # The thin-chain case is exactly when the shortcut is most useful.
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [_row(500)], 471.20)
        assert got.ok and got.strike == 500.0

    def test_the_quote_travels_with_the_pick(self):
        # So the ticket can populate without a second request against a chain
        # that may already have moved.
        rows = [_row(470, mid=3.85)]
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], rows, 471.0)
        assert got.mid == 3.85
        assert got.bid == pytest.approx(3.80) and got.ask == pytest.approx(3.90)

    def test_an_equidistant_pair_takes_the_lower_strike_deterministically(self):
        rows = [_row(470), _row(472)]
        a = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], rows, 471.0)
        b = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], list(reversed(rows)), 471.0)
        assert a.strike == b.strike == 470.0


class TestItSaysWhyItCouldNot:
    def test_no_spot_names_the_missing_spot_not_the_missing_strike(self):
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [_row(470)], None)
        assert not got.ok
        assert "no spot price" in got.reason

    def test_a_zero_spot_is_absence_not_a_price(self):
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [_row(470)], 0.0)
        assert not got.ok and "no spot price" in got.reason

    def test_an_expiry_with_no_puts_says_which_side_is_missing(self):
        got = qp.contract_for(qp.BY_KEY[qp.ATM_PUT], [_row(470, "call")], 471.0)
        assert not got.ok and "no puts" in got.reason

    def test_an_empty_chain_says_so_rather_than_returning_nothing(self):
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [], 471.0)
        assert not got.ok and got.reason
        assert got.strike is None

    def test_an_unresolved_pick_reports_no_strike_rather_than_zero(self):
        # A strike of 0.0 is a price. This is an absence.
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [], 471.0)
        assert got.strike is None

    def test_rows_without_a_numeric_strike_are_skipped(self):
        rows = [{"right": "call", "strike": None}, _row(470)]
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], rows, 471.0)
        assert got.ok and got.strike == 470.0


class TestSerialisation:
    def test_the_view_model_is_json_safe_primitives(self):
        import json
        got = qp.contract_for(qp.BY_KEY[qp.ATM_CALL], [_row(470)], 471.0,
                              expiration="2026-09-18", dte=42)
        json.dumps(got.to_dict(), allow_nan=False)
        assert got.to_dict()["expiration"] == "2026-09-18"
        assert got.to_dict()["dte"] == 42
