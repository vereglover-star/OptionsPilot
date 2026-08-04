<#
.SYNOPSIS
  Build the Windows exe, safely - tests run first, always.
.DESCRIPTION
  Wraps scripts/build_exe.ps1 (the actual PyInstaller invocation, left
  untouched - it already handles the running-instance guard and data/
  backup/restore correctly) with the pre-build test gate CLAUDE.md already
  mandates: never spend 2-3 minutes on a PyInstaller build for code that
  fails its own test suite. Refuses to build on a red suite unless
  -SkipTests is passed explicitly.
.EXAMPLE
  .\scripts\build.ps1
  .\scripts\build.ps1 -SkipTests   # emergency rebuild only
#>
param([switch]$SkipTests)
. "$PSScriptRoot\_common.ps1"

# This script RUNS other programs and reports their exit codes, so a native
# process writing to stderr must not terminate it. See the "runner scripts and
# stderr" note at the top of _common.ps1 for why this line exists.
$ErrorActionPreference = "Continue"

if (-not $SkipTests) {
    Write-Step "Pre-build gate: running the full test suite"
    & "$PSScriptRoot\test.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Tests failed - build aborted. Fix the suite, or pass -SkipTests to override."
        exit 1
    }
} else {
    Write-Host "WARNING: -SkipTests passed - building without running the suite first." -ForegroundColor Yellow
}

Ensure-Environment -Extras @("dev", "ui", "build") | Out-Null

Write-Step "Building the executable (scripts/build_exe.ps1)"
& "$PSScriptRoot\build_exe.ps1"
exit $LASTEXITCODE
