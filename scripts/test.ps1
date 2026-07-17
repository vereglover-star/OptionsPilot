<#
.SYNOPSIS
  Run the pytest suite with an unambiguous PASS/FAIL result.
.DESCRIPTION
  Ensures the environment is ready, then runs pytest. The final line is
  always an explicit "TESTS: PASS"/"TESTS: FAIL" derived from pytest's own
  exit code - not from parsing its printed summary line, which terminal
  output capture in this environment has previously swallowed silently
  (see CLAUDE.md "Known traps"). Any extra arguments pass straight through
  to pytest (a single file, -k EXPR, -x, ...).
.EXAMPLE
  .\scripts\test.ps1
  .\scripts\test.ps1 tests\test_risk.py
  .\scripts\test.ps1 -k manual_entry
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)
. "$PSScriptRoot\_common.ps1"

$python = Ensure-Environment -Extras @("dev", "ui")

Write-Step "Running pytest $($PytestArgs -join ' ')"
& $python -m pytest @PytestArgs
$exit = $LASTEXITCODE

if ($exit -eq 0) {
    Write-Ok "TESTS: PASS"
} else {
    Write-Fail "TESTS: FAIL (pytest exit code $exit)"
}
exit $exit
