"""HomeService — assembly, region independence, and the evidence floor.

The service computes almost nothing; its job is to gather six regions from four
owners and to fail *per region*. So these tests are mostly about what happens
when an owner is broken, which is the part `UI_V2_WIREFRAMES.md` §2.10 is
specific about and the part a happy-path test would never reach.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from optionspilot.intelligence import stats
from optionspilot.services.home import (
    MAX_NEXT_ACTIONS, MIN_TRADES_FOR_WIN_RATE, HomeService, win_rate_view,
)
from optionspilot.services.statusline import StatusInputs


class FakePortfolio:
    def __init__(self, *, positions=(), equity=10_000.0, risk_dollars=0.0):
        self._positions = list(positions)
        self._equity = equity
        self._risk = risk_dollars

    def account(self):
        return NS(to_dict=lambda: {"equity": self._equity, "cash": self._equity,
                                   "realized_pnl": 0.0,
                                   "starting_balance": 10_000.0})

    def open_risk(self):
        return NS(to_dict=lambda: {"dollars": self._risk, "pct_of_account": 0.0,
                                   "positions": len(self._positions),
                                   "marked": 0})

    def positions(self):
        return [NS(to_dict=lambda p=p: p) for p in self._positions]


def build(**kw):
    kw.setdefault("portfolio", FakePortfolio())
    kw.setdefault("facts", lambda: StatusInputs(has_traded=True))
    kw.setdefault("performance",
                  lambda: NS(daily_pnl=0.0, buying_power=10_000.0,
                             win_rate=None, trades=0))
    return HomeService(**kw)


class TestAssembly:
    def test_it_returns_every_region_the_wireframe_names(self):
        view = build().view()
        for field in ("status", "account", "open_risk", "today_pnl",
                      "buying_power", "win_rate", "positions",
                      "working_orders", "next_actions", "equity",
                      "watchlist", "errors"):
            assert hasattr(view, field), field

    def test_a_healthy_home_reports_no_errors(self):
        assert build().view().errors == []

    def test_the_status_line_is_serialised_not_nested_as_an_object(self):
        """The payload crosses a JSON boundary; a view model inside a view model
        would not survive `to_dict`."""
        payload = json.loads(json.dumps(build().view().to_dict()))
        assert isinstance(payload["status"], dict)
        assert payload["status"]["text"]

    def test_the_whole_payload_survives_json(self):
        json.dumps(build().view().to_dict(), allow_nan=False)


class TestRegionsFailIndependently:
    """§2.10: "Home never fails as a whole, because its regions have
    independent sources." A raising owner names itself and the rest renders."""

    def test_a_broken_portfolio_does_not_take_the_status_line_with_it(self):
        class Broken(FakePortfolio):
            def account(self):
                raise RuntimeError("broker is gone")

        view = build(portfolio=Broken()).view()
        assert "metrics" in view.errors
        assert view.account is None
        assert view.status["text"], "the sentence survived its neighbour failing"

    def test_a_broken_status_source_falls_back_to_a_quiet_sentence(self):
        def boom():
            raise RuntimeError("no orchestrator")

        view = build(facts=boom).view()
        assert "status" in view.errors
        assert view.status["text"]
        assert view.status["needs_you"] is False, (
            "a fact this host could not answer must never manufacture an alarm "
            "— nor silence one, which is why the defaults are the quiet case")

    def test_each_failing_region_is_named_once(self):
        class Broken(FakePortfolio):
            def account(self):
                raise RuntimeError("x")

            def positions(self):
                raise RuntimeError("y")

        view = build(portfolio=Broken()).view()
        assert view.errors == sorted(set(view.errors))
        assert "metrics" in view.errors and "positions" in view.errors

    def test_one_broken_region_leaves_the_others_populated(self):
        class Broken(FakePortfolio):
            def open_risk(self):
                raise RuntimeError("x")

        view = build(portfolio=Broken(positions=[{"contract": "SPY"}])).view()
        assert view.open_risk is None and "risk" in view.errors
        assert view.positions == [{"contract": "SPY"}]
        assert view.account is not None


class TestNextActions:
    """H4, the specification-critical region."""

    def test_it_shows_at_most_three(self):
        engine = lambda: [{"id": n} for n in range(10)]  # noqa: E731
        assert len(build(intelligence=engine).view().next_actions) == MAX_NEXT_ACTIONS
        assert MAX_NEXT_ACTIONS == 3

    def test_it_does_not_reorder_what_the_engine_ranked(self):
        """The engine ranks with a false-discovery correction already applied.
        Re-ranking here would be a second opinion about evidence, computed by
        the layer with the least of it."""
        ranked = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert build(intelligence=lambda: ranked).view().next_actions == ranked

    def test_no_findings_is_an_empty_list(self):
        assert build(intelligence=lambda: []).view().next_actions == []

    def test_could_not_look_is_none_and_not_an_empty_list(self):
        """"No findings" and "I could not read your history" are different
        answers, and §2.10 requires the second to be visible: a silent empty
        panel is indistinguishable from "nothing is wrong"."""
        assert build(intelligence=lambda: None).view().next_actions is None

    def test_an_engine_that_raises_is_could_not_look(self):
        def boom():
            raise RuntimeError("intelligence is down")

        view = build(intelligence=boom).view()
        assert view.next_actions is None
        assert "next_actions" in view.errors

    def test_no_engine_at_all_is_no_findings(self):
        """A host that composes without `intelligence/` is not a host whose
        engine failed."""
        view = build().view()
        assert view.next_actions == [] and "next_actions" not in view.errors


class TestWinRateEvidenceFloor:
    def test_the_floor_is_the_ladder_intelligence_already_uses(self):
        """Not a new number. A headline card does not get to be more confident
        than the analysis engine about the same history."""
        assert MIN_TRADES_FOR_WIN_RATE == stats.MIN_SAMPLE_HIGH == 30

    def test_below_the_floor_there_is_no_rate(self):
        view = win_rate_view(0.75, 4)
        assert view.rate is None and view.sufficient is False
        assert view.trades == 4 and view.needed == 30

    def test_at_the_floor_the_rate_is_stated(self):
        view = win_rate_view(0.58, 30)
        assert view.rate == pytest.approx(0.58) and view.sufficient is True

    def test_zero_trades_is_not_a_zero_percent_win_rate(self):
        """A zero win rate on zero trades is a false statement about a person."""
        view = win_rate_view(None, 0)
        assert view.rate is None and view.trades == 0

    def test_a_missing_rate_is_never_invented_from_a_large_sample(self):
        assert win_rate_view(None, 500).rate is None

    def test_it_reaches_the_payload(self):
        view = build(performance=lambda: NS(daily_pnl=1.0, buying_power=1.0,
                                            win_rate=0.6, trades=41)).view()
        assert view.win_rate["rate"] == pytest.approx(0.6)
        assert view.win_rate["sufficient"] is True


class TestOptionalRegions:
    def test_absent_collaborators_render_as_empty_not_as_errors(self):
        """A host that composes Home without an equity source has not suffered
        a failure, and must not be reported as one."""
        view = build().view()
        assert view.equity == [] and view.watchlist == []
        assert view.working_orders == [] and view.errors == []

    def test_a_present_but_broken_one_is_an_error(self):
        def boom():
            raise RuntimeError("no history")

        view = build(equity=boom).view()
        assert view.equity == [] and "equity" in view.errors
