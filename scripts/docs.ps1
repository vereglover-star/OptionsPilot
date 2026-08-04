<#
.SYNOPSIS
  Verify documentation consistency (cross-references, test counts, version sync).
.DESCRIPTION
  Runs scripts/check_docs.py: confirms every docs/*.md cross-reference
  actually exists, that "current state" docs' claimed test counts match a
  live pytest collection, and that pyproject.toml's version agrees with
  optionspilot/__init__.py. Requires the dev environment (for the live
  pytest count); ensures it if missing.
.EXAMPLE
  .\scripts\docs.ps1
#>
. "$PSScriptRoot\_common.ps1"

# This script RUNS other programs and reports their exit codes, so a native
# process writing to stderr must not terminate it. See the "runner scripts and
# stderr" note at the top of _common.ps1 for why this line exists.
$ErrorActionPreference = "Continue"
$python = Ensure-Environment -Extras @("dev", "ui")

Write-Step "Checking documentation consistency"
& $python "$PSScriptRoot\check_docs.py"
$exit = $LASTEXITCODE

if ($exit -eq 0) {
    Write-Ok "DOCS: PASS"
} else {
    Write-Fail "DOCS: FAIL"
}
exit $exit
