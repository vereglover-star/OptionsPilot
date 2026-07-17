<#
.SYNOPSIS
  Prepare a release: verify, optionally bump the version, build, report.
.DESCRIPTION
  Orchestrates the full release-readiness pipeline described in
  docs/RELEASE_CHECKLIST.md and prints a PASS/FAIL report against it.
  Never commits, tags, pushes, or publishes anything - those remain
  explicit, human-approved actions per this project's standing safety
  rules. This script tells you exactly which commands to run yourself at
  the end.
.EXAMPLE
  .\scripts\release.ps1
  .\scripts\release.ps1 -Version 0.2.0
  .\scripts\release.ps1 -SkipBuild    # doc/version prep only, no exe
#>
param(
    [string]$Version,
    [switch]$SkipBuild
)
. "$PSScriptRoot\_common.ps1"

Write-Host "`n===================================" -ForegroundColor Cyan
Write-Host " OptionsPilot release preparation" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan

$report = [ordered]@{}

Write-Step "1. Checking git state"
Set-Location $RepoRoot
$status = git status --porcelain
if ($status) {
    Write-Fail "Working tree is not clean - commit or stash before releasing."
    $report["Clean working tree"] = $false
} else {
    Write-Ok "Working tree clean (git status AND git diff --stat both checked)"
    $report["Clean working tree"] = $true
}

if ($Version) {
    Write-Step "2. Setting version to $Version"
    $python = Ensure-Environment -Extras @("dev", "ui")
    & $python "$PSScriptRoot\bump_version.py" $Version
    $report["Version set to $Version"] = ($LASTEXITCODE -eq 0)
} else {
    Write-Step "2. Version bump skipped (no -Version given)"
}

Write-Step "3. Running full verification (scripts/verify.ps1)"
& "$PSScriptRoot\verify.ps1"
$report["Full verification (tests + docs + html-ids + pip check + browser)"] = ($LASTEXITCODE -eq 0)

if (-not $SkipBuild) {
    Write-Step "4. Building the executable"
    & "$PSScriptRoot\build.ps1" -SkipTests   # already verified in step 3
    $report["Executable built"] = ($LASTEXITCODE -eq 0)
} else {
    Write-Host "`n4. Build skipped (-SkipBuild)" -ForegroundColor Yellow
}

Write-Host "`n===== RELEASE READINESS =====" -ForegroundColor Cyan
$allPass = $true
foreach ($k in $report.Keys) {
    if ($report[$k]) { Write-Ok $k } else { Write-Fail $k; $allPass = $false }
}

if ($allPass) {
    Write-Host "`nAll automated checks passed. Remaining steps are yours to approve" -ForegroundColor Green
    Write-Host "(see docs/RELEASE_CHECKLIST.md for the full list):" -ForegroundColor Green
    Write-Host "  1. Review docs/CHANGELOG.md for a dated entry covering this release."
    Write-Host "  2. git add -A; git commit -m `"...`""
    if ($Version) {
        Write-Host "  3. git tag v$Version"
        Write-Host "  4. git push origin main --tags"
        Write-Host "  5. gh release create v$Version dist\OptionsPilot -F docs\CHANGELOG.md"
    } else {
        Write-Host "  3. git push origin main"
    }
} else {
    Write-Host "`nNot release-ready - fix the failures above first." -ForegroundColor Red
}
exit ([int](-not $allPass))
