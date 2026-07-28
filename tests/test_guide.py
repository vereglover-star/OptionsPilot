"""Tests for the guided-onboarding domain layer (V0.6.1).

Three groups, in descending order of how much they would hurt if they broke:

  * `TestCatalogueContract` — the frontend and the backend hold one vocabulary
    between them (tutorial ids, feature keys). Those two catalogues live in two
    files by necessity and would drift silently: a recommendation naming a
    tutorial `index.html` does not have renders as nothing, and a rule reading a
    feature nothing records can never fire. Both failure modes LOOK implemented,
    which is why they are asserted statically rather than trusted.
  * `TestState` — a preferences document read off disk must cost a user their
    guide progress at worst, never their app (the `apply_control_state` lesson,
    V0.5.7).
  * `TestRecommendations` — every rule fires only on evidence, states the
    observation rather than a judgement, and never speaks about trading.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from optionspilot.ui import guide
from optionspilot.ui.guide import GuideFacts

HTML = (pathlib.Path(__file__).resolve().parent.parent
        / "optionspilot" / "ui" / "static" / "index.html")


def _js_block(text: str, opener: str, closer: str) -> str:
    start = text.index(opener)
    return text[start:text.index(closer, start)]


def frontend_tutorials() -> set[str]:
    block = _js_block(HTML.read_text(encoding="utf-8"),
                      "const GUIDE_TUTORIALS = {", "\n};")
    return set(re.findall(r"^  (\w+): \{", block, re.MULTILINE))


def frontend_features() -> set[str]:
    block = _js_block(HTML.read_text(encoding="utf-8"),
                      "const GUIDE_FEATURES = [", "];")
    return set(re.findall(r'"([^"]+)"', block))


class TestCatalogueContract:
    def test_tutorial_ids_match_the_frontend_exactly(self):
        """Both directions matter. A backend id the frontend lacks produces a
        recommendation that renders blank; a frontend id the backend lacks is a
        tutorial whose completion is silently discarded by normalize_state."""
        assert frontend_tutorials() == set(guide.TUTORIALS)

    def test_every_read_feature_is_actually_recorded(self):
        missing = set(guide.KNOWN_FEATURES) - frontend_features()
        assert not missing, (
            f"recommender reads {sorted(missing)}, which index.html never "
            "records — those rules can never fire")

    def test_every_tutorial_the_recommender_can_name_exists(self):
        """Exhaustive over the rules, not over a hand-written list: drive the
        recommender with facts extreme enough to trip everything, and assert
        every tutorial it names is real."""
        facts = GuideFacts(closed_trades=9, coach_reviews=4, open_positions=2,
                           orders_placed=9, order_kinds_used=frozenset({"market"}),
                           watchlist_size=1, single_data_source=True)
        named = set()
        # Each rule is suppressed once offered, so sweep until it runs dry.
        state = guide.default_state()
        for _ in range(len(guide.TUTORIALS) + 2):
            recs = guide.recommendations(state, facts)
            if not recs:
                break
            for r in recs:
                named.add(r.tutorial)
                state["dismissed"].append(r.tutorial)
        assert named, "no rule fired at all — the sweep is not testing anything"
        assert named <= set(guide.TUTORIALS)
        assert named <= frontend_tutorials()


class TestState:
    def test_defaults_are_a_fresh_document(self):
        a, b = guide.default_state(), guide.default_state()
        a["completed"].append("welcome")
        assert b["completed"] == []

    @pytest.mark.parametrize("raw", [
        None, [], "welcome", 42, {"completed": "welcome"},
        {"completed": [1, 2, None]}, {"features": ["a", "b"]},
        {"features": {"ok": "not-an-int"}}, {"features": {"UPPER": 3}},
        {"onboarded": {"nested": True}}, {"dismissed": {"a": 1}},
    ])
    def test_a_hand_edited_document_never_raises(self, raw):
        state = guide.normalize_state(raw)
        assert set(state) == set(guide.DEFAULT_STATE)
        assert isinstance(state["completed"], list)
        assert isinstance(state["features"], dict)

    def test_unknown_tutorial_ids_are_dropped(self):
        state = guide.normalize_state(
            {"completed": ["welcome", "not_a_tutorial", "charts"]})
        assert state["completed"] == ["welcome", "charts"]

    def test_feature_keys_are_shape_checked(self):
        state = guide.normalize_state({"features": {
            "tab.charts": 3, "Tab.Charts": 9, "x" * 200: 1, "ok-key:1": 2,
            "neg": -4, "bool": True,
        }})
        assert state["features"] == {"tab.charts": 3, "ok-key:1": 2}

    def test_feature_keys_are_capped(self):
        raw = {f"f{i}": 1 for i in range(guide.MAX_FEATURE_KEYS + 50)}
        assert len(guide.normalize_state({"features": raw})["features"]) \
            == guide.MAX_FEATURE_KEYS

    def test_merge_unions_completions_and_never_unfinishes(self):
        state = guide.merge_state(guide.default_state(), {"completed": ["charts"]})
        state = guide.merge_state(state, {"completed": ["trade"]})
        assert state["completed"] == ["charts", "trade"]
        # A client posting a short list must not erase what is already there.
        state = guide.merge_state(state, {"completed": []})
        assert state["completed"] == ["charts", "trade"]

    def test_merge_increments_features(self):
        state = guide.merge_state(guide.default_state(),
                                  {"features": ["tab.charts", "tab.charts"]})
        state = guide.merge_state(state, {"features": "tab.charts"})
        assert state["features"]["tab.charts"] == 3

    def test_finishing_a_tutorial_undismisses_it(self):
        state = guide.merge_state(guide.default_state(), {"dismissed": ["charts"]})
        state = guide.merge_state(state, {"completed": ["charts"]})
        assert state["dismissed"] == []
        assert state["completed"] == ["charts"]

    def test_welcome_implies_onboarded_either_way(self):
        assert guide.merge_state(guide.default_state(),
                                 {"completed": ["welcome"]})["onboarded"]
        assert guide.merge_state(guide.default_state(),
                                 {"dismissed": ["welcome"]})["onboarded"]

    @pytest.mark.parametrize("flag", guide.DISPLAY_FLAGS)
    def test_settings_are_replaced_not_unioned(self, flag):
        """A user turning a display preference off must win — including the two
        accessibility flags, where 'sticky on' would be the worse failure."""
        start = guide.default_state()[flag]
        state = guide.merge_state(guide.default_state(), {flag: not start})
        assert state[flag] is (not start)
        state = guide.merge_state(state, {flag: start})
        assert state[flag] is start

    def test_display_flags_survive_a_round_trip(self):
        state = guide.merge_state(guide.default_state(),
                                  {"large_text": True, "high_contrast": True})
        assert guide.normalize_state(state)["large_text"] is True
        assert guide.normalize_state(state)["high_contrast"] is True

    def test_display_flags_are_coerced_not_trusted(self):
        state = guide.normalize_state({"large_text": "yes", "high_contrast": 0})
        assert state["large_text"] is True and state["high_contrast"] is False

    def test_forget_resets_everything(self):
        state = guide.merge_state(guide.default_state(),
                                  {"completed": ["welcome", "charts"],
                                   "features": ["tab.charts"], "reduce_motion": True})
        assert guide.merge_state(state, {"forget": True}) == guide.default_state()

    def test_merge_tolerates_garbage(self):
        assert guide.merge_state(guide.default_state(), None) == guide.default_state()
        assert guide.merge_state(guide.default_state(), "nope") == guide.default_state()


class TestRecommendations:
    def test_a_new_user_is_offered_the_tour_first(self):
        recs = guide.recommendations(guide.default_state(), GuideFacts())
        assert recs[0].tutorial == "welcome"

    def test_an_onboarded_user_with_no_history_is_left_alone_mostly(self):
        """Nothing should fire on an absence of evidence except the two rules
        whose whole subject IS the absence (never backtested, tiny watchlist)."""
        state = guide.merge_state(guide.default_state(), {"completed": ["welcome"]})
        recs = guide.recommendations(state, GuideFacts(watchlist_size=8))
        assert {r.tutorial for r in recs} == {"backtest"}

    def test_market_orders_only_is_evidenced_by_the_order_book(self):
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        facts = GuideFacts(orders_placed=6,
                           order_kinds_used=frozenset({"market"}))
        rec = next(r for r in guide.recommendations(state, facts)
                   if r.tutorial == "trade")
        assert "6" in rec.reason
        assert rec.evidence["orders_placed"] == 6

    def test_a_user_who_has_used_limits_is_not_told_about_them(self):
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        facts = GuideFacts(orders_placed=6,
                           order_kinds_used=frozenset({"market", "limit"}))
        assert not [r for r in guide.recommendations(state, facts)
                    if "limit order" in r.reason]

    def test_no_orders_at_all_does_not_fire_the_order_type_rule(self):
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        facts = GuideFacts(orders_placed=0, order_kinds_used=frozenset())
        assert not [r for r in guide.recommendations(state, facts)
                    if "market orders" in r.reason]

    def test_an_unknown_data_source_count_suppresses_that_rule(self):
        """None is 'I could not tell', not 'no'. Only True may fire it."""
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        for value, expected in ((None, False), (False, False), (True, True)):
            facts = GuideFacts(single_data_source=value, watchlist_size=9,
                               closed_trades=3)
            fired = any(r.tutorial == "marketdata"
                        for r in guide.recommendations(state, facts))
            assert fired is expected, value

    def test_a_visited_tab_stops_its_recommendation(self):
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        facts = GuideFacts(closed_trades=4)
        assert any(r.tutorial == "journal"
                   for r in guide.recommendations(state, facts))
        state = guide.merge_state(state, {"features": ["tab.journal"]})
        assert not any(r.tutorial == "journal"
                       for r in guide.recommendations(state, facts))

    def test_completed_and_dismissed_tutorials_are_never_offered(self):
        facts = GuideFacts(closed_trades=4, coach_reviews=2, orders_placed=5,
                           order_kinds_used=frozenset({"market"}))
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        offered = {r.tutorial for r in guide.recommendations(state, facts)}
        assert offered
        state = guide.merge_state(state, {"dismissed": sorted(offered)})
        assert not [r for r in guide.recommendations(state, facts)
                    if r.tutorial in offered]

    def test_one_tutorial_is_never_offered_twice(self):
        """Two rules reach the Trade tour; the user must not see it twice."""
        state = guide.merge_state(guide.default_state(), {"onboarded": True})
        facts = GuideFacts(orders_placed=5, open_positions=2,
                           order_kinds_used=frozenset({"market"}))
        recs = guide.recommendations(state, facts)
        assert len(recs) == len({r.tutorial for r in recs})

    def test_output_is_capped_and_ordered(self):
        facts = GuideFacts(closed_trades=4, coach_reviews=2, open_positions=1,
                           orders_placed=5, order_kinds_used=frozenset({"market"}),
                           watchlist_size=1, single_data_source=True)
        recs = guide.recommendations(guide.default_state(), facts)
        assert len(recs) <= guide.MAX_RECOMMENDATIONS
        assert [r.priority for r in recs] == sorted(r.priority for r in recs)

    def test_every_recommendation_carries_a_reason(self):
        facts = GuideFacts(closed_trades=4, coach_reviews=2, orders_placed=5,
                           order_kinds_used=frozenset({"market"}))
        for r in guide.recommendations(guide.default_state(), facts):
            assert r.reason.strip() and r.headline.strip()
            assert r.tutorial in guide.TUTORIALS

    def test_no_rule_gives_trading_advice(self):
        """The line this module must not cross: it recommends TUTORIALS from
        feature usage. Claims about how the user should trade belong to
        intelligence/, which measures them properly."""
        banned = ("you should trade", "stop taking", "trade less", "trade more",
                  "you are over", "your win rate", "you tend to")
        facts = GuideFacts(closed_trades=40, coach_reviews=20, open_positions=3,
                           orders_placed=40, order_kinds_used=frozenset({"market"}),
                           watchlist_size=1, single_data_source=True)
        state = guide.default_state()
        for _ in range(len(guide.TUTORIALS) + 2):
            recs = guide.recommendations(state, facts)
            if not recs:
                break
            for r in recs:
                text = (r.headline + " " + r.reason).lower()
                assert not any(b in text for b in banned), r
                state["dismissed"].append(r.tutorial)

    def test_payload_is_json_serialisable(self):
        body = guide.payload(guide.default_state(),
                             GuideFacts(order_kinds_used=frozenset({"limit"})))
        round_tripped = json.loads(json.dumps(body))
        assert round_tripped["tutorials"] == list(guide.TUTORIALS)
        assert round_tripped["facts"]["order_kinds_used"] == ["limit"]
