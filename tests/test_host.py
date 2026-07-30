"""Host platform abstraction (V0.7.0).

Two things are worth testing here and they are different in kind. The capability
profiles are *data* — the tests assert they are internally consistent and that
the blockers the architecture depends on are actually recorded, because a
profile that quietly granted `BIND_LISTENER` to iOS would invalidate the entire
desktop-as-host hosting model without anything failing. The adapters are
*behaviour* — and the behaviour that matters is that a host call never raises,
because every one of them sits on a path where an OS refusal is a normal state.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from optionspilot.host import (
    HOST_PROFILES, Capability, DesktopHost, HeadlessHost, HostAdapter,
    HostProfile, current_host, profile_for, set_host,
)
from optionspilot.host.adapter import SINGLE_INSTANCE_PORT
from optionspilot.host.capabilities import detect_profile_name


class TestProfiles:
    def test_every_profile_is_self_consistent(self):
        for name, profile in HOST_PROFILES.items():
            assert profile.name == name, f"{name} profile is misfiled"
            assert profile.description.strip(), f"{name} has no description"
            # capabilities + missing partition the whole enum, with no overlap
            assert set(profile.capabilities) | set(profile.missing()) == set(Capability)
            assert not set(profile.capabilities) & set(profile.missing())

    def test_notes_only_ever_explain_a_MISSING_capability(self):
        """A note on a capability the host HAS is a contradiction.

        It reads as documentation and is worse than none: the next person
        implementing that target believes a blocker exists where there is not
        one, or (worse) that one does not exist where it does.
        """
        for profile in HOST_PROFILES.values():
            missing = {c.value for c in profile.missing()}
            stray = set(profile.notes) - missing
            assert not stray, \
                f"{profile.name} notes a capability it HAS: {sorted(stray)}"

    def test_only_desktop_and_headless_claim_to_be_implemented(self):
        """`implemented` is the flag that stops a design target being mistaken
        for a shipped one. Exactly two things run today."""
        live = {n for n, p in HOST_PROFILES.items() if p.implemented}
        assert live == {"desktop", "headless"}

    def test_desktop_can_do_everything(self):
        assert not HOST_PROFILES["desktop"].missing()

    @pytest.mark.parametrize("target", ["ios", "android"])
    def test_mobile_targets_cannot_host(self, target):
        """The load-bearing one.

        The whole architecture — the desktop stays flagship, the phone is a
        companion, the paper account has exactly one writer — follows from
        mobile clients not being able to bind a listener or own the data. If
        this ever flips, `docs/ARCHITECTURE-PLATFORM.md` is wrong, not this test.
        """
        profile = HOST_PROFILES[target]
        assert not profile.can(Capability.BIND_LISTENER)
        assert not profile.can(Capability.SELF_UPDATE)
        assert not profile.can(Capability.WIDE_VIEWPORT)
        # and each of those absences carries its reason
        for cap in ("bind_listener", "self_update", "wide_viewport"):
            assert profile.notes.get(cap), \
                f"{target} is missing {cap} without saying why"

    def test_mobile_notifications_are_push_only(self):
        """A server-originated notification cannot be a local toast on a phone.

        `notify/`'s DesktopNotifier assumption — that the process raising the
        event is the process showing it — is exactly what does not hold, and
        `REMOTE_PUSH_ONLY` is where that is written down.
        """
        for target in ("ios", "android"):
            profile = HOST_PROFILES[target]
            assert profile.can(Capability.REMOTE_PUSH_ONLY)
            assert not profile.can(Capability.TOAST)

    def test_web_has_no_durable_local_storage(self):
        """localStorage is a cache, not storage. This is the assertion behind
        the V0.7.0 decision to move workspace state server-side."""
        assert not HOST_PROFILES["web"].can(Capability.LOCAL_FILESYSTEM)

    def test_unknown_target_raises_rather_than_defaulting(self):
        """A typo'd target silently resolving to `desktop` would grant every
        capability to a host that has none of them."""
        with pytest.raises(KeyError):
            profile_for("iphone")

    def test_profile_serializes_to_primitives(self):
        import json
        doc = HOST_PROFILES["ios"].to_dict()
        json.dumps(doc, allow_nan=False)      # must not raise
        assert isinstance(doc["capabilities"], list)
        assert all(isinstance(c, str) for c in doc["capabilities"])

    def test_detected_profile_is_always_an_implemented_one(self):
        """A running process cannot be inside a design target."""
        assert HOST_PROFILES[detect_profile_name()].implemented


class TestAdapters:
    def test_desktop_host_reports_a_root_without_touching_disk(self, tmp_path):
        host = DesktopHost(tmp_path / "nonexistent")
        assert host.data_root() == tmp_path / "nonexistent"
        assert not (tmp_path / "nonexistent").exists()

    def test_headless_shares_the_desktop_storage_root(self, tmp_path):
        """Model B (a home server hosting the same account) is the same
        product, not a different one — so it must not use a different root."""
        assert DesktopHost(tmp_path).data_root() == HeadlessHost(tmp_path).data_root()

    def test_headless_declines_capabilities_it_lacks(self, tmp_path):
        host = HeadlessHost(tmp_path)
        assert not host.supports(Capability.TOAST)
        assert host.supports(Capability.BIND_LISTENER)

    def test_open_external_url_is_false_not_an_exception_when_unsupported(
            self, tmp_path):
        """A host call on a path that cannot succeed returns False.

        Every caller of this is a convenience — a signup link, a release note —
        and a raise would turn "this box has no browser" into a 500.
        """
        host = HeadlessHost(tmp_path)
        assert host.open_external_url("https://example.invalid") is False

    def test_open_external_url_swallows_a_failing_browser(self, tmp_path,
                                                          monkeypatch):
        import optionspilot.host.adapter as adapter
        monkeypatch.setattr(adapter.webbrowser, "open",
                            lambda url: (_ for _ in ()).throw(OSError("no display")))
        assert DesktopHost(tmp_path).open_external_url("https://example.com") is False

    def test_single_instance_is_exclusive_and_released_on_close(self, tmp_path):
        """A socket, not a lock file, precisely so a hard kill cannot lock the
        user out of their own app — the OS releases it when the process dies."""
        host = DesktopHost(tmp_path)
        first = host.acquire_single_instance()
        if first is None:
            pytest.skip(f"port {SINGLE_INSTANCE_PORT} already held by a real app")
        try:
            assert host.acquire_single_instance() is None
        finally:
            first.close()
        second = host.acquire_single_instance()
        assert second is not None
        second.close()

    def test_the_mutex_waits_out_a_departing_predecessor(self, tmp_path):
        """"Restart" spawns the successor from the outgoing process.

        The two overlap by however long the parent needs to unwind, so a
        single-attempt bind turned an ordinary restart into "OptionsPilot is
        already running" — the successor lost the race to its own parent.
        """
        host = DesktopHost(tmp_path)
        outgoing = host.acquire_single_instance()
        if outgoing is None:
            pytest.skip(f"port {SINGLE_INSTANCE_PORT} already held by a real app")
        threading.Timer(0.3, outgoing.close).start()
        successor = host.acquire_single_instance()
        try:
            assert successor is not None, "the successor never got the port"
        finally:
            if successor is not None:
                successor.close()

    def test_a_genuine_second_instance_is_still_refused_promptly(self, tmp_path):
        """Retrying must not turn "already running" into a long stall."""
        host = DesktopHost(tmp_path)
        held = host.acquire_single_instance()
        if held is None:
            pytest.skip(f"port {SINGLE_INSTANCE_PORT} already held by a real app")
        try:
            started = time.monotonic()
            assert host.acquire_single_instance() is None
            assert time.monotonic() - started < 3.0
        finally:
            held.close()

    def test_describe_carries_no_user_data(self, tmp_path):
        """`describe()` is diagnostics-safe by contract; anything a user is
        invited to attach to a public bug report is held to
        `data/credentials.py`'s standard."""
        doc = DesktopHost(tmp_path).describe()
        assert set(doc) == {"host", "python_platform", "profile", "data_root"}

    def test_a_custom_host_can_replace_the_process_default(self, tmp_path):
        """The seam a future mobile backend (or a test) uses."""
        class Fake(HostAdapter):
            profile = HostProfile("desktop", "fake", frozenset(), implemented=True)

            def data_root(self):
                return tmp_path

            def temp_dir(self):
                return tmp_path

        try:
            set_host(Fake())
            assert current_host().data_root() == tmp_path
            assert current_host().acquire_single_instance() is not None
        finally:
            set_host(None)

    def test_resetting_to_none_re_resolves(self):
        set_host(None)
        assert isinstance(current_host(), (DesktopHost, HeadlessHost))
