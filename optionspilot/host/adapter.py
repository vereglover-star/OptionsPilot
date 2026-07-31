"""HostAdapter — the runtime interface to the machine this process is on.

Every OS-shaped question the application asks goes through here: where durable
storage lives, where scratch space lives, how a URL reaches a browser, how two
copies of the app avoid fighting over one paper account. Before V0.7.0 each of
those was answered inline at its one call site, which is fine right up to the
moment there is a second kind of caller — and then each one is a separate
discovery of the same problem.

The rule that makes this worth having: **a business-logic module may ask a
capability question, never an `sys.platform` question.** `if not host.supports(
Capability.TOAST)` survives a port; `if sys.platform == "win32"` is a bug on
every platform that is not Windows and a silent one on most of them.

`DesktopHost` is byte-for-byte the behaviour V0.6.1 shipped — the single
instance lock is the same socket bound to the same port, storage is the same
`AppPaths`. Nothing here changes what the desktop app does.
"""

from __future__ import annotations

import abc
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.paths import AppPaths
from optionspilot.host.capabilities import (
    Capability, HostProfile, detect_profile_name, profile_for,
)

log = get_logger("ui")

#: The port `DesktopHost` binds as a cross-process mutex. Unchanged from
#: `ui/desktop.py`, where this lived before V0.7.0 — an install upgraded
#: mid-session must still recognise the older process's lock.
SINGLE_INSTANCE_PORT = 8786


class HostAdapter(abc.ABC):
    """One host. Subclass per platform; never branch inside a method."""

    #: The declarative capability set. Subclasses set this once.
    profile: HostProfile

    def supports(self, capability: Capability) -> bool:
        return self.profile.can(capability)

    # ── storage ──────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def data_root(self) -> Path:
        """The durable, private, per-user storage root."""

    @abc.abstractmethod
    def temp_dir(self) -> Path:
        """Scratch space. May be cleared by the OS at any time."""

    # ── outward actions (all best-effort, none may raise) ────────────────────

    def open_external_url(self, url: str) -> bool:
        """Hand a URL to whatever shows web pages here. Returns whether it went.

        Best-effort by contract: this is only ever used for a signup page or a
        release note, and a headless box with no browser is a normal state, not
        an error worth propagating into a request handler.
        """
        if not self.supports(Capability.OPEN_EXTERNAL_URL):
            return False
        try:
            return bool(webbrowser.open(url))
        except Exception as exc:  # noqa: BLE001 — never raise out of a host call
            log.debug("open_external_url(%s) failed: %s", url, exc)
            return False

    def acquire_single_instance(self):
        """Claim the right to be the only running copy.

        Returns an opaque handle to hold for the process lifetime, or None if
        another copy already holds it. Callers must not inspect the handle —
        that is the whole point; on a host with no such concept the answer is
        simply "you are the only one".
        """
        return _NULL_LOCK

    def set_startup(self, enabled: bool, command: str) -> bool:
        """Enable per-user startup where this host supports it.

        The default is deliberately inert; a future menu-bar or headless host
        must not inherit Windows registry behavior accidentally.
        """
        return False

    # ── reporting ────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Diagnostics-safe description. Contains no user data and no secret,
        so it is safe to attach to a public bug report — the standard
        `data/credentials.py` sets for anything exportable."""
        return {
            "host": self.profile.name,
            "python_platform": sys.platform,
            "profile": self.profile.to_dict(),
            "data_root": str(self.data_root()),
        }


class _NullLock:
    """The handle returned where single-instance has no meaning."""

    def close(self) -> None:
        return None


_NULL_LOCK = _NullLock()


class DesktopHost(HostAdapter):
    """Windows / macOS / Linux with a desktop session. The flagship host."""

    def __init__(self, root: str | Path | None = None):
        self.profile = profile_for("desktop")
        self._paths = AppPaths(root)

    def data_root(self) -> Path:
        return self._paths.root

    def temp_dir(self) -> Path:
        return Path(tempfile.gettempdir())

    def acquire_single_instance(self, attempts: int = 5, delay: float = 0.2):
        """Hold a loopback port for the process lifetime as a mutex.

        Two instances sharing one SQLite paper account is corruption waiting to
        happen. A bound socket is used rather than a lock file because a lock
        file survives a hard kill and a socket does not — a crashed instance
        must never lock the user out of their own app.

        Retried briefly because "Restart" launches the successor from the
        outgoing process and the two necessarily overlap. The parent releases
        the port before spawning, so the retries only absorb scheduling jitter;
        a genuine second instance still gives up in about a second.
        """
        for attempt in range(max(1, attempts)):
            lock = socket.socket()
            try:
                lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
                return lock
            except OSError:
                lock.close()
                if attempt < attempts - 1:
                    time.sleep(delay)
        return None

    def set_startup(self, enabled: bool, command: str) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, "OptionsPilot", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "OptionsPilot")
                    except FileNotFoundError:
                        pass
            return True
        except OSError as exc:
            log.warning("could not update Windows startup registration: %s", exc)
            return False


class HeadlessHost(HostAdapter):
    """`serve` mode on a machine with no desktop session.

    Differs from `DesktopHost` in what it will *attempt*, not in what it stores:
    the data root is identical, because a headless server holding the same paper
    account is the documented model-B hosting option, not a different product.
    """

    def __init__(self, root: str | Path | None = None):
        self.profile = profile_for("headless")
        self._paths = AppPaths(root)

    def data_root(self) -> Path:
        return self._paths.root

    def temp_dir(self) -> Path:
        return Path(tempfile.gettempdir())

    def acquire_single_instance(self):
        return DesktopHost.acquire_single_instance(self)  # same socket mutex


# ── process-wide selection ───────────────────────────────────────────────────
#
# One host per process, resolved lazily and replaceable. A module-level default
# rather than an injected parameter everywhere because the host genuinely IS
# process-global — there is exactly one machine underneath — and threading it
# through every constructor would be ceremony that buys nothing. `set_host` is
# what a test (or a future mobile backend embedding this package) uses.

_lock = threading.Lock()
_host: HostAdapter | None = None


def current_host() -> HostAdapter:
    global _host
    with _lock:
        if _host is None:
            name = detect_profile_name()
            _host = DesktopHost() if name == "desktop" else HeadlessHost()
            log.debug("host resolved: %s", _host.profile.name)
        return _host


def set_host(host: HostAdapter | None) -> None:
    """Replace (or, with None, reset) the process host. Tests and embeddings."""
    global _host
    with _lock:
        _host = host
