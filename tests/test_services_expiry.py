"""Expiry labelling (UI V2 M4).

The table in the module docstring is the audit that produced this module; each
row of it is a test here. The point is not that the arithmetic is hard — it is
that the arithmetic was being done twice, in two languages, with two answers,
on the same screen.
"""
from __future__ import annotations

from datetime import date

from optionspilot.core.models import OptionContract, OptionRight
from optionspilot.services import expiry as ex


TODAY = date(2026, 8, 7)


class TestDaysTo:
    def test_today_is_zero_not_one(self):
        assert ex.days_to("2026-08-07", TODAY) == 0

    def test_tomorrow_is_one(self):
        assert ex.days_to("2026-08-08", TODAY) == 1

    def test_three_calendar_days_away_is_three(self):
        assert ex.days_to("2026-08-10", TODAY) == 3

    def test_a_past_expiry_is_negative_rather_than_clamped(self):
        # Clamping is what let the old code render an expired contract as
        # though it expired today.
        assert ex.days_to("2026-08-05", TODAY) == -2

    def test_an_unparseable_date_is_none_rather_than_a_guess(self):
        assert ex.days_to("not-a-date", TODAY) is None

    def test_it_agrees_with_the_domain_models_own_dte(self):
        # The whole defect was two calculations disagreeing. This is the one
        # that was already right; the module must match it exactly.
        for iso in ("2026-08-07", "2026-08-08", "2026-08-10", "2026-09-06"):
            contract = OptionContract(
                underlying="SPY", expiration=date.fromisoformat(iso),
                strike=470.0, right=OptionRight.CALL)
            assert ex.days_to(iso, TODAY) == contract.dte(TODAY)


class TestLabels:
    def test_same_day_says_0dte_and_today(self):
        assert ex.label(0) == ("0DTE", "Today")

    def test_next_day_says_1dte_and_tomorrow(self):
        assert ex.label(1) == ("1DTE", "Tomorrow")

    def test_three_days_says_3dte(self):
        assert ex.label(3) == ("3DTE", "3 days")

    def test_thirty_days_says_30dte(self):
        assert ex.label(30) == ("30DTE", "30 days")

    def test_a_past_expiry_says_expired_rather_than_a_negative_number(self):
        short, plain = ex.label(-2)
        assert short == "Expired" and plain == "2 days ago"

    def test_one_day_ago_is_singular(self):
        assert ex.label(-1)[1] == "1 day ago"

    def test_every_label_carries_both_registers(self):
        # §1.4's anti-bifurcation rule: one screen for both audiences, so the
        # trader's term and the beginner's wording travel together.
        for dte in range(-3, 60):
            short, plain = ex.label(dte)
            assert short.strip() and plain.strip()
            assert short != plain


class TestDescribe:
    def test_it_labels_every_listed_expiration_in_order(self):
        got = ex.describe(["2026-08-07", "2026-08-08", "2026-08-14"], TODAY)
        assert [e.dte for e in got] == [0, 1, 7]
        assert [e.label for e in got] == ["0DTE", "1DTE", "7DTE"]

    def test_an_expired_entry_is_flagged_rather_than_hidden(self):
        got = ex.describe(["2026-08-05"], TODAY)
        assert got[0].expired is True

    def test_unparseable_entries_are_dropped_not_rendered_blank(self):
        got = ex.describe(["oops", "2026-08-08"], TODAY)
        assert [e.date for e in got] == ["2026-08-08"]

    def test_it_serialises_to_primitives(self):
        import json
        json.dumps([e.to_dict() for e in ex.describe(["2026-08-08"], TODAY)],
                   allow_nan=False)


class TestTheDefectThisModuleReplaces:
    """The measured behaviour of the JavaScript this module deletes.

    Reproduced here so the regression is a test rather than a memory: the old
    formula rounded a TIME delta up, so it read one day high at every hour of
    the trading day except after 16:00 on expiration day itself.
    """

    @staticmethod
    def _old_js(expiration: str, now) -> int:
        import math
        from datetime import datetime
        exp = datetime.fromisoformat(expiration + "T16:00:00")
        return max(0, math.ceil((exp - now).total_seconds() * 1000 / 864e5))

    def test_the_old_formula_read_one_high_on_expiration_day(self):
        from datetime import datetime
        now = datetime(2026, 8, 7, 10, 0)
        assert self._old_js("2026-08-07", now) == 1     # what it showed
        assert ex.days_to("2026-08-07", TODAY) == 0     # what is true

    def test_and_one_high_on_every_other_day_too(self):
        from datetime import datetime
        now = datetime(2026, 8, 7, 10, 0)
        for iso, correct in (("2026-08-08", 1), ("2026-08-10", 3),
                             ("2026-09-06", 30)):
            assert self._old_js(iso, now) == correct + 1
            assert ex.days_to(iso, TODAY) == correct
