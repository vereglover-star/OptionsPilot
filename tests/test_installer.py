"""Static guards for the Windows installer config and its release-pipeline
wiring. The actual ISCC compile and the fresh-install / upgrade / repair /
uninstall runs are manual + CI (documented in docs/INSTALLER.md); these tests
lock the installer's load-bearing decisions against regression."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "OptionsPilot.iss").read_text(encoding="utf-8")
REL = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")


class TestInstallLocationAndRegistration:
    def test_installs_to_program_files(self):
        assert r"DefaultDirName={autopf}\{#MyAppName}" in ISS

    def test_requires_admin(self):
        assert "PrivilegesRequired=admin" in ISS

    def test_64bit_install_mode(self):
        assert "ArchitecturesInstallIn64BitMode=x64compatible" in ISS

    def test_stable_appid(self):
        # The GUID must never change — it is how Windows recognizes and upgrades
        # an existing installation in place.
        assert "AppId={{4C0D3A7E-0000-4E00-9000-4F5054494C00}" in ISS

    def test_registers_with_programs_and_features(self):
        for key in ("UninstallDisplayName=", "UninstallDisplayIcon=",
                    "AppPublisher=", "AppSupportURL=", "AppPublisherURL=",
                    "AppCopyright=", "AppVersion="):
            assert key in ISS, key


class TestShortcutsAndIcons:
    def test_start_menu_app_and_uninstall(self):
        assert r"{group}\{#MyAppName}" in ISS
        assert r"{group}\Uninstall {#MyAppName}" in ISS
        assert "{uninstallexe}" in ISS

    def test_desktop_icon_checked_by_default(self):
        line = next(l for l in ISS.splitlines() if 'Name: "desktopicon"' in l)
        assert "unchecked" not in line   # no 'unchecked' flag => checked by default

    def test_setup_uses_app_icon(self):
        assert r"SetupIconFile=..\assets\optionspilot.ico" in ISS
        assert (ROOT / "assets" / "optionspilot.ico").exists()

    def test_uninstall_shortcut_uses_app_icon(self):
        assert r'IconFilename: "{app}\{#MyAppExeName}"' in ISS


class TestDataSafety:
    def test_uninstall_prompt_defaults_to_no(self):
        assert "CurUninstallStepChanged" in ISS
        assert "MB_DEFBUTTON2" in ISS          # default button = No
        assert "DelTree" in ISS
        assert r"{localappdata}\{#MyAppName}" in ISS

    def test_no_install_time_data_removal(self):
        # Data removal is an uninstall-time decision, never an install task.
        assert "[UninstallDelete]" not in ISS
        assert "removedata" not in ISS

    def test_installer_only_writes_app_files(self):
        files_section = ISS.split("[Files]", 1)[1].split("[", 1)[0]
        assert "localappdata" not in files_section.lower()
        assert r"..\dist\OptionsPilot\*" in files_section


class TestUpgrade:
    def test_upgrade_uses_previous_dir(self):
        assert "UsePreviousAppDir=yes" in ISS

    def test_closes_running_app_for_upgrade(self):
        assert "CloseApplications=yes" in ISS


class TestVersioningAndOutput:
    def test_versioned_output_filename(self):
        assert "OutputBaseFilename=OptionsPilot-Setup-v{#MyAppVersion}" in ISS

    def test_version_overridable_on_command_line(self):
        assert "#ifndef MyAppVersion" in ISS
        assert "{#MyAppVersion}" in ISS

    def test_build_script_stamps_single_source_version(self):
        assert "optionspilot.__version__" in BUILD
        assert "/DMyAppVersion=$version" in BUILD
        assert r"installer\OptionsPilot.iss" in BUILD


class TestPipelineWiring:
    def test_release_installs_inno_and_builds_installer(self):
        assert "innosetup" in REL
        assert "build_installer.ps1" in REL

    def test_release_uploads_installer_and_still_ships_zip(self):
        assert "OptionsPilot-Setup-v*.exe" in REL
        assert "OptionsPilot-v*.zip" in REL   # the portable zip is not removed
