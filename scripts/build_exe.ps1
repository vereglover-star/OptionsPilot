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

# --windowed: no console window — this is a real desktop app. CLI commands
# still work headlessly (OptionsPilot.exe scan) but print nothing; use
# `python -m optionspilot` from the repo for CLI output.
#
# --collect-all yfinance: yfinance is imported lazily via
# importlib.import_module (data/yfinance_provider.py), which PyInstaller's
# static import scan cannot see. Without this flag the build succeeds but the
# packaged app has no market data provider at all — every candle/quote/chain
# request dies with "No module named 'yfinance'" at runtime. The bundle check
# below and tests/test_packaging.py both guard this.
.\.venv\Scripts\pyinstaller --noconfirm --clean `
  --name OptionsPilot `
  --onedir `
  --windowed `
  --icon "assets\optionspilot.ico" `
  --add-data "optionspilot\ui\static;optionspilot\ui\static" `
  --add-data "optionspilot\data_assets;optionspilot\data_assets" `
  --collect-all webview `
  --collect-all yfinance `
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
# Modules the app loads dynamically are invisible to PyInstaller's analysis;
# a build can succeed while silently dropping them, producing an exe that only
# fails at runtime on the first data request. Prove the bundle is complete by
# running the built exe's offline selftest (it forces every lazy import).
$st = Start-Process "dist\OptionsPilot\OptionsPilot.exe" -ArgumentList "selftest" `
    -Wait -PassThru -WindowStyle Hidden
if ($st.ExitCode -ne 0) {
    throw "Packaged selftest failed (exit $($st.ExitCode)) - a lazily-imported module is missing from the bundle. Check the --collect-all flags above."
}
Write-Host "Packaged selftest: PASS (lazy imports present in the bundle)"

Copy-Item config.yaml dist\OptionsPilot\config.yaml -Force
if ($backup) {
    Copy-Item $backup "dist\OptionsPilot\data" -Recurse -Force
    Write-Host "Restored app data from backup."
}

Write-Host "`nBuilt: dist\OptionsPilot\OptionsPilot.exe"
Write-Host "Double-click to open the desktop app; state lives in data\ beside the exe."
