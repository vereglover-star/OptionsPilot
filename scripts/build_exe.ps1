# Build OptionsPilot.exe (one-dir bundle in dist/OptionsPilot/).
# Run from the project root:  .\scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

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

Copy-Item config.yaml dist\OptionsPilot\config.yaml -Force
Write-Host "`nBuilt: dist\OptionsPilot\OptionsPilot.exe"
Write-Host "Double-click to open the desktop app; state lives in data\ beside the exe."
