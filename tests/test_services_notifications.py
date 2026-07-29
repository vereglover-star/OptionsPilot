"""NotificationService and the sync-boundary inventory (V0.7.0).

Both are mostly *registry* code, and registry code fails in one characteristic
way: it goes stale silently. A notification kind that exists in `notify/` but
not in the catalogue routes as `info` forever; a store added to `AppPaths` but
not to the inventory is a file nobody decided the sync policy for. Neither
breaks anything today, and both are exactly the sort of omission that is
expensive to discover later — so the tests here are consistency assertions
between the registries and the code they claim to describe.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from optionspilot.notify.base import KINDS as NOTIFY_KINDS
from optionspilot.services import sync
from optionspilot.services.notifications import (
    CATALOGUE, SEVERITIES, NotificationService, is_pushable, severity_of,
)


class FakeEvent:
    def __init__(self, kind, title, body="", ts=None):
        self.kind, self.title, self.body = kind, title, body
        self.ts = ts or datetime(2026, 7, 28, tzinfo=timezone.utc)


class FakeCenter:
    def __init__(self, history=None, fail=False):
        self.history = list(history or [])
        self.sent = []
        self._fail = fail

    def notify(self, kind, title, body=""):
        if self._fail:
            raise RuntimeError("sink exploded")
        self.sent.append((kind, title, body))
        self.history.append(FakeEvent(kind, title, body))


class TestCatalogue:
    def test_every_notify_KIND_is_catalogued(self):
        """The staleness assertion. A kind `notify/` can raise but the
        catalogue does not know routes as `info` forever — silently, and only
        on the day a push sink exists to route it wrongly."""
        missing = set(NOTIFY_KINDS) - set(CATALOGUE)
        assert not missing, \
            f"notify.KINDS not in services CATALOGUE: {sorted(missing)}"

    def test_every_severity_is_one_of_the_declared_levels(self):
        for kind in CATALOGUE.values():
            assert kind.severity in SEVERITIES, kind.key

    def test_every_kind_says_when_it_fires(self):
        for kind in CATALOGUE.values():
            assert kind.when.strip(), f"{kind.key} has no description"

    def test_pushable_is_independent_of_severity(self):
        """Deliberately orthogonal. `provider_offline` is important and belongs
        in the app; pushing it to a phone at 3am teaches the user to switch
        notifications off entirely — which costs them the risk-limit alert too."""
        assert severity_of("provider_offline") == "important"
        assert is_pushable("provider_offline") is False
        assert is_pushable("risk_limit") is True

    def test_an_uncatalogued_kind_degrades_to_info_rather_than_critical(self):
        """Unknown, not fatal, and quiet rather than loud: the center has always
        accepted an uncatalogued kind and sent it anyway, so rejecting would
        drop notifications an older caller still raises — and escalating would
        let a typo wake a phone."""
        assert severity_of("some_future_kind") == "info"
        assert is_pushable("some_future_kind") is False


class TestService:
    def test_raising_an_uncatalogued_kind_still_sends(self):
        center = FakeCenter()
        assert NotificationService(center).raise_event("mystery", "T") is True
        assert center.sent == [("mystery", "T", "")]

    def test_a_failing_sink_never_raises_at_the_caller(self):
        """The cardinal rule inherited verbatim from `notify/base.py`: a
        notification failure must NEVER interrupt trading. Adding a layer above
        the center must not weaken it."""
        assert NotificationService(FakeCenter(fail=True)).raise_event(
            "trade_closed", "T") is False

    def test_recent_is_newest_first(self):
        """The ordering is the service's decision, not each client's. A second
        client getting it backwards shows a user their oldest notification as
        their newest — a disagreement that is undebuggable from either side."""
        center = FakeCenter([FakeEvent("trade_opened", "first"),
                             FakeEvent("trade_closed", "second")])
        assert [n.title for n in NotificationService(center).recent()] == \
            ["second", "first"]

    def test_recent_carries_the_catalogued_severity(self):
        center = FakeCenter([FakeEvent("risk_limit", "halt")])
        assert NotificationService(center).recent()[0].severity == "critical"

    def test_recent_respects_and_clamps_its_limit(self):
        center = FakeCenter([FakeEvent("trade_opened", str(i)) for i in range(50)])
        service = NotificationService(center)
        assert len(service.recent(5)) == 5
        assert service.recent(0) == []
        assert len(service.recent(10_000)) == 50    # clamped, not exploded

    def test_an_unreadable_history_yields_an_empty_list_not_a_500(self):
        class Broken:
            @property
            def history(self):
                raise RuntimeError("store gone")

        assert NotificationService(Broken()).recent() == []

    def test_views_serialize_to_primitives(self):
        center = FakeCenter([FakeEvent("trade_closed", "t", "b")])
        json.dumps([n.to_dict() for n in NotificationService(center).recent()],
                   allow_nan=False)


class TestSyncBoundaries:
    def test_credentials_are_the_only_NEVER_and_they_are_never(self):
        """The one entry whose policy is a prohibition rather than a strategy.
        `data/credentials.py` is built so a plaintext key leaves through exactly
        one method; a sync layer treating this as 'just another preferences
        file' defeats all of that in one line."""
        never = sync.never_sync()
        assert [o.path for o in never] == ["data/credentials.json"]

    def test_every_appaths_FILE_is_classified(self):
        """The staleness assertion for the inventory: a new store must not be
        addable without someone deciding what it means on a second device.

        Files only. A directory is not a persisted object — its contents are,
        and `data/coach/*.json` classifies the coach directory more usefully
        than an entry for the directory itself would. The two exceptions are
        `logs/` and `backups/`, which ARE classified wholesale because nothing
        inside them is individually meaningful.
        """
        from optionspilot.core.paths import AppPaths

        paths = AppPaths("/tmp/op-test")
        classified = {o.path for o in sync.INVENTORY}
        # A glob entry covers every file matching it; compare on the prefix
        # before the `*` so `data/coach/x.json` matches `data/coach/*.json`.
        globs = [p.split("*")[0] for p in classified if "*" in p]

        unclassified = []
        for attr in dir(paths):
            # `get_backtest_journal_db` takes a symbol; its family is
            # classified as `data/backtest_*.db`.
            if not attr.startswith("get_") or attr == "get_backtest_journal_db":
                continue
            value = getattr(paths, attr)()
            relative = value.relative_to(paths.root).as_posix()
            if not value.suffix:
                continue                      # a directory, not an object
            if relative in classified:
                continue
            if any(relative.startswith(g) for g in globs):
                continue
            unclassified.append(f"{attr}() -> {relative}")
        assert not unclassified, (
            "AppPaths files with no sync classification: "
            + ", ".join(sorted(unclassified))
            + " — add them to services/sync.INVENTORY")

    def test_the_journal_is_append_only_never_last_write_wins(self):
        """A last-write-wins merge on the system of record would silently delete
        a device's trades. The journal and the experience store are written
        once per trade and never edited, so union is the correct answer."""
        by_path = {o.path: o for o in sync.INVENTORY}
        assert by_path["data/journal.db"].policy is sync.SyncPolicy.APPEND_ONLY
        assert by_path["data/experience.db"].policy is sync.SyncPolicy.APPEND_ONLY

    def test_the_paper_account_has_exactly_one_writer(self):
        """The assertion the whole desktop-as-host model rests on."""
        by_path = {o.path: o for o in sync.INVENTORY}
        assert by_path["data/paper.db"].policy is sync.SyncPolicy.SINGLE_WRITER
        assert by_path["data/orders.db"].policy is sync.SyncPolicy.SINGLE_WRITER

    def test_settings_json_is_flagged_as_a_shared_writer(self):
        """One file, several domains — the entry most likely to break first
        under any replication scheme, because a phone writing its workspace
        must not clobber the desktop's trading mode."""
        shared = {o.path for o in sync.INVENTORY if o.shared_writer}
        assert "data/settings.json" in shared

    def test_every_entry_says_what_it_holds(self):
        for obj in sync.INVENTORY + sync.CLIENT_TRAPPED:
            assert obj.holds.strip(), f"{obj.path} does not say what it holds"

    def test_client_trapped_state_is_named_rather_than_omitted(self):
        """An inventory that quietly excludes what it cannot classify is worse
        than one that names the gap. Chart drawings are the remaining blocker
        and must stay visible as one."""
        trapped = {o.path for o in sync.CLIENT_TRAPPED}
        assert any("chDraw" in p for p in trapped)
        for obj in sync.CLIENT_TRAPPED:
            assert obj.rationale.strip(), f"{obj.path} does not say why"

    def test_every_declared_domain_has_at_least_one_entry(self):
        """Found by the V0.7.0 self-audit, and it is the subtler failure mode.

        `SyncDomain.WORKSPACE` existed with no inventory entry — every workspace
        fact had been folded into the `data/settings.json` PREFERENCES row — so
        `report()` skipped the domain entirely and the inventory read as
        complete while saying nothing about the one domain the milestone built.
        A domain with no entries is not evidence that nothing is in it; it is
        evidence that nobody classified what is.
        """
        empty = [d.value for d in sync.SyncDomain if not sync.by_domain(d)]
        assert not empty, (
            f"declared sync domains with no classified object: {empty} — "
            "either classify what belongs there or delete the domain")

    def test_report_is_json_safe_and_free_of_user_data(self):
        doc = sync.report()
        json.dumps(doc, allow_nan=False)
        assert doc["never_sync"] == ["data/credentials.json"]
        assert doc["counts"]["inventory"] == len(sync.INVENTORY)
