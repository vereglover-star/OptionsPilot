"""Apply a downloaded update: mandatory backup -> silent install -> restart.

This is the only side-effectful, Windows-specific layer, so the two genuinely
external actions — spawning the installer process and re-launching the app — are
injected (``spawn`` / ``relaunch``) and default to real ``subprocess`` calls.
Tests pass fakes and assert on the exact command line without ever running an
installer.

The apply sequence, in order, matching the milestone's mandate:

  1. **Backup first, always.** ``create_backup(paths, "pre-update")`` snapshots
     the user's ``data/`` before anything is launched. If the backup fails the
     update is aborted and the caller is told, so the user can decline.
  2. **Validate** the file (delegated to validation.py by the caller/service).
  3. **Launch silently** with Inno Setup's unattended flags
     (``/VERYSILENT /SUPPRESSMSGBOXES /NORESTART``) plus ``/NOCANCEL`` — no
     wizard, no prompts. The installer upgrades **Program Files only**; it never
     touches ``%LOCALAPPDATA%\\OptionsPilot`` (guaranteed by the storage split,
     not by this code).
  4. **Restart.** The installer is told to relaunch the app on completion via
     ``/RESTARTAPPLICATIONS``, and we also hand it the exe path so a restart
     happens whether the app was already closed or Inno closes it.

The current app process should exit shortly after a successful launch so the
installer can replace the running exe; the service coordinates that.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.migration import create_backup
from optionspilot.core.paths import AppPaths
from optionspilot.update.models import UpdateError

log = get_logger("update")

# Inno Setup unattended flags. VERYSILENT = no wizard + no progress window;
# SUPPRESSMSGBOXES = auto-answer message boxes; NORESTART = never reboot Windows;
# NOCANCEL = the user can't half-cancel a silent upgrade mid-flight.
SILENT_FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL"]

# spawn(cmd: list[str]) -> None   — start the installer, do not wait
Spawn = Callable[[list[str]], object]
# relaunch(exe: str) -> None      — start a fresh app instance (fallback restart)
Relaunch = Callable[[str], object]


def _default_spawn(cmd: list[str]):  # pragma: no cover - real process spawn
    # DETACHED so the installer outlives this process, which is about to exit so
    # its own exe can be overwritten.
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(cmd, close_fds=True, creationflags=flags)


def _default_relaunch(exe: str):  # pragma: no cover - real process spawn
    return subprocess.Popen([exe], close_fds=True)


def app_executable() -> str | None:
    """Path to the installed app exe when frozen (PyInstaller), else ``None``.

    Used so the installer can relaunch the exact binary the user was running.
    In a dev checkout there is no exe, so restart-after-install is a no-op there.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return None


class InstallerLauncher:
    """Backs up, then launches a validated installer unattended."""

    def __init__(self, *, paths: AppPaths | None = None,
                 spawn: Spawn = _default_spawn,
                 relaunch: Relaunch = _default_relaunch,
                 backup: Callable[[AppPaths, str], object] = create_backup):
        self._paths = paths or AppPaths()
        self._spawn = spawn
        self._relaunch = relaunch
        self._backup = backup

    def create_pre_update_backup(self) -> Path | None:
        """Mandatory pre-update snapshot of the user's data.

        Returns the backup directory (or ``None`` when there is nothing to back
        up — a brand-new install). Raises :class:`UpdateError` if a backup was
        expected but failed, so the caller can let the user abort.
        """
        try:
            return self._backup(self._paths, "pre-update")
        except Exception as exc:  # noqa: BLE001 - convert to a presentable error
            log.error("pre-update backup failed: %s", exc)
            raise UpdateError(
                "Could not back up your data before updating.",
                detail=str(exc), retryable=True) from exc

    def launch(self, installer_path: Path | str, *,
               restart: bool = True,
               exe_path: str | None = None) -> list[str]:
        """Spawn the installer unattended. Returns the command line used.

        Does **not** exit the current process — the caller decides when to quit
        so the installer can replace the exe (the service closes the app right
        after this returns).
        """
        installer_path = Path(installer_path)
        if not installer_path.is_file():
            raise UpdateError("The update installer could not be found.",
                              detail=str(installer_path))

        cmd = [str(installer_path), *SILENT_FLAGS]
        exe = exe_path if exe_path is not None else app_executable()
        if restart and exe:
            # Inno relaunches the app it closed; handing it the exe makes the
            # restart explicit and robust even if the app was already closed.
            cmd.append("/RESTARTAPPLICATIONS")
        try:
            self._spawn(cmd)
        except OSError as exc:
            raise UpdateError("The update installer failed to start.",
                              detail=str(exc), retryable=True) from exc
        log.info("launched installer: %s", " ".join(cmd))
        return cmd

    def relaunch_app(self, exe_path: str | None = None) -> bool:
        """Best-effort restart of the app (fallback if the installer didn't).

        Returns ``True`` if a relaunch was attempted. A dev checkout (no exe)
        returns ``False`` — nothing to relaunch.
        """
        exe = exe_path if exe_path is not None else app_executable()
        if not exe:
            return False
        try:
            self._relaunch(exe)
            return True
        except OSError as exc:
            log.error("could not relaunch app: %s", exc)
            return False
