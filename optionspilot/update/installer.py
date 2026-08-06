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
     our own ``/RELAUNCH`` switch, which ``installer/OptionsPilot.iss`` reads in
     a ``Check:`` on a second ``[Run]`` entry. The installer is the authority
     here because it is the only participant that knows when the file
     replacement finished — the app cannot know, since it must be dead for the
     install to proceed at all.

The current app process should exit shortly after a successful launch so the
installer can replace the running exe; the service coordinates that.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Callable

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.migration import create_backup
from optionspilot.core.paths import AppPaths
from optionspilot.update.models import SignatureVerdict, UpdateError

log = get_logger("update")

# Inno Setup unattended flags. VERYSILENT = no wizard + no progress window;
# SUPPRESSMSGBOXES = auto-answer message boxes; NORESTART = never reboot Windows
# (nothing to do with restarting the app); NOCANCEL = the user can't half-cancel
# a silent upgrade mid-flight.
SILENT_FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL"]

#: Our own switch, read by `RelaunchRequested` in `installer/OptionsPilot.iss`.
#: NOT an Inno Setup flag — Inno ignores parameters it does not recognise, which
#: is what lets a script define one.
#:
#: It replaces `/RESTARTAPPLICATIONS`, which was passed here for three releases
#: and could never have worked. That flag drives Restart Manager, and **RM can
#: only restart a process it closed itself**. This updater guarantees it closes
#: none: `apply_update` spawns Setup and the app then shuts *itself* down, so by
#: the time RM scans there is nothing registered. Worse if the timing went the
#: other way — RM closes a GUI app with `WM_CLOSE`, which lands in
#: `_DesktopController.on_closing`, which cancels every close it did not
#: sanction. That is the V0.12.1 hang, re-entered through a different door.
#:
#: So the installer relaunches from `[Run]` instead, which is also the only
#: participant that knows when the file replacement actually finished. The app
#: cannot know: it is required to be dead for the install to proceed.
RELAUNCH_FLAG = "/RELAUNCH"

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


# ---------------------------------------------------------------------------
# Authenticode verification (V0.9.0-C9-1)
# ---------------------------------------------------------------------------
# A checksum proves the file matches what the release published. A signature
# proves WHO published it. This is the second one, and it lives here because
# this module is the updater's sanctioned OS boundary — the one file in
# `update/` that `tests/test_architecture.py` permits to branch on
# `sys.platform` ("launching an installer IS OS-specific"). `validation.py`
# stays pure and takes the verdict as an injected callable, so `update/` never
# has to import `host/`, which its {core}-only allow-list forbids.
#
# WinVerifyTrust, NOT signtool.exe. The C9 plan proposed shelling out to
# `signtool verify`, which is right for the BUILD (a GitHub runner has the
# Windows SDK) and wrong for the CLIENT: signtool ships with the SDK, which no
# ordinary user has installed. A signtool-based check would return "cannot
# determine" on essentially every real machine — a gate present in the code and
# inert in production, which is the exact failure this repo already learned
# from `RiskManager.approve_manual_entry`. wintrust.dll is the API signtool
# itself calls, and it is on every Windows install since XP.

#: WINTRUST_ACTION_GENERIC_VERIFY_V2 — the standard Authenticode policy.
_WVT_ACTION = (0x00AAC56B, 0xCD44, 0x11D0, (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))

_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0          # revocation needs the network; validation is offline
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2

# Deliberately NOT set: WTD_SAFER_FLAG (0x100). Measured on Windows 11, it
# collapses every distinct verdict into TRUST_E_NOSIGNATURE — a file TAMPERED
# after signing reports as "not signed at all". That is the Finnhub 401/403
# lesson in a new place: a diagnostic that confidently names the wrong cause is
# worse than one that says "I don't know", because someone acts on it.
_WTD_PROV_FLAGS = 0

#: Statuses meaning "this could not be evaluated" rather than "evaluated, and
#: not trustworthy". These map to UNKNOWN, never to a negative verdict.
_CANNOT_EVALUATE = frozenset({
    0x80092003,   # CRYPT_E_FILE_ERROR — the file could not be read
})

#: Statuses meaning "there is no signature here at all", which is a DIFFERENT
#: fact from "there is one and it is bad" — see models.SignatureVerdict. Every
#: release before V0.9.0 lands here, so conflating the two would strand them.
#:
#: SUBJECT_FORM_UNKNOWN belongs here and not with the failures: it means no
#: subject-interface package recognised the file's format, so no signature was
#: ever located — it is not a judgement on a signature. Whether the download is
#: a well-formed installer at all is `validate`'s name/size/hash question, and
#: answering it from here would be this layer naming a cause it cannot see.
_NO_SIGNATURE = frozenset({
    0x800B0100,   # TRUST_E_NOSIGNATURE — a valid PE carrying no signature
    0x800B0003,   # TRUST_E_SUBJECT_FORM_UNKNOWN — nothing here to verify
})

#: For the log line only. An unlisted non-zero status is still a failure; it
#: just gets reported by number instead of by name.
_STATUS_NAMES = {
    0x00000000: "valid, trusted signature",
    0x800B0100: "no signature present",
    0x800B0109: "signature present, but its root is not trusted",
    0x800B0101: "the signing certificate has expired",
    0x800B010A: "the certificate chain could not be built",
    0x800B0111: "the signing certificate is explicitly distrusted",
    0x80096010: "the file was modified after it was signed",
    0x800B0003: "unrecognised file format",
    0x80092003: "the file could not be read",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _WinTrustFileInfo(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_ulong),
                ("pcwszFilePath", ctypes.c_wchar_p),
                ("hFile", ctypes.c_void_p),
                ("pgKnownSubject", ctypes.c_void_p)]


class _WinTrustData(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_ulong),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", ctypes.c_ulong),
                ("fdwRevocationChecks", ctypes.c_ulong),
                ("dwUnionChoice", ctypes.c_ulong),
                ("pFile", ctypes.POINTER(_WinTrustFileInfo)),
                ("dwStateAction", ctypes.c_ulong),
                ("hWVTStateData", ctypes.c_void_p),
                ("pwszURLReference", ctypes.c_wchar_p),
                ("dwProvFlags", ctypes.c_ulong),
                ("dwUIContext", ctypes.c_ulong),
                ("pSignatureSettings", ctypes.c_void_p)]


def _win_verify_trust(path: Path) -> int | None:
    """Raw WinVerifyTrust status for a file, or ``None`` if unreachable.

    ``None`` means the question could not be ASKED — not on Windows, or
    wintrust.dll would not load. Never raises: this sits on a path where an OS
    refusal is a normal state, exactly like `host/adapter.py`.
    """
    if sys.platform != "win32":
        return None
    try:
        wintrust = ctypes.WinDLL("wintrust.dll")
    except (OSError, AttributeError) as exc:   # pragma: no cover - Windows-only
        log.debug("wintrust unavailable: %s", exc)
        return None

    action = _GUID(_WVT_ACTION[0], _WVT_ACTION[1], _WVT_ACTION[2],
                   (ctypes.c_ubyte * 8)(*_WVT_ACTION[3]))
    file_info = _WinTrustFileInfo(ctypes.sizeof(_WinTrustFileInfo),
                                  str(path), None, None)
    data = _WinTrustData()
    data.cbStruct = ctypes.sizeof(_WinTrustData)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_NONE
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = _WTD_STATEACTION_VERIFY
    data.dwProvFlags = _WTD_PROV_FLAGS
    try:
        status = wintrust.WinVerifyTrust(None, ctypes.byref(action),
                                         ctypes.byref(data))
        # CLOSE releases hWVTStateData. Skipping it leaks a handle per check.
        data.dwStateAction = _WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    except OSError as exc:                     # pragma: no cover - Windows-only
        log.debug("WinVerifyTrust raised for %s: %s", path, exc)
        return None
    return status & 0xFFFFFFFF


def classify_trust_status(status: int | None) -> SignatureVerdict:
    """Map a WinVerifyTrust status onto a :class:`SignatureVerdict`.

    Split out from the OS call so the mapping — the part with the actual policy
    in it — is testable on any platform, including the Ubuntu CI leg.

    The three ways of NOT being trusted are kept apart on purpose. ``UNSIGNED``
    is the normal state of every release before V0.9.0 and must remain
    installable in Phase 1; ``INVALID`` is the case the mechanism exists to
    catch; ``UNKNOWN`` is not a claim about the file at all. An unrecognised
    non-zero status is ``INVALID``, because defaulting an unknown refusal to
    anything softer would turn each error code Microsoft adds into a silent
    bypass.
    """
    if status is None:
        return SignatureVerdict.UNKNOWN
    if status == 0:
        return SignatureVerdict.TRUSTED
    if status in _CANNOT_EVALUATE:
        return SignatureVerdict.UNKNOWN
    if status in _NO_SIGNATURE:
        return SignatureVerdict.UNSIGNED
    return SignatureVerdict.INVALID


def verify_authenticode(path: Path | str) -> SignatureVerdict:
    """What does Authenticode say about ``path``?

    Never raises, on any platform. Off Windows the answer is always
    :attr:`SignatureVerdict.UNKNOWN` — an inability to check, never a negative
    verdict about the file.
    """
    path = Path(path)
    status = _win_verify_trust(path)
    verdict = classify_trust_status(status)
    if status is None:
        log.debug("authenticode not checkable here for %s", path.name)
    else:
        log.info("authenticode %s for %s: 0x%08X (%s)",
                 verdict.value.upper(), path.name, status,
                 _STATUS_NAMES.get(status, "unrecognised status"))
    return verdict


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
            # Ask the installer to start the app again once it has finished
            # replacing it. Still gated on `exe`: no frozen exe means a dev
            # checkout, where the installed app is not what is running.
            cmd.append(RELAUNCH_FLAG)
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
