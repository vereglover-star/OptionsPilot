"""What a client running on a given kind of device can actually do.

This module is DATA. It ships no behaviour and makes no decision; it exists so
that "does this target support X?" has one answer that a test can read, instead
of being rediscovered as an `if sys.platform` in whichever module hit the wall
first.

Two rules govern it, and both exist because the naive alternative is worse:

1. **A capability describes the HOST, never the user's preference.** `TOAST` is
   whether the platform can raise a system notification at all, not whether the
   user asked for one — that is `NotifyConfig.desktop`, and merging the two
   would make an unsupported platform indistinguishable from a switched-off
   feature. A user can turn off something the host supports; a user cannot turn
   on something it does not.

2. **A profile for a target that does not exist yet is a DESIGN DOCUMENT, and
   is marked as such.** `ios`, `android` and `web` are here because the point of
   V0.7.0 is to be ready for them, and a profile that says `SELF_UPDATE: False`
   is what stops a future session wiring the auto-updater into a mobile build
   and discovering App Store policy the hard way. `implemented=False` says
   loudly that nothing runs on it today. Do not read an unimplemented profile as
   a promise that the target works.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass, field


class Capability(enum.Enum):
    """One thing a host either can or cannot do.

    Named for the *need*, not the mechanism — `LOCAL_FILESYSTEM` rather than
    `has_appdata`, because the whole purpose is that callers stop knowing the
    mechanism.
    """

    #: A writable, durable, private directory survives process restart.
    #: False on a client that holds only a cache it may lose at any time.
    LOCAL_FILESYSTEM = "local_filesystem"
    #: The process may bind a TCP listener (i.e. it can BE the server).
    #: This is the hinge of the whole hosting model: desktop can, phones cannot.
    BIND_LISTENER = "bind_listener"
    #: A long-lived background loop may run while the UI is not in front.
    BACKGROUND_LOOP = "background_loop"
    #: A system-level notification can be raised by this process itself.
    TOAST = "toast"
    #: Notifications can only arrive via a remote push service (APNs/FCM).
    REMOTE_PUSH_ONLY = "remote_push_only"
    #: The app can replace its own binary (the auto-updater's precondition).
    SELF_UPDATE = "self_update"
    #: A URL can be handed to an external browser.
    OPEN_EXTERNAL_URL = "open_external_url"
    #: Text can be placed on a system clipboard.
    CLIPBOARD = "clipboard"
    #: A file can be written somewhere the user chooses (an export target).
    USER_FILE_EXPORT = "user_file_export"
    #: The process can hold a cross-process single-instance lock.
    SINGLE_INSTANCE_LOCK = "single_instance_lock"
    #: A resizable window whose geometry the app controls.
    WINDOW_MANAGEMENT = "window_management"
    #: Arbitrary subprocesses may be launched.
    SUBPROCESS = "subprocess"
    #: Enough screen area for the multi-panel desktop workspace.
    #: Drives layout choice, and is the honest reason mobile is a COMPANION:
    #: the backtest and market-data-control surfaces assume this.
    WIDE_VIEWPORT = "wide_viewport"


@dataclass(frozen=True, slots=True)
class HostProfile:
    """The capability set of one target, plus how real it is."""

    name: str
    #: Human sentence for a diagnostics page or a design conversation.
    description: str
    capabilities: frozenset[Capability]
    #: False for a target nothing runs on yet. See rule 2 in the module docstring.
    implemented: bool = False
    #: Why a notable capability is absent, keyed by capability value. Present so
    #: a blocker carries its reason to whoever reads it next, rather than the
    #: reason living in a commit message nobody will find.
    notes: dict[str, str] = field(default_factory=dict)

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def missing(self) -> list[Capability]:
        return sorted(set(Capability) - self.capabilities, key=lambda c: c.value)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "implemented": self.implemented,
            "capabilities": sorted(c.value for c in self.capabilities),
            "missing": [c.value for c in self.missing()],
            "notes": dict(self.notes),
        }


_ALL = frozenset(Capability)

#: Every target OptionsPilot is designed against. `desktop` is the only one that
#: runs today; the rest are the architecture's stated targets and exist so a
#: blocker is visible before someone builds into it.
HOST_PROFILES: dict[str, HostProfile] = {
    "desktop": HostProfile(
        name="desktop",
        description="Windows / macOS / Linux — the flagship. Hosts the server, "
                    "owns the data, runs the cycle loop.",
        capabilities=_ALL,
        implemented=True,
    ),
    "headless": HostProfile(
        name="headless",
        description="`python -m optionspilot serve` on a machine with no "
                    "desktop session — CI, a home server, a container.",
        capabilities=_ALL - {
            Capability.TOAST,
            Capability.CLIPBOARD,
            Capability.WINDOW_MANAGEMENT,
            Capability.OPEN_EXTERNAL_URL,
            Capability.WIDE_VIEWPORT,
        },
        implemented=True,
        notes={
            "toast": "no desktop session to raise a notification into; the "
                     "email notifier still works",
            "wide_viewport": "there is no viewport at all — a headless host "
                             "serves clients, it does not render",
        },
    ),
    "web": HostProfile(
        name="web",
        description="A browser pointed at a `serve`-mode instance. Exists "
                    "today in the sense that the served UI is a web client; "
                    "not a separate build.",
        capabilities=frozenset({
            Capability.OPEN_EXTERNAL_URL,
            Capability.CLIPBOARD,
            Capability.USER_FILE_EXPORT,
            Capability.WIDE_VIEWPORT,
        }),
        implemented=False,
        notes={
            "local_filesystem": "localStorage is not durable storage — a "
                                "cleared profile loses it silently, which is "
                                "exactly why workspace state moved server-side "
                                "in V0.7.0",
            "bind_listener": "a browser tab cannot be the server; it is always "
                             "a client of one",
            "background_loop": "no execution while the tab is closed",
            "self_update": "the page is whatever the server serves",
        },
    ),
    "ios": HostProfile(
        name="ios",
        description="iPhone / iPad companion client, paired to a desktop host. "
                    "DESIGN TARGET — no code exists.",
        capabilities=frozenset({
            Capability.LOCAL_FILESYSTEM,
            Capability.REMOTE_PUSH_ONLY,
            Capability.OPEN_EXTERNAL_URL,
            Capability.CLIPBOARD,
            Capability.USER_FILE_EXPORT,
        }),
        implemented=False,
        notes={
            "bind_listener": "the phone is a client of the user's desktop; the "
                             "desktop-as-host model exists because of this",
            "background_loop": "iOS schedules background refresh, the app does "
                               "not decide when it runs",
            "toast": "a local notification is possible, but anything triggered "
                     "by the SERVER must arrive through APNs — hence "
                     "REMOTE_PUSH_ONLY rather than TOAST",
            "self_update": "the App Store owns the binary",
            "subprocess": "not permitted",
            "wide_viewport": "iPad approaches it; iPhone does not. The "
                             "companion charter follows from this, not from "
                             "a product preference",
        },
    ),
    "android": HostProfile(
        name="android",
        description="Android companion client. DESIGN TARGET — no code exists. "
                    "Deliberately identical to ios except where the platform "
                    "genuinely differs, because a third client needing a "
                    "server change would mean the design failed.",
        capabilities=frozenset({
            Capability.LOCAL_FILESYSTEM,
            Capability.BACKGROUND_LOOP,
            Capability.REMOTE_PUSH_ONLY,
            Capability.OPEN_EXTERNAL_URL,
            Capability.CLIPBOARD,
            Capability.USER_FILE_EXPORT,
        }),
        implemented=False,
        notes={
            "bind_listener": "technically possible, deliberately not designed "
                             "for — a phone that sleeps is not a host for a "
                             "paper account",
            "toast": "local notifications work; server-originated ones need FCM",
            "self_update": "Play Store owns the binary",
            "wide_viewport": "tablets approach it; phones do not",
        },
    ),
}


def profile_for(name: str) -> HostProfile:
    """The profile for a target name. Raises rather than guessing — a typo'd
    target silently returning `desktop` would grant every capability."""
    try:
        return HOST_PROFILES[name]
    except KeyError:
        raise KeyError(
            f"unknown host target {name!r} "
            f"(known: {sorted(HOST_PROFILES)})") from None


def detect_profile_name() -> str:
    """Best-effort name of the profile this process is running under.

    Only ever returns an *implemented* profile: this answers "where am I", and
    a process can never be running inside a design target.
    """
    return "desktop" if sys.platform in ("win32", "darwin", "linux") else "headless"
