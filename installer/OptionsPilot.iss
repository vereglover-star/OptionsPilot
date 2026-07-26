; OptionsPilot — Inno Setup script (TEMPLATE, not yet wired into the release pipeline)
; =============================================================================
; This is groundwork for "Professional Release Pipeline 1.1". It is NOT built by
; the GitHub Actions release workflow today. When the installer step is added,
; the release job will:
;   1. build the app        -> scripts\build.ps1        (dist\OptionsPilot\)
;   2. compile this script   -> ISCC OptionsPilot.iss /DMyAppVersion=<x.y.z>
;   3. upload Output\OptionsPilot-Setup-v<x.y.z>.exe as a Release asset
;
; Compile locally with the free Inno Setup compiler (https://jrsoftware.org):
;   iscc installer\OptionsPilot.iss /DMyAppVersion=0.5.0
;
; Design decisions (see docs/RELEASE.md "Installer preparation"):
;   * Per-USER install, no admin/UAC — matches a no-elevation desktop app whose
;     data already lives under %LOCALAPPDATA%. Install dir: %LOCALAPPDATA%\Programs\OptionsPilot.
;   * User data lives in %LOCALAPPDATA%\OptionsPilot (managed by the app via
;     AppPaths). The installer NEVER writes there; the uninstaller NEVER deletes
;     it by default (opt-in checkbox below) — so upgrades and uninstall/reinstall
;     preserve the paper account, journal, coach reviews, and settings.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"   ; overridden on the command line with /DMyAppVersion=x.y.z
#endif
#define MyAppName "OptionsPilot"
#define MyAppExeName "OptionsPilot.exe"
#define MyAppPublisher "the OptionsPilot authors"

[Setup]
AppId={{4C0D3A7E-0000-4E00-9000-4F5054494C00}   ; stable GUID — keep constant across versions so upgrades replace in place
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest                        ; per-user, no admin prompt
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=Output
OutputBaseFilename=OptionsPilot-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; SignTool=...   ; (future) wire Authenticode signing of the setup + exe here

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
; Opt-in, OFF by default: only removes user data if the user explicitly asks.
Name: "removedata"; Description: "Also delete my OptionsPilot data (paper account, journal, coach, settings)"; GroupDescription: "On uninstall:"; Flags: unchecked

[Files]
; The entire PyInstaller one-dir bundle produced by scripts\build.ps1.
Source: "..\dist\OptionsPilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch OptionsPilot"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove user data ONLY if the 'removedata' task was selected. Default uninstall
; leaves %LOCALAPPDATA%\OptionsPilot untouched so reinstalling keeps everything.
Type: filesandordirs; Name: "{localappdata}\OptionsPilot"; Tasks: removedata
