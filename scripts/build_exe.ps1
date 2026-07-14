# Build OptionsPilot.exe (one-dir bundle in dist/OptionsPilot/).
# Run from the project root:  .\scripts\build_exe.ps1
#
# The app stores its state (paper account, journal, learned weights) in
# dist\OptionsPilot\data\ next to the exe. PyInstaller --noconfirm wipes the
# whole output folder, so that data is backed up before the build and
# restored after - a rebuild must never destroy the paper account.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Refuse to build over a running instance (open SQLite handles break the wipe)
$running = Get-Process OptionsPilot -ErrorAction SilentlyContinue
if ($running) {
    throw "OptionsPilot.exe is running (PID $($running.Id -join ', ')) - close it before rebuilding."
}

# Preserve the user's app data across the rebuild
$dataDir = "dist\OptionsPilot\data"
$backup = $null
if (Test-Path $dataDir) {
    $backup = Join-Path $env:TEMP ("optionspilot-data-backup-" + (Get-Date -Format "yyyyMMddHHmmss"))
    Copy-Item $dataDir $backup -Recurse
    Write-Host "Backed up app data to $backup"
}

.\.venv\Scripts\pyinstaller --noconfirm --clean `
  --name OptionsPilot `
  --onedir `
  --add-data "optionspilot\ui\static;optionspilot\ui\static" `
  --collect-all webview `
  --collect-submodules optionspilot `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan.on `
  optionspilot_app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE - dist may be incomplete."
}
if (-not (Test-Path "dist\OptionsPilot\_internal")) {
    throw "Build output is incomplete (dist\OptionsPilot\_internal missing)."
}

Copy-Item config.yaml dist\OptionsPilot\config.yaml -Force
if ($backup) {
    Copy-Item $backup "dist\OptionsPilot\data" -Recurse -Force
    Write-Host "Restored app data from backup."
}

Write-Host "`nBuilt: dist\OptionsPilot\OptionsPilot.exe"
Write-Host "Double-click to open the desktop app; state lives in data\ beside the exe."
