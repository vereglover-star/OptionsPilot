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
