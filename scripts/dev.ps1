<#
.SYNOPSIS
  Start a local development server in one command.
.DESCRIPTION
  Ensures .venv exists and dependencies are installed, then launches
  OptionsPilot. Defaults to browser mode with the live scan loop disabled
  (--no-loop) - the fastest iteration loop for backend/UI work, and safe
  to leave running without it trading against your real watchlist. Pass
  -Ui for the real pywebview desktop window, or -Loop to also run the
  live scan loop.

  Replaces the previously manual sequence: create a venv, activate it,
  `pip install -e .[dev,ui]`, then remember the right `python -m
  optionspilot serve --port N --no-loop` invocation.
.EXAMPLE
  .\scripts\dev.ps1
  .\scripts\dev.ps1 -Ui
  .\scripts\dev.ps1 -Port 9000 -Loop
#>
param(
    [switch]$Ui,
    [switch]$Loop,
    [int]$Port = 8787,
    [switch]$Notify   # also install the optional windows-toasts extra
)
. "$PSScriptRoot\_common.ps1"

$extras = @("dev", "ui")
if ($Notify) { $extras += "notify" }
$python = Ensure-Environment -Extras $extras

if ($Ui) {
    Write-Step "Launching the desktop window (python -m optionspilot ui)"
    & $python -m optionspilot ui
} else {
    $extraArgs = @()
    if (-not $Loop) { $extraArgs += "--no-loop" }
    Write-Step "Launching the dev server at http://127.0.0.1:$Port (Ctrl+C to stop)"
    & $python -m optionspilot serve --port $Port @extraArgs
}
