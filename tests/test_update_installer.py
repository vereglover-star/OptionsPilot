"""InstallerLauncher: mandatory backup, silent flags, restart, error paths.

Also home to the Authenticode verifier (V0.9.0-C9-1), which lives in the same
module because `update/installer.py` is the updater's sanctioned OS boundary.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from optionspilot.update import installer as installer_mod
from optionspilot.update.installer import (
    RELAUNCH_FLAG, SILENT_FLAGS, InstallerLauncher, classify_trust_status,
    verify_authenticode,
)
from optionspilot.update.models import SignatureVerdict, UpdateError


class _Spawn:
    def __init__(self, fail=False):
        self.commands = []
        self.fail = fail

    def __call__(self, cmd):
        if self.fail:
            raise OSError("cannot start process")
        self.commands.append(cmd)
        return object()


def _installer(tmp_path, name="OptionsPilot-Setup-v0.5.0.exe"):
    p = tmp_path / name
    p.write_bytes(b"MZ...")
    return p


class TestBackup:
    def test_backup_called_with_pre_update_label(self, tmp_path):
        seen = {}

        def fake_backup(paths, label):
            seen["label"] = label
            return tmp_path / "backups" / "snap"

        launcher = InstallerLauncher(spawn=_Spawn(), backup=fake_backup)
        launcher.create_pre_update_backup()
        assert seen["label"] == "pre-update"

    def test_backup_failure_raises_update_error(self, tmp_path):
        def boom(paths, label):
            raise OSError("disk error")

        launcher = InstallerLauncher(spawn=_Spawn(), backup=boom)
        with pytest.raises(UpdateError) as ei:
            launcher.create_pre_update_backup()
        assert ei.value.retryable


class TestLaunch:
    def test_launches_with_silent_flags(self, tmp_path):
        spawn = _Spawn()
        launcher = InstallerLauncher(spawn=spawn, backup=lambda p, l: None)
        installer = _installer(tmp_path)
        cmd = launcher.launch(installer, restart=False)
        assert cmd[0] == str(installer)
        for flag in SILENT_FLAGS:
            assert flag in cmd
        assert spawn.commands == [cmd]

    def test_restart_adds_flag_when_exe_known(self, tmp_path):
        spawn = _Spawn()
        launcher = InstallerLauncher(spawn=spawn, backup=lambda p, l: None)
        cmd = launcher.launch(_installer(tmp_path), restart=True,
                              exe_path="C:/Program Files/OptionsPilot/OptionsPilot.exe")
        assert RELAUNCH_FLAG in cmd

    def test_no_restart_flag_without_exe(self, tmp_path):
        spawn = _Spawn()
        launcher = InstallerLauncher(spawn=spawn, backup=lambda p, l: None)
        cmd = launcher.launch(_installer(tmp_path), restart=True, exe_path=None)
        assert RELAUNCH_FLAG not in cmd


class TestRelaunchIsTheInstallersJob:
    """V0.12.3. The update installed correctly and the app never came back.

    Three mechanisms could have relaunched it and none did: the `[Run]` entry
    carries `skipifsilent` and every update is `/VERYSILENT`; Restart Manager
    was told `RestartApplications=no` and in any case can only restart a process
    it closed, while this app closes ITSELF; and `relaunch_app()` is called by
    nothing in production.

    The installer is now the single authority, because it is the only
    participant that knows when the file replacement finished — the app cannot
    know, since it has to be dead for the install to happen at all.
    """

    @staticmethod
    def _cmd(tmp_path, **kw):
        launcher = InstallerLauncher(spawn=_Spawn(), backup=lambda p, l: None)
        return launcher.launch(_installer(tmp_path), **kw)

    def test_the_updater_asks_for_a_relaunch(self, tmp_path):
        cmd = self._cmd(tmp_path, restart=True, exe_path="C:/x/OptionsPilot.exe")
        assert RELAUNCH_FLAG in cmd
        assert cmd.count(RELAUNCH_FLAG) == 1

    def test_restartapplications_is_never_emitted(self, tmp_path):
        """It drove Restart Manager, which cannot restart a process it did not
        close — and if RM ever did close this app, `WM_CLOSE` would land in
        `on_closing`, which cancels every close it did not sanction. That is
        the V0.12.1 hang re-entered through another door, so the flag must not
        come back on ANY branch."""
        for kwargs in ({"restart": True, "exe_path": "C:/x/OptionsPilot.exe"},
                       {"restart": True, "exe_path": None},
                       {"restart": False}):
            assert "/RESTARTAPPLICATIONS" not in self._cmd(tmp_path, **kwargs)

    def test_a_silent_install_that_did_not_ask_gets_no_relaunch(self, tmp_path):
        """`restart=False` is the caller saying "install, do not come back".
        The installer must not decide otherwise, which is exactly what the
        `Check:` on the second `[Run]` entry enforces."""
        cmd = self._cmd(tmp_path, restart=False)
        assert RELAUNCH_FLAG not in cmd
        for flag in SILENT_FLAGS:
            assert flag in cmd

    def test_a_dev_checkout_asks_for_nothing(self, tmp_path):
        """No frozen exe means the installed app is not what is running, so
        relaunching it would start a build the developer did not ask for."""
        assert RELAUNCH_FLAG not in self._cmd(tmp_path, restart=True,
                                              exe_path=None)

    def test_the_flag_is_ours_and_not_an_inno_one(self):
        """Inno ignores parameters it does not recognise, which is what lets a
        script define its own — but only if it stays spelled the way
        `RelaunchRequested` in OptionsPilot.iss compares it."""
        assert RELAUNCH_FLAG == "/RELAUNCH"
        assert RELAUNCH_FLAG not in SILENT_FLAGS


class TestTheInstallerScriptHoldsUpItsEnd:
    """The switch is one fact written in two languages, and nothing else here
    can check the second one.

    `verify.ps1` never compiles the `.iss` — Inno is not a build dependency of
    the test suite — so a rename on either side would ship a silent no-op: the
    updater passing a switch nobody reads, the app never coming back, and every
    Python test above still green. That is precisely the drift this codebase
    keeps paying for, so the two halves are asserted against each other as
    text. It proves the contract, not the behaviour; the behaviour needs a real
    build (see docs/RELEASE.md).
    """

    @staticmethod
    def _script() -> str:
        path = Path(__file__).resolve().parents[1] / "installer" / "OptionsPilot.iss"
        assert path.is_file(), f"installer script missing at {path}"
        return path.read_text(encoding="utf-8")

    def test_the_script_reads_the_switch_the_app_sends(self):
        script = self._script()
        assert "function RelaunchRequested" in script
        assert f"'{RELAUNCH_FLAG}'" in script, (
            f"OptionsPilot.iss does not compare against {RELAUNCH_FLAG} — the "
            f"updater would pass a switch nothing reads")

    def test_the_relaunch_entry_is_gated_and_runs_as_the_original_user(self):
        """Ungated it would launch after every silent install; elevated it
        would resolve {localappdata} for whoever approved UAC, which is a
        different data directory than the user's own."""
        line = next(ln for ln in self._script().splitlines()
                    if ln.startswith("Filename:") and "RelaunchRequested" in ln)
        assert "Check: RelaunchRequested" in line
        assert "runasoriginaluser" in line
        assert "nowait" in line, "Setup would block on the app it just started"
        assert "skipifsilent" not in line, (
            "the whole point of this entry is that it DOES run silently")

    def test_the_interactive_entry_is_untouched(self):
        """The Finished-page checkbox keeps `postinstall skipifsilent`: an
        interactive install must behave exactly as it did before."""
        line = next(ln for ln in self._script().splitlines()
                    if ln.startswith("Filename:") and "Description:" in ln)
        assert "postinstall" in line and "skipifsilent" in line
        assert "Check:" not in line

    def test_restart_manager_is_still_told_not_to_restart(self):
        """`CloseApplications=yes` keeps an interactive upgrade able to replace
        files; `RestartApplications=no` keeps RM out of the relaunch decision,
        which now has exactly one owner."""
        script = self._script()
        assert "RestartApplications=no" in script
        assert "CloseApplications=yes" in script

    def test_missing_installer_raises(self, tmp_path):
        launcher = InstallerLauncher(spawn=_Spawn(), backup=lambda p, l: None)
        with pytest.raises(UpdateError):
            launcher.launch(tmp_path / "missing.exe")

    def test_spawn_failure_raises_update_error(self, tmp_path):
        launcher = InstallerLauncher(spawn=_Spawn(fail=True), backup=lambda p, l: None)
        with pytest.raises(UpdateError) as ei:
            launcher.launch(_installer(tmp_path), restart=False)
        assert ei.value.retryable


class TestRelaunch:
    def test_relaunch_without_exe_is_noop(self):
        launcher = InstallerLauncher(spawn=_Spawn(), backup=lambda p, l: None)
        assert launcher.relaunch_app(exe_path=None) is False

    def test_relaunch_invokes_relaunch(self):
        calls = []
        launcher = InstallerLauncher(spawn=_Spawn(),
                                     relaunch=lambda exe: calls.append(exe),
                                     backup=lambda p, l: None)
        assert launcher.relaunch_app(exe_path="C:/x/OptionsPilot.exe") is True
        assert calls == ["C:/x/OptionsPilot.exe"]


# ---------------------------------------------------------------------------
# Authenticode verification (V0.9.0-C9-1)
# ---------------------------------------------------------------------------

#: Statuses measured from a real `WinVerifyTrust` call on Windows 11 during
#: C9-1, one per reachable outcome. Pinned as data so the mapping can be
#: asserted on the Ubuntu CI leg too — the codes are facts about Windows, not
#: about the platform the test happens to run on.
TRUSTED = 0x00000000            # embedded-signed python.exe, chain trusted
NO_SIGNATURE = 0x800B0100       # a plain unsigned PE
UNTRUSTED_ROOT = 0x800B0109     # signed with a self-signed dev certificate
BAD_DIGEST = 0x80096010         # one byte flipped after signing
SUBJECT_FORM_UNKNOWN = 0x800B0003   # not a recognised signable file at all
FILE_ERROR = 0x80092003         # the file could not be read


class TestClassifyTrustStatus:
    """The status -> verdict mapping, which is where the policy actually is.

    Kept separate from the OS call so it is asserted on every platform in the
    matrix, not only on the canonical Windows leg.
    """

    def test_success_is_trusted(self):
        assert classify_trust_status(TRUSTED) is SignatureVerdict.TRUSTED

    def test_a_plain_unsigned_file_is_unsigned_not_invalid(self):
        """The distinction C9-2 could not be built without.

        Every release before V0.9.0 is unsigned. Folding this into INVALID
        refuses all of them, and the client doing the refusing is the OLD one
        — so the fix would ship in an update nobody could install.
        """
        assert classify_trust_status(NO_SIGNATURE) is SignatureVerdict.UNSIGNED

    def test_an_unrecognised_file_form_is_unsigned_not_invalid(self):
        """Nothing located a signature — that is not a verdict ON a signature.

        Whether the download is a well-formed installer is `validate`'s
        name/size/hash question, answered with evidence this layer does not
        have.
        """
        assert classify_trust_status(SUBJECT_FORM_UNKNOWN) is SignatureVerdict.UNSIGNED

    @pytest.mark.parametrize("status", [UNTRUSTED_ROOT, BAD_DIGEST])
    def test_a_signature_that_does_not_hold_up_is_invalid(self, status):
        assert classify_trust_status(status) is SignatureVerdict.INVALID

    def test_unreadable_file_cannot_be_evaluated(self):
        """Not the same as "unsigned" — nothing was actually judged."""
        assert classify_trust_status(FILE_ERROR) is SignatureVerdict.UNKNOWN

    def test_unreachable_check_is_unknown(self):
        assert classify_trust_status(None) is SignatureVerdict.UNKNOWN

    def test_unrecognised_failure_is_still_a_failure(self):
        """An unlisted non-zero status means WinVerifyTrust refused the file.

        Defaulting an unknown refusal to anything softer would turn every
        future error code Microsoft adds into a silent bypass.
        """
        assert classify_trust_status(0xDEADBEEF) is SignatureVerdict.INVALID

    def test_the_three_ways_of_not_being_trusted_stay_apart(self):
        """The whole reason the verdict is an enum and not a bool.

        "no signature", "bad signature" and "could not look" drive three
        different decisions in `validate()`. A caller reduced to truthiness
        would treat them identically.
        """
        verdicts = {classify_trust_status(NO_SIGNATURE),
                    classify_trust_status(BAD_DIGEST),
                    classify_trust_status(None)}
        assert len(verdicts) == 3
        assert SignatureVerdict.TRUSTED not in verdicts

    def test_only_invalid_blocks_a_phase_one_install(self):
        assert SignatureVerdict.INVALID.refuses_install
        assert not SignatureVerdict.UNSIGNED.refuses_install
        assert not SignatureVerdict.UNKNOWN.refuses_install
        assert not SignatureVerdict.TRUSTED.refuses_install


class TestVerifyAuthenticode:
    """Cross-platform contract: a verdict, always, and never an exception."""

    def test_off_windows_is_unknown_not_a_negative_verdict(self, monkeypatch, tmp_path):
        """A Linux developer running the suite must not see a signature failure.

        Inability to check is not evidence about the file.
        """
        monkeypatch.setattr(sys, "platform", "linux")
        target = tmp_path / "OptionsPilot-Setup-v9.9.9.exe"
        target.write_bytes(b"MZ" + b"\0" * 64)
        assert verify_authenticode(target) is SignatureVerdict.UNKNOWN

    def test_never_raises_on_a_missing_path(self, tmp_path):
        assert isinstance(verify_authenticode(tmp_path / "nope.exe"), SignatureVerdict)

    def test_accepts_a_string_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        assert verify_authenticode(str(tmp_path / "x.exe")) is SignatureVerdict.UNKNOWN


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32",
                    reason="WinVerifyTrust is a Windows API; the mapping it "
                           "feeds is asserted platform-independently in "
                           "TestClassifyTrustStatus")
class TestVerifyAuthenticodeOnWindows:
    """Against real files, through the real OS call.

    A signed reference is obtained without touching any certificate store: the
    running interpreter is Authenticode-signed on both python.org builds and
    the CI runner's. Where it is not, these skip rather than assert something
    weaker — but the negative cases below need no signature at all and always
    run.
    """

    @staticmethod
    def _signed_reference():
        exe = Path(sys.executable)
        if installer_mod._win_verify_trust(exe) == TRUSTED:
            return exe
        return None

    def test_a_trusted_signed_binary_verifies(self):
        ref = self._signed_reference()
        if ref is None:
            pytest.skip(reason="this interpreter carries no trusted embedded "
                               "Authenticode signature, so there is no signed "
                               "reference file available without installing one")
        assert verify_authenticode(ref) is SignatureVerdict.TRUSTED

    def test_a_byte_flipped_after_signing_is_rejected(self, tmp_path):
        """The demonstration that matters: tampering is detected, and it is
        reported AS tampering rather than as "unsigned".

        This is what pins `_WTD_PROV_FLAGS = 0`. Setting WTD_SAFER_FLAG makes
        Windows return TRUST_E_NOSIGNATURE here — still a refusal, but one that
        names the wrong cause.
        """
        ref = self._signed_reference()
        if ref is None:
            pytest.skip(reason="no signed reference file to tamper with on "
                               "this machine; see the sibling test")
        target = tmp_path / "tampered.exe"
        shutil.copy(ref, target)
        blob = bytearray(target.read_bytes())
        blob[len(blob) // 2] ^= 0xFF
        target.write_bytes(bytes(blob))

        assert installer_mod._win_verify_trust(target) == BAD_DIGEST
        assert verify_authenticode(target) is SignatureVerdict.INVALID

    def test_an_unsigned_file_is_a_verdict_not_an_inability_to_check(self, tmp_path):
        """Absence of a signature is something Windows actually told us."""
        target = tmp_path / "unsigned.exe"
        target.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")
        assert verify_authenticode(target) is not SignatureVerdict.UNKNOWN

    def test_a_missing_file_cannot_be_evaluated(self, tmp_path):
        """`validate()` rejects a missing file on its own; this layer says only
        that it could not judge one."""
        assert verify_authenticode(tmp_path / "absent.exe") is SignatureVerdict.UNKNOWN
