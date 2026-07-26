"""InstallerLauncher: mandatory backup, silent flags, restart, error paths."""

from __future__ import annotations

import pytest

from optionspilot.update.installer import SILENT_FLAGS, InstallerLauncher
from optionspilot.update.models import UpdateError


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
        assert "/RESTARTAPPLICATIONS" in cmd

    def test_no_restart_flag_without_exe(self, tmp_path):
        spawn = _Spawn()
        launcher = InstallerLauncher(spawn=spawn, backup=lambda p, l: None)
        cmd = launcher.launch(_installer(tmp_path), restart=True, exe_path=None)
        assert "/RESTARTAPPLICATIONS" not in cmd

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
