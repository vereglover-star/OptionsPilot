<#
.SYNOPSIS
  Run every automated verification available, in one command.
.DESCRIPTION
  The pre-commit / pre-release gate: full pytest suite, static HTML
  id-reference check, documentation consistency check, a dependency
  sanity check (pip check), and - if the [browser] extra is installed - a
  real headless-browser smoke check across every tab with zero tolerance
  for console errors. Prints one aggregated PASS/FAIL report and exits
  non-zero if anything failed.

  This is what "run every automated verification available" means for
  this repo - run this script.
.EXAMPLE
  .\scripts\verify.ps1
  .\scripts\verify.ps1 -SkipBrowser
  .\scripts\verify.ps1 -RequireBrowser
#>
param(
    [switch]$SkipBrowser,
    [switch]$RequireBrowser
)
. "$PSScriptRoot\_common.ps1"

$results = [ordered]@{}

Write-Step "1/5 Tests"
& "$PSScriptRoot\test.ps1"
$results["Tests"] = ($LASTEXITCODE -eq 0)

$python = Ensure-Environment -Extras @("dev", "ui")

Write-Step "2/5 Frontend id() references"
& $python "$PSScriptRoot\check_html_ids.py"
$results["HTML id references"] = ($LASTEXITCODE -eq 0)

Write-Step "3/5 Documentation consistency"
& $python "$PSScriptRoot\check_docs.py"
$results["Docs consistency"] = ($LASTEXITCODE -eq 0)

Write-Step "4/5 Dependency check (pip check)"
& $python -m pip check
$results["pip check"] = ($LASTEXITCODE -eq 0)

# Offline by default (scripted providers, no network) so it is deterministic
# and safe here; `--live` probes the real provider chain and is a manual step.
Write-Step "5/5 Market-data stress scenarios"
& $python "$PSScriptRoot\marketdata_stress.py"
$results["Market-data stress"] = ($LASTEXITCODE -eq 0)

if (-not $SkipBrowser) {
    Write-Step "Bonus: headless-browser smoke check"
    $browserArgs = @()
    if ($RequireBrowser) { $browserArgs += "--require" }
    & $python "$PSScriptRoot\browser_check.py" @browserArgs
    $results["Browser smoke check"] = ($LASTEXITCODE -eq 0)

    Write-Step "Bonus: chart regression check"
    & $python "$PSScriptRoot\chart_check.py" @browserArgs
    $results["Chart regression check"] = ($LASTEXITCODE -eq 0)

    Write-Step "Bonus: market-data control centre check"
    & $python "$PSScriptRoot\marketdata_check.py" @browserArgs
    $results["Market-data control check"] = ($LASTEXITCODE -eq 0)

    Write-Step "Bonus: trading intelligence UI check"
    & $python "$PSScriptRoot\intelligence_check.py" @browserArgs
    $results["Trading intelligence UI check"] = ($LASTEXITCODE -eq 0)

    Write-Step "Bonus: guided onboarding & help UI check"
    & $python "$PSScriptRoot\guide_check.py" @browserArgs
    $results["Guided onboarding UI check"] = ($LASTEXITCODE -eq 0)
}

Write-Host "`n===== VERIFY SUMMARY =====" -ForegroundColor Cyan
$allPass = $true
foreach ($k in $results.Keys) {
    if ($results[$k]) { Write-Ok $k } else { Write-Fail $k; $allPass = $false }
}

if ($allPass) {
    Write-Host "`nVERIFY: PASS - safe to commit / build / release.`n" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nVERIFY: FAIL - see failures above.`n" -ForegroundColor Red
    exit 1
}
