"""WorkspaceService (V0.7.0).

The module is pure, so most of this is adversarial input: a workspace document
is read at startup from a JSON file a user can open and written from a client
POST body, which puts it in the same hazard class as `marketdata.json` — the
preferences file whose hand-edited `{"providers": [1,2]}` crashed the app out of
the composition root because `or {}` is not a type check.

The governing requirement is therefore stated as a property rather than a list
of cases: **there is no input for which `normalize` raises, and no input which
produces a document the app cannot use.** The individual tests below are the
shapes most likely to arrive; the sweep at the end is the actual guarantee.
"""

from __future__ import annotations

import json

import pytest

from optionspilot.services import workspace as ws
from optionspilot.services.workspace import WorkspaceService


class FakeStore:
    """The `RuntimeSettings` surface WorkspaceService actually uses.

    `set_surface_level` is strict here because the real one is: the service's
    forgiveness on the workspace endpoint is a decision it makes for itself,
    and a lenient double would test the decision away.
    """

    def __init__(self, doc=None, surface_level=3, shell_v2=True):
        self.doc = doc if doc is not None else {}
        self.writes = 0
        self.level = surface_level
        self.shell = shell_v2

    def workspace_state(self):
        return dict(self.doc)

    def set_workspace_state(self, state):
        self.doc = dict(state)
        self.writes += 1
        return dict(self.doc)

    def surface_level(self):
        return self.level

    def set_surface_level(self, level):
        if level not in (1, 2, 3, 4):
            raise ValueError(f"surface_level must be one of [1, 2, 3, 4], "
                             f"got {level!r}")
        self.level = level
        return level

    def shell_v2(self):
        return self.shell

    def set_shell_v2(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError(f"shell_v2 must be true or false, got {enabled!r}")
        self.shell = enabled
        return enabled


class TestNormalize:
    def test_empty_input_gives_the_shipped_defaults(self):
        doc = ws.normalize(None)
        for key, value in ws.DEFAULTS.items():
            assert doc[key] == value

    def test_defaults_match_the_pre_V070_localStorage_defaults(self):
        """A user with no stored workspace must see exactly what they saw
        before this service existed — SPY, 1d, RTH only, no auto-follow."""
        assert ws.DEFAULTS["symbol"] == "SPY"
        assert ws.DEFAULTS["timeframe"] == "1d"
        assert ws.DEFAULTS["extended_hours"] is False
        assert ws.DEFAULTS["auto_follow"] is False
        assert ws.DEFAULTS["watchlist_sort"] == "custom"

    def test_a_timeframe_is_validated_against_the_domain_vocabulary(self):
        """Unlike `tab`, this one IS checked: an unparseable timeframe reaches
        `/api/candles`, which resolves it through `Timeframe.from_string` and
        502s. A cosmetic-looking field with a non-cosmetic failure."""
        assert ws.normalize({"timeframe": "5m"})["timeframe"] == "5m"
        assert ws.normalize({"timeframe": "17q"})["timeframe"] == "1d"
        assert ws.normalize({"timeframe": 5})["timeframe"] == "1d"

    def test_a_tab_is_NOT_validated_against_a_python_list(self):
        """Deliberate. Tab ids are frontend vocabulary; a backend copy of them
        would be the two-catalogue drift `services/guide.py` exists to avoid — a
        failure this project has paid for three times. Type and length only."""
        landing = ws.DEFAULTS["tab"]     # referenced, not spelled: this test
        assert landing == "dashboard"    # hardcoded "dash" and went stale once
        assert ws.normalize({"tab": "some-future-tab"})["tab"] == "some-future-tab"
        assert ws.normalize({"tab": "x" * 500})["tab"] == landing
        assert ws.normalize({"tab": ""})["tab"] == landing

    def test_symbols_are_upper_cased_and_bounded(self):
        assert ws.normalize({"symbol": " tsla "})["symbol"] == "TSLA"
        assert ws.normalize({"symbol": "BRK.B"})["symbol"] == "BRK.B"
        assert ws.normalize({"symbol": "'; DROP TABLE"})["symbol"] == "SPY"
        assert ws.normalize({"symbol": "A" * 40})["symbol"] == "SPY"

    def test_recent_symbols_are_deduped_and_capped(self):
        doc = ws.normalize({"recent_symbols": ["AAA", "AAA", *[f"S{i}" for i in range(50)]]})
        assert len(doc["recent_symbols"]) == ws.MAX_RECENT
        assert len(set(doc["recent_symbols"])) == len(doc["recent_symbols"])

    def test_layouts_are_bounded_in_count_and_name_length(self):
        """This document lands in the same `settings.json` the trading mode
        lives in. An unbounded client write is how a preferences file becomes a
        denial of service against startup."""
        doc = ws.normalize({"layouts": {f"name{i}": {"x": i} for i in range(200)}})
        assert len(doc["layouts"]) <= ws.MAX_LAYOUTS
        long_name = "n" * 500
        doc = ws.normalize({"layouts": {long_name: {}}})
        assert all(len(k) <= ws.MAX_NAME for k in doc["layouts"])

    def test_a_layout_body_is_opaque(self):
        """The backend must not understand a layout, or it becomes a second
        place that changes every time the frontend gains a panel."""
        body = {"panels": [{"id": "chart", "w": 3}], "nested": {"deep": True}}
        assert ws.normalize({"layouts": {"mine": body}})["layouts"]["mine"] == body

    def test_a_non_dict_layout_body_is_dropped_not_kept(self):
        assert ws.normalize({"layouts": {"a": ["not", "a", "dict"]}})["layouts"] == {}

    @pytest.mark.parametrize("garbage", [
        None, 0, "", "a string", [], [1, 2, 3], {"tab": None},
        {"indicators": "ema"}, {"indicators": [1, 2, {}]},
        {"layouts": "not a dict"}, {"recent_symbols": {"not": "a list"}},
        {"sidebar_collapsed": "yes"}, {"extended_hours": 1},
        {"symbol": None}, {"timeframe": []}, {"updated": 12345},
        {"expiry": 20260918}, {"expiry": "not-a-date"}, {"expiry": "2026-13-45"},
        {"contract": "SPY260918C00450000"}, {"contract": []},
        {"contract": {"strike": float("nan")}},
        {"contract": {"symbol": "SPY", "expiry": "2026-09-18",
                      "strike": float("inf"), "right": "call"}},
    ])
    def test_no_input_can_make_normalize_raise(self, garbage):
        """The actual guarantee. The failure mode for a bad preferences file
        must be 'you lose a preference', never 'the app will not start'."""
        doc = ws.normalize(garbage)
        assert set(doc) == set(ws.DEFAULTS) | {"updated"}
        json.dumps(doc, allow_nan=False)   # and it must survive the wire


def a_contract(**over) -> dict:
    return {"symbol": "SPY", "expiry": "2026-09-18",
            "strike": 450.0, "right": "call", **over}


class TestContractContext:
    """UI V2 M1-C2 — §4.5's second and third continuity guarantees."""

    def test_nothing_is_selected_by_default(self):
        doc = ws.normalize(None)
        assert doc["expiry"] == "" and doc["contract"] is None

    def test_a_well_formed_selection_round_trips(self):
        doc = ws.normalize({"symbol": "SPY", "expiry": "2026-09-18",
                            "contract": a_contract()})
        assert doc["expiry"] == "2026-09-18"
        assert doc["contract"] == a_contract()

    def test_an_expiry_is_validated_as_a_real_date(self):
        """Same test `timeframe` is held to and for the same reason: this one
        reaches `/api/chain`, which parses it."""
        assert ws.normalize({"expiry": "2026-02-30"})["expiry"] == ""
        assert ws.normalize({"expiry": "18/09/2026"})["expiry"] == ""
        assert ws.normalize({"expiry": " 2026-09-18 "})["expiry"] == "2026-09-18"

    @pytest.mark.parametrize("field,bad", [
        ("expiry", ""), ("expiry", "nope"), ("expiry", None),
        ("strike", 0), ("strike", -5), ("strike", "450"), ("strike", None),
        ("strike", True), ("strike", ws.MAX_STRIKE + 1),
        ("right", "CALL"), ("right", "c"), ("right", None), ("right", 1),
        ("symbol", ""), ("symbol", None), ("symbol", 7),
    ])
    def test_a_contract_is_all_or_nothing(self, field, bad):
        """A contract missing a usable strike is not a partially-selected
        contract; keeping the readable half hands the ticket something it
        cannot price.

        `("strike", True)` is the one worth reading twice: `isinstance(True,
        int)` is True in Python, so a naive check stores a $1.00 strike.
        """
        doc = ws.normalize({"symbol": "SPY", "contract": a_contract(**{field: bad})})
        assert doc["contract"] is None

    def test_a_missing_field_drops_the_whole_contract(self):
        for field in ("symbol", "expiry", "strike", "right"):
            partial = {k: v for k, v in a_contract().items() if k != field}
            assert ws.normalize({"symbol": "SPY", "contract": partial})["contract"] is None

    def test_a_contract_for_another_symbol_is_dropped_on_read(self):
        """The one cross-field invariant. A stored SPY call is meaningless once
        the workspace symbol is QQQ, and keeping it would break §4.5's FIRST
        guarantee (one symbol context) in order to serve its third."""
        doc = ws.normalize({"symbol": "QQQ", "contract": a_contract()})
        assert doc["contract"] is None

    def test_changing_the_symbol_drops_the_selection(self):
        state = ws.merge(None, {"symbol": "SPY", "contract": a_contract()})
        assert state["contract"] == a_contract()
        state = ws.merge(state, {"symbol": "QQQ"})
        assert state["contract"] is None

    def test_selecting_a_row_for_a_symbol_switched_in_the_same_patch_survives(self):
        """The client flow that would break under a naive "clear on symbol
        change" rule written in `merge` instead of in `normalize`."""
        state = ws.merge(None, {"symbol": "QQQ",
                                "contract": a_contract(symbol="QQQ")})
        assert state["contract"] == a_contract(symbol="QQQ")

    def test_a_symbol_change_keeps_the_timeframe_and_the_expiry(self):
        """§4.5-2 says the timeframe survives a symbol change. An expiry does
        too — a date means the same thing under any underlying — and only the
        contract, which names an instrument, does not."""
        state = ws.merge(None, {"symbol": "SPY", "timeframe": "15m",
                                "expiry": "2026-09-18",
                                "contract": a_contract()})
        state = ws.merge(state, {"symbol": "QQQ"})
        assert state["timeframe"] == "15m"
        assert state["expiry"] == "2026-09-18"
        assert state["contract"] is None

    def test_the_selection_survives_a_tab_change(self):
        """'...and remains selected if the user visits Research and returns.'"""
        state = ws.merge(None, {"symbol": "SPY", "contract": a_contract()})
        state = ws.merge(state, {"tab": "backtest"})
        state = ws.merge(state, {"tab": "trade"})
        assert state["contract"] == a_contract()

    def test_clearing_is_expressible(self):
        state = ws.merge(None, {"symbol": "SPY", "expiry": "2026-09-18",
                                "contract": a_contract()})
        state = ws.merge(state, {"contract": None, "expiry": ""})
        assert state["contract"] is None and state["expiry"] == ""

    def test_a_reset_clears_the_selection(self):
        store = FakeStore({"symbol": "SPY", "expiry": "2026-09-18",
                           "contract": a_contract()})
        view = WorkspaceService(store).reset()
        assert view.contract is None and view.expiry == ""

    def test_a_selection_survives_the_wire_and_a_restart(self):
        store = FakeStore()
        WorkspaceService(store).update({"symbol": "SPY", "expiry": "2026-09-18",
                                        "contract": a_contract()})
        json.dumps(store.doc, allow_nan=False)      # what settings.json holds
        restarted = WorkspaceService(FakeStore(store.doc)).get()
        assert restarted.contract == a_contract()
        assert restarted.expiry == "2026-09-18"
        json.dumps(restarted.to_dict(), allow_nan=False)


class TestSurfaceLevelOnTheWorkspace:
    """UI V2 M1-C3 — §4.5-5, served with the rest of the context.

    Stored apart (its own `settings.json` key, its own DEVICE_ONLY sync
    policy), served together, because a client needs every context fact in the
    same breath to render a first frame.
    """

    def test_it_is_served_with_the_workspace(self):
        assert WorkspaceService(FakeStore(surface_level=1)).get().surface_level == 1

    def test_it_can_be_set_through_the_same_patch_as_a_symbol(self):
        store = FakeStore()
        view = WorkspaceService(store).update({"symbol": "QQQ", "surface_level": 4})
        assert view.symbol == "QQQ" and view.surface_level == 4
        assert store.level == 4

    def test_it_is_not_written_into_the_workspace_document(self):
        """Two keys, two sync policies. If it leaked into the document it
        would inherit LAST_WRITE_WINS and start following users between
        devices, which is the decision that was explicitly not taken."""
        store = FakeStore()
        WorkspaceService(store).update({"surface_level": 2})
        assert "surface_level" not in store.doc
        assert store.level == 2

    def test_an_unusable_level_is_ignored_rather_than_4xxd(self):
        """The same forgiveness `timeframe` gets on this endpoint, and for the
        same reason: it records where someone was looking."""
        store = FakeStore(surface_level=3)
        view = WorkspaceService(store).update({"surface_level": 9,
                                               "symbol": "NVDA"})
        assert view.surface_level == 3          # unchanged, not defaulted
        assert view.symbol == "NVDA"            # the rest of the patch applied

    def test_the_stores_setter_is_still_strict_for_everyone_else(self):
        """The forgiveness is the SERVICE's decision for one endpoint, not a
        weakening of the store — a CLI or a settings screen calling the setter
        directly still gets told."""
        store = FakeStore()
        with pytest.raises(ValueError):
            store.set_surface_level(9)

    def test_a_workspace_reset_does_not_reset_it(self):
        """Closer to an accessibility setting than to a panel arrangement:
        someone tidying their layout has not asked to be shown nineteen
        columns they chose not to see."""
        store = FakeStore({"symbol": "QQQ"}, surface_level=1)
        assert WorkspaceService(store).reset().surface_level == 1

    def test_it_survives_the_wire(self):
        doc = WorkspaceService(FakeStore(surface_level=4)).get().to_dict()
        assert json.loads(json.dumps(doc, allow_nan=False))["surface_level"] == 4


class TestShellFlag:
    """UI V2 M2-C1. The rollback path for the whole shell migration.

    It rides the workspace payload for the same reason Surface Level does, and
    one stronger: it decides which frame the client draws at all, so fetching
    it separately would mean rendering the old shell and replacing it — a
    visible flash on every launch.
    """

    def test_it_is_on_by_default_since_m2_c11(self):
        assert WorkspaceService(FakeStore()).get().shell_v2 is True

    def test_it_is_served_with_the_workspace(self):
        assert WorkspaceService(FakeStore(shell_v2=False)).get().shell_v2 is False

    def test_it_can_be_turned_off_through_the_workspace_patch(self):
        """The rollback, over the wire."""
        store = FakeStore()
        assert WorkspaceService(store).update({"shell_v2": False}).shell_v2 is False
        assert store.shell is False

    def test_it_is_not_written_into_the_workspace_document(self):
        """Its own key, its own DEVICE_ONLY sync policy: a rollback that
        propagates to every device is not a rollback, it is an outage."""
        store = FakeStore()
        WorkspaceService(store).update({"shell_v2": False})
        assert "shell_v2" not in store.doc

    @pytest.mark.parametrize("bad", ["true", 1, 0, None, "yes", []])
    def test_an_unusable_value_is_ignored_rather_than_4xxd(self, bad):
        store = FakeStore(shell_v2=False)
        assert WorkspaceService(store).update({"shell_v2": bad}).shell_v2 is False

    @pytest.mark.parametrize("bad", ["true", 1, 0, None])
    def test_the_stores_setter_is_still_strict(self, bad):
        with pytest.raises(ValueError):
            FakeStore().set_shell_v2(bad)

    def test_a_workspace_reset_does_not_flip_it(self):
        """Resetting a layout must not silently move someone between two
        navigations."""
        store = FakeStore({"symbol": "QQQ"}, shell_v2=False)
        assert WorkspaceService(store).reset().shell_v2 is False

    def test_both_shell_fields_travel_together(self):
        store = FakeStore()
        view = WorkspaceService(store).update({"shell_v2": False,
                                               "surface_level": 1,
                                               "symbol": "NVDA"})
        assert (view.shell_v2, view.surface_level, view.symbol) == (False, 1, "NVDA")
        assert set(ws.WorkspaceService.SHELL_FIELDS) == {"surface_level", "shell_v2"}


class TestMerge:
    def test_a_partial_patch_leaves_untouched_keys_alone(self):
        """The reason merge is partial rather than a whole-document PUT: a
        phone that only knows about `symbol` must not overwrite a desktop's
        saved panel layout with its own defaults."""
        current = ws.normalize({"tab": "charts", "layouts": {"mine": {"a": 1}}})
        merged = ws.merge(current, {"symbol": "QQQ"})
        assert merged["symbol"] == "QQQ"
        assert merged["tab"] == "charts"
        assert merged["layouts"] == {"mine": {"a": 1}}

    def test_setting_a_symbol_promotes_it_to_the_head_of_recents(self):
        """Maintained server-side so a client never has to, and so two clients
        cannot disagree about the order."""
        state = ws.merge(None, {"symbol": "AAPL"})
        state = ws.merge(state, {"symbol": "MSFT"})
        state = ws.merge(state, {"symbol": "AAPL"})
        assert state["recent_symbols"][:2] == ["AAPL", "MSFT"]
        assert state["recent_symbols"].count("AAPL") == 1

    def test_recents_stay_capped_across_many_merges(self):
        state = None
        for i in range(60):
            state = ws.merge(state, {"symbol": f"SYM{i}"})
        assert len(state["recent_symbols"]) == ws.MAX_RECENT

    def test_an_unusable_value_in_a_patch_falls_back_rather_than_rejecting(self):
        """This endpoint records where someone was looking. 4xx-ing it would be
        an error toast in the middle of scrolling a chart."""
        merged = ws.merge({"symbol": "QQQ"}, {"timeframe": "not-a-timeframe"})
        assert merged["timeframe"] == "1d"
        assert merged["symbol"] == "QQQ"

    def test_unknown_keys_are_dropped_silently(self):
        merged = ws.merge(None, {"symbol": "SPY", "hack": "value"})
        assert "hack" not in merged

    def test_merge_never_raises_on_garbage(self):
        for patch in (None, "x", [1], {"layouts": 5}, {"recent_symbols": None}):
            assert ws.merge(None, patch)["symbol"] == "SPY"


class TestService:
    def test_get_returns_a_frozen_view_model(self):
        view = WorkspaceService(FakeStore()).get()
        with pytest.raises(Exception):
            view.symbol = "QQQ"          # frozen: a second renderer cannot drift

    def test_update_persists_and_returns_the_merged_state(self):
        store = FakeStore()
        service = WorkspaceService(store)
        view = service.update({"symbol": "nvda", "tab": "charts"})
        assert view.symbol == "NVDA"
        assert store.doc["tab"] == "charts"
        assert store.writes == 1

    def test_reset_clears_layouts_too(self):
        """A 'reset my workspace' that silently kept twenty stale layouts is
        the half-reset a user then reports as 'reset doesn't work'."""
        store = FakeStore({"symbol": "QQQ", "layouts": {"mine": {"a": 1}}})
        view = WorkspaceService(store).reset()
        assert view.symbol == "SPY"
        assert view.layouts == {}

    def test_a_corrupt_stored_document_still_yields_a_usable_workspace(self):
        """The startup path. `settings.json` is a file a user can open."""
        store = FakeStore({"symbol": ["not", "a", "symbol"], "layouts": 7,
                           "timeframe": {"nope": True}})
        view = WorkspaceService(store).get()
        assert view.symbol == "SPY" and view.timeframe == "1d" and view.layouts == {}

    def test_a_clock_stamps_updated_and_its_absence_does_not(self):
        from datetime import datetime, timezone
        stamp = datetime(2026, 7, 28, tzinfo=timezone.utc)
        assert WorkspaceService(FakeStore()).update({}).updated is None
        stamped = WorkspaceService(FakeStore(), clock=lambda: stamp).update({})
        assert stamped.updated == stamp.isoformat()

    def test_the_view_serializes_to_primitives(self):
        doc = WorkspaceService(FakeStore()).get().to_dict()
        json.dumps(doc, allow_nan=False)
