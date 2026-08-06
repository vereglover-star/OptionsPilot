; OptionsPilot — Inno Setup installer script (Professional Windows Installer 1.0)
; =============================================================================
; Produces a professional Windows setup: installs to C:\Program Files\OptionsPilot
; (all users, elevated), registers with Windows "Installed Apps" / Programs and
; Features, creates Start Menu + (optional) Desktop shortcuts, and supports
; repair, in-place upgrade, and uninstall.
;
; Built by the release pipeline (see .github/workflows/release.yml ->
; scripts/build_installer.ps1):
;   1. build the app  -> scripts\build.ps1                 (dist\OptionsPilot\)
;   2. compile         -> ISCC installer\OptionsPilot.iss /DMyAppVersion=<x.y.z>
;   3. output          -> installer\Output\OptionsPilot-Setup-v<x.y.z>.exe
; Compile locally the same way (free Inno Setup 6 compiler, https://jrsoftware.org):
;   iscc installer\OptionsPilot.iss /DMyAppVersion=0.5.0     (or run scripts\build_installer.ps1)
;
; DATA SAFETY — the load-bearing design decision:
;   User data lives in %LOCALAPPDATA%\OptionsPilot (managed by the app via
;   AppPaths), which is SEPARATE from the install directory. The installer only
;   ever writes to {app} (Program Files\OptionsPilot). Therefore:
;     * Upgrades / reinstalls replace only application files — journal, coach,
;       settings, trades, watchlists, logs, and backups are never touched.
;     * Uninstall leaves that data intact UNLESS the user explicitly opts in at
;       uninstall time (the prompt in [Code] below, defaulting to NO).

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"   ; overridden on the command line with /DMyAppVersion=x.y.z
#endif
#define MyAppName "OptionsPilot"
#define MyAppExeName "OptionsPilot.exe"
#define MyAppPublisher "the OptionsPilot authors"
#define MyAppURL "https://github.com/vereglover-star/OptionsPilot"
#define MyAppSupportURL "https://github.com/vereglover-star/OptionsPilot/issues"
#define MyAppCopyright "Copyright (C) 2026 the OptionsPilot authors"

[Setup]
; A stable AppId is what lets Windows recognize an existing installation and
; upgrade it in place (same GUID across versions). NEVER change this GUID.
AppId={{4C0D3A7E-0000-4E00-9000-4F5054494C00}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppSupportURL}
AppUpdatesURL={#MyAppURL}/releases
AppCopyright={#MyAppCopyright}

; Install into Program Files (64-bit), all users. The directory page is shown so
; a user may choose another location. Elevation is required for Program Files.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

; Programs and Features / Installed Apps registration + branding.
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\optionspilot.ico
WizardStyle=modern

; Close a running OptionsPilot (via Restart Manager) so an upgrade can replace
; its files. Restart Manager must NOT restart it: RM only restarts what it
; closed, and on the update path the app closes itself before Setup ever scans
; (optionspilot\update\installer.py). Relaunching is done by the [Run] entry
; below instead, gated on our own /RELAUNCH switch.
CloseApplications=yes
RestartApplications=no

OutputDir=Output
OutputBaseFilename=OptionsPilot-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
; SignTool=signtool   ; (future) Authenticode-sign the setup + exe once a cert exists

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Checked by default (Step 6). The user can uncheck it on the "Select Additional
; Tasks" page.
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; The entire PyInstaller one-dir bundle produced by scripts\build.ps1. This is
; ONLY application files — user data lives elsewhere (see the header).
Source: "..\dist\OptionsPilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Start Menu folder "OptionsPilot" with the app and its uninstaller (Step 5).
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\{#MyAppExeName}"
; Optional desktop shortcut (Step 6).
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Interactive install: the "Launch OptionsPilot" checkbox on the Finished page.
; `skipifsilent` keeps it off the silent path entirely — which is why an update
; never relaunched anything before v0.12.3.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; Update install: relaunch only when the updater asked for it by passing
; /RELAUNCH. `runasoriginaluser` is load-bearing, not tidiness — Setup runs
; elevated (PrivilegesRequired=admin), and without it the app would inherit
; admin and AppPaths would resolve {localappdata} for whichever account
; approved UAC, i.e. a DIFFERENT data directory than the user's own.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[Code]
{ True when /RELAUNCH was passed on the command line. Inno ignores parameters
  it does not recognise, which is what lets a script define its own; the app
  passes this one from InstallerLauncher.launch (RELAUNCH_FLAG). }
function RelaunchRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ Uninstall-time prompt (Step 4). Ask whether to also remove personal data,
  defaulting to NO so an accidental uninstall never destroys the paper account,
  journal, coach reviews, settings, watchlists, or backups. In silent uninstall
  the default (keep data) is used automatically. }
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#MyAppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Do you also want to remove your personal OptionsPilot data?' + #13#10#13#10 +
                'This permanently deletes your paper-trading account, journal, coach ' +
                'reviews, settings, watchlists, and backups:' + #13#10 +
                DataDir + #13#10#13#10 +
                'Choose No to keep your data for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
