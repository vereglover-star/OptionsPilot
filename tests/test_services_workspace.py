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
    """The `RuntimeSettings` surface WorkspaceService actually uses."""

    def __init__(self, doc=None):
        self.doc = doc if doc is not None else {}
        self.writes = 0

    def workspace_state(self):
        return dict(self.doc)

    def set_workspace_state(self, state):
        self.doc = dict(state)
        self.writes += 1
        return dict(self.doc)


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
    ])
    def test_no_input_can_make_normalize_raise(self, garbage):
        """The actual guarantee. The failure mode for a bad preferences file
        must be 'you lose a preference', never 'the app will not start'."""
        doc = ws.normalize(garbage)
        assert set(doc) == set(ws.DEFAULTS) | {"updated"}
        json.dumps(doc, allow_nan=False)   # and it must survive the wire


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
