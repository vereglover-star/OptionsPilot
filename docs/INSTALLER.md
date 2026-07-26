# INSTALLER.md — the Windows installer (Professional Windows Installer 1.0)

OptionsPilot ships as a professional Windows installer built with **Inno Setup 6**
from `installer/OptionsPilot.iss`. The release pipeline produces
`OptionsPilot-Setup-vX.Y.Z.exe` **alongside** the portable `OptionsPilot-vX.Y.Z.zip`
on every version tag; the zip is still published for users who prefer a
no-install extract-and-run.

## What the installer does

| Behavior | Detail |
|---|---|
| **Install location** | `C:\Program Files\OptionsPilot` by default (`{autopf}`, 64-bit). The user can pick another folder on the directory page. |
| **Elevation** | Requires admin (UAC) — Program Files is machine-wide. The app itself runs **without** admin afterwards. |
| **Runtime files** | The entire PyInstaller one-dir bundle (`OptionsPilot.exe` + `_internal\` + `config.yaml`). |
| **Start Menu** | Folder **OptionsPilot** containing **OptionsPilot** and **Uninstall OptionsPilot**. |
| **Desktop shortcut** | Optional, **checked by default** on the "Select Additional Tasks" page. |
| **Installed Apps** | Registered in Windows Settings → Installed Apps and Control Panel → Programs and Features, with publisher, version, support/publisher URLs, copyright, icon, and an estimated size (auto-computed by Inno). |
| **Icon** | `assets/optionspilot.ico` — used for the setup exe, the shortcuts, and the uninstaller. |
| **Repair / reinstall** | Running the setup again over the same version repairs/reinstalls in place. |
| **Uninstall** | Standard uninstaller; see "Uninstall" below. |

## Upgrade behavior (install over an existing version)

The installer carries a **stable `AppId` GUID** (`{{4C0D3A7E-…}`), so Windows
recognizes an existing installation and upgrades it **in place** (same folder via
`UsePreviousAppDir=yes`). Only application files under `{app}` are replaced.

**User data is never touched by an upgrade.** All user data lives in a separate
root — `%LOCALAPPDATA%\OptionsPilot` (`data/ logs/ backups/ exports/ migrations/`),
owned by the app via `core/paths.py::AppPaths` — which the installer never writes
to. Journal, coach reviews, settings, trades, watchlists, logs, and backups all
survive upgrades untouched. If OptionsPilot is running during an upgrade, Inno's
Restart Manager (`CloseApplications=yes`) closes it so its files can be replaced.

**Auto-updates (V0.5.0+).** The installed app updates itself through this same
installer: the in-app updater (`docs/AUTO_UPDATER.md`) downloads the newer
`OptionsPilot-Setup-vX.Y.Z.exe` from GitHub Releases, backs up the user's data
(`create_backup(paths, "pre-update")`), and runs it silently
(`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /RESTARTAPPLICATIONS`) —
so this script's unattended behavior is part of the update contract. Keep the
output filename pattern and the silent-install semantics stable.

## Uninstall behavior

The uninstaller removes the program files and shortcuts. At uninstall time it
asks:

> **Do you also want to remove your personal OptionsPilot data?**
> This permanently deletes your paper-trading account, journal, coach reviews,
> settings, watchlists, and backups (`%LOCALAPPDATA%\OptionsPilot`).
> Choose **No** to keep your data for a future reinstall.

The default is **No** (`MB_DEFBUTTON2`), so an accidental or scripted (silent)
uninstall never destroys data. Only an explicit **Yes** deletes
`%LOCALAPPDATA%\OptionsPilot` (via `DelTree` in the `[Code]` section). This is
why uninstall-then-reinstall preserves everything.

> Note: because the app installs machine-wide but stores data per-user, the
> uninstall prompt removes the **uninstalling user's** data (their
> `%LOCALAPPDATA%`). On a normal single-user PC that is the same person. A
> machine with several Windows users would keep each other user's data.

## Building the installer

### Locally

```powershell
# 1. install Inno Setup 6 once:  https://jrsoftware.org/isdl.php  (or: choco install innosetup -y)
# 2. build the app + compile the installer (stamps optionspilot.__version__):
.\scripts\build_installer.ps1            # reuses dist\OptionsPilot if present
.\scripts\build_installer.ps1 -Rebuild   # force a fresh app build first
# -> installer\Output\OptionsPilot-Setup-v<version>.exe
```

`scripts/build_installer.ps1` reads the single-source version
(`optionspilot.__version__`), builds `dist\OptionsPilot\` if needed, locates
`ISCC.exe`, and compiles `installer\OptionsPilot.iss` with
`/DMyAppVersion=<version>`.

### In CI (automatic on a version tag)

`.github/workflows/release.yml` builds the exe, packages the zip, installs Inno
Setup (`choco install innosetup`), runs `scripts/build_installer.ps1`, and
attaches **both** `OptionsPilot-Setup-vX.Y.Z.exe` and `OptionsPilot-vX.Y.Z.zip`
to the GitHub Release. See `docs/RELEASE.md` for the full pipeline.

## Testing

`tests/test_installer.py` statically guards the installer's load-bearing
decisions (Program Files target, admin, stable AppId, Start-Menu + uninstall
entries, desktop-icon-default-checked, app icon everywhere, uninstall-time
data prompt defaulting to No, no install-time data removal, in-place upgrade,
versioned output) and the pipeline wiring (Inno install + installer build +
both assets uploaded, zip retained).

**Must be verified manually** (no headless way to drive a Windows installer;
do this before a public release — a checklist for each is in the milestone
report):

- Fresh install → app launches; data appears under `%LOCALAPPDATA%\OptionsPilot`.
- Upgrade over a prior version → app files replaced, **data/settings/journal/coach survive**.
- Repair (reinstall same version) → app files restored, data untouched.
- Uninstall, answer **No** → data survives; reinstall → everything present.
- Uninstall, answer **Yes** → `%LOCALAPPDATA%\OptionsPilot` removed.
- Programs and Features shows correct name/publisher/version/icon and uninstalls cleanly.

## Missing / optional assets

- **Wizard images** (`WizardImageFile` 164×314, `WizardSmallImageFile` 55×58
  bitmaps) are not set — Inno's defaults are used. Add branded bitmaps under
  `installer/` and wire them in `[Setup]` for extra polish.
- **Code signing** is not configured. The setup exe and app exe are unsigned, so
  SmartScreen will warn on first run. A `SignTool` hook is stubbed in the `.iss`;
  wire it once an Authenticode certificate is available (a real prerequisite for
  a friction-free public release).
