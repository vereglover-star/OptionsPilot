"""UpdateService — the application-facing facade and state machine.

One object the FastAPI layer and the desktop shell talk to. It owns the update
*state machine* (see :class:`UpdatePhase`), runs the network check and the
download on background threads so the UI never blocks, and persists the user's
preferences through an injected store. Every lower layer it drives is
replaceable via the constructor, so the whole service is tested offline with
fakes — no sockets, no real installer.

Guarantees the milestone requires:

  * **Startup is never slowed.** :meth:`maybe_check_on_launch` spawns a daemon
    thread and returns immediately; a network failure is swallowed.
  * **Always usable offline.** Every public method degrades to a clean state or
    a presentable error; nothing here raises into the caller.
  * **Backup before install, always.** :meth:`apply_update` takes a mandatory
    ``pre-update`` backup and aborts (letting the user cancel) if it fails.
  * **Data is never touched by the updater.** The service only reads GitHub,
    writes to a temp scratch dir, and launches the installer — which upgrades
    Program Files only.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from optionspilot.core.logging_setup import get_logger
from optionspilot.update import ui as fmt
from optionspilot.update.checker import UpdateChecker, is_check_due
from optionspilot.update.downloader import Downloader, default_download_dir
from optionspilot.update.github_api import DEFAULT_REPO, GitHubReleases
from optionspilot.update.installer import InstallerLauncher
from optionspilot.update.models import (
    DownloadProgress,
    ReleaseInfo,
    UpdateChannel,
    UpdateCheckResult,
    UpdateError,
    UpdatePhase,
)
from optionspilot.update.validation import validate
from optionspilot.update.version import Version

log = get_logger("update")


class PreferencesStore(Protocol):  # pragma: no cover - structural type
    """What the service needs from wherever preferences live (RuntimeSettings)."""

    def update_prefs(self) -> dict: ...
    def set_update_prefs(self, **patch) -> dict: ...


class InMemoryPrefs:
    """A standalone :class:`PreferencesStore` for tests/embedding."""

    def __init__(self, **initial):
        self._doc = {
            "auto_check": True,
            "frequency": "daily",
            "channel": "stable",
            "skip_version": None,
            "last_checked": None,
            "last_seen_version": None,
        }
        self._doc.update(initial)

    def update_prefs(self) -> dict:
        return dict(self._doc)

    def set_update_prefs(self, **patch) -> dict:
        self._doc.update(patch)
        return dict(self._doc)


class UpdateService:
    """Coordinates check -> download -> validate -> backup -> install -> restart."""

    def __init__(self, current_version: str | Version, prefs: PreferencesStore, *,
                 repo: str = DEFAULT_REPO,
                 client: GitHubReleases | None = None,
                 downloader: Downloader | None = None,
                 launcher: InstallerLauncher | None = None,
                 download_dir: Path | str | None = None,
                 on_install_launched: Callable[[], None] | None = None):
        self.current = (current_version if isinstance(current_version, Version)
                        else Version.parse(str(current_version)))
        self._prefs = prefs
        self._repo = repo
        self._client = client or GitHubReleases(repo)
        self._downloader = downloader or Downloader()
        self._launcher = launcher or InstallerLauncher()
        self._download_dir = Path(download_dir) if download_dir else default_download_dir()
        self._on_install_launched = on_install_launched

        self._lock = threading.RLock()
        self._phase = UpdatePhase.IDLE
        self._result: UpdateCheckResult | None = None
        self._release: ReleaseInfo | None = None
        self._progress = DownloadProgress()
        self._download_path: str | None = None
        self._error: str | None = None
        self._check_thread: threading.Thread | None = None
        self._download_thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def set_install_hook(self, hook: Callable[[], None] | None) -> None:
        """Register a callback invoked right after the installer is launched.

        The desktop shell uses this to close its window so the running exe can be
        replaced. Optional — when unset, the installer's own CloseApplications
        handling takes care of shutting the app down."""
        self._on_install_launched = hook

    # ── preferences ──────────────────────────────────────────────────────────
    def _channel(self) -> UpdateChannel:
        raw = str(self._prefs.update_prefs().get("channel", "stable"))
        return UpdateChannel.BETA if raw == "beta" else UpdateChannel.STABLE

    def _skip_version(self) -> str | None:
        return self._prefs.update_prefs().get("skip_version")

    # ── check ────────────────────────────────────────────────────────────────
    def is_due(self, now: datetime | None = None) -> bool:
        prefs = self._prefs.update_prefs()
        last = _parse_iso(prefs.get("last_checked"))
        return is_check_due(str(prefs.get("frequency", "daily")), last, now)

    def maybe_check_on_launch(self) -> bool:
        """Auto-check in the background if enabled and due. Returns whether a
        check was started. Never blocks, never raises."""
        prefs = self._prefs.update_prefs()
        if not prefs.get("auto_check", True):
            return False
        if not self.is_due():
            return False
        self.check_async()
        return True

    def check_async(self) -> None:
        """Run a check on a daemon thread (idempotent while one is in flight)."""
        with self._lock:
            if self._check_thread is not None and self._check_thread.is_alive():
                return
            self._phase = UpdatePhase.CHECKING
            self._check_thread = threading.Thread(
                target=self._run_check, name="update-check", daemon=True)
            self._check_thread.start()

    def check_now(self) -> UpdateCheckResult:
        """Synchronous check (used by the manual 'Check for Updates' action and
        tests). Persists state; never raises."""
        return self._run_check()

    def _run_check(self) -> UpdateCheckResult:
        checker = UpdateChecker(self.current, repo=self._repo, client=self._client)
        result = checker.check(channel=self._channel())
        with self._lock:
            self._result = result
            self._error = result.error
            if result.error:
                self._phase = UpdatePhase.ERROR
            elif result.update_available and result.release \
                    and result.release.has_installer:
                self._release = result.release
                self._phase = UpdatePhase.AVAILABLE
            else:
                self._phase = UpdatePhase.UP_TO_DATE
        # Persist the check timestamp + what we saw (for "last checked" display).
        self._prefs.set_update_prefs(
            last_checked=datetime.now(timezone.utc).isoformat(),
            last_seen_version=str(result.latest) if result.latest else None,
        )
        return result

    # ── skip / preferences mutation ──────────────────────────────────────────
    def skip_current(self) -> dict:
        """Dismiss the currently-offered version so it stops prompting."""
        with self._lock:
            v = self._result.latest if self._result else None
        return self._prefs.set_update_prefs(skip_version=str(v) if v else None)

    def set_preferences(self, **patch) -> dict:
        """Validate + persist user-facing preference changes from Settings."""
        clean: dict = {}
        if "auto_check" in patch:
            clean["auto_check"] = bool(patch["auto_check"])
        if "frequency" in patch:
            freq = str(patch["frequency"])
            if freq not in ("launch", "daily", "weekly"):
                raise UpdateError(f"invalid update frequency: {freq!r}")
            clean["frequency"] = freq
        if "channel" in patch:
            chan = str(patch["channel"])
            if chan not in ("stable", "beta"):
                raise UpdateError(f"invalid update channel: {chan!r}")
            clean["channel"] = chan
        if "skip_version" in patch:
            sv = patch["skip_version"]
            clean["skip_version"] = str(sv) if sv else None
        return self._prefs.set_update_prefs(**clean)

    # ── download ─────────────────────────────────────────────────────────────
    def start_download(self) -> bool:
        """Begin downloading the available installer on a background thread.

        Returns ``False`` if there is nothing to download or a download is
        already running. Never raises.
        """
        with self._lock:
            if self._release is None or not self._release.has_installer:
                return False
            if self._download_thread is not None and self._download_thread.is_alive():
                return False
            self._cancel.clear()
            self._progress = DownloadProgress()
            self._download_path = None
            self._error = None
            self._phase = UpdatePhase.DOWNLOADING
            release = self._release
            self._download_thread = threading.Thread(
                target=self._run_download, args=(release,),
                name="update-download", daemon=True)
            self._download_thread.start()
            return True

    def _run_download(self, release: ReleaseInfo) -> None:
        def on_progress(p: DownloadProgress) -> None:
            with self._lock:
                self._progress = p
        result = self._downloader.download(
            release.installer, dest_dir=self._download_dir,
            progress_cb=on_progress, cancel=self._cancel)
        with self._lock:
            if result.cancelled:
                self._phase = UpdatePhase.AVAILABLE
                self._download_path = None
                return
            if result.error:
                self._phase = UpdatePhase.ERROR
                self._error = result.error
                return
            self._download_path = result.path
            self._phase = UpdatePhase.DOWNLOADED

    def cancel_download(self) -> None:
        """Request cancellation of an in-flight download (safe to call anytime)."""
        self._cancel.set()

    def progress(self) -> DownloadProgress:
        with self._lock:
            return self._progress

    # ── apply ────────────────────────────────────────────────────────────────
    def apply_update(self, *, restart: bool = True) -> dict:
        """Validate the download, back up, and launch the installer.

        Order is load-bearing: validate -> backup (mandatory) -> launch. Any
        failure returns ``{"ok": False, "error": ...}`` with a presentable
        message; only on success does the caller (server/desktop) shut the app
        down so the installer can replace the exe.
        """
        with self._lock:
            path = self._download_path
            release = self._release
        if not path or not Path(path).is_file():
            return {"ok": False, "error": "No downloaded update is ready to install."}

        # 1. Validate before we ever execute anything.
        with self._lock:
            self._phase = UpdatePhase.VERIFYING
        expected_size = release.installer.size if release and release.installer else None
        verdict = validate(path, expected_size=expected_size)
        if not verdict.ok:
            with self._lock:
                self._phase = UpdatePhase.ERROR
                self._error = verdict.message
            return {"ok": False, "error": verdict.message}

        # 2. Mandatory pre-update backup — abort if it fails.
        try:
            backup_dir = self._launcher.create_pre_update_backup()
        except UpdateError as exc:
            with self._lock:
                self._phase = UpdatePhase.ERROR
                self._error = exc.message
            return {"ok": False, "error": exc.message, "detail": exc.detail}

        # 3. Launch silently.
        try:
            cmd = self._launcher.launch(path, restart=restart)
        except UpdateError as exc:
            with self._lock:
                self._phase = UpdatePhase.ERROR
                self._error = exc.message
            return {"ok": False, "error": exc.message, "detail": exc.detail}

        with self._lock:
            self._phase = UpdatePhase.INSTALLING
        if self._on_install_launched is not None:
            # Let the shell close its window so the installer can replace the exe.
            try:
                self._on_install_launched()
            except Exception:  # noqa: BLE001
                log.debug("on_install_launched hook raised", exc_info=True)
        return {"ok": True, "backup": str(backup_dir) if backup_dir else None,
                "command": cmd}

    # ── snapshots for the UI ─────────────────────────────────────────────────
    def snapshot(self) -> dict:
        """Everything the Settings panel and the dialog need in one payload."""
        with self._lock:
            prefs = self._prefs.update_prefs()
            result = self._result
            phase = self._phase
            progress = self._progress
        skip = prefs.get("skip_version")
        latest = str(result.latest) if result and result.latest else None
        available = bool(result and result.update_available
                         and result.release and result.release.has_installer)
        payload = {
            "phase": phase.value,
            "current_version": str(self.current),
            "latest_version": latest,
            "update_available": available,
            "dismissed": bool(available and skip and latest == skip),
            "error": result.error if result else None,
            "auto_check": prefs.get("auto_check", True),
            "frequency": prefs.get("frequency", "daily"),
            "channel": prefs.get("channel", "stable"),
            "skip_version": skip,
            "last_checked": prefs.get("last_checked"),
            "release": None,
            "progress": fmt.progress_payload(progress, phase),
            "download_path": self._download_path,
        }
        if result is not None and available and result.release is not None:
            payload["release"] = fmt.release_payload(result.release)
        return payload


def _parse_iso(text) -> datetime | None:
    if not text or not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
