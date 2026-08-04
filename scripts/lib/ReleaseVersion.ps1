# ReleaseVersion.ps1 — the version, the changelog, and the bridge to Python.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
#
# Every function here is a thin call into scripts/lib/release_support.py. That
# indirection is the whole point: "which files hold a copy of the version",
# "is 0.9.10 newer than 0.9.9" and "does the CHANGELOG have a section for this"
# are decisions with edge cases, and decisions with edge cases belong somewhere
# pytest can reach. PowerShell keeps the shelling-out and the printing.

$script:ReleaseSupportScript = Join-Path $PSScriptRoot "release_support.py"

# Runs a release_support.py sub-command. Native stderr is captured rather than
# redirected for the same PowerShell 5.1 reason documented in ReleaseGit.ps1:
# under $ErrorActionPreference = "Stop", a `2>&1` on a native process turns any
# stderr line into a terminating error even when the process exits 0.
function Invoke-ReleaseSupport {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$StdIn
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($PSBoundParameters.ContainsKey("StdIn")) {
            $raw = $StdIn | & $Python $script:ReleaseSupportScript @Arguments 2>&1
        } else {
            $raw = & $Python $script:ReleaseSupportScript @Arguments 2>&1
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $lines = @($raw | ForEach-Object { "$_" })
    return [pscustomobject]@{
        ExitCode = $code
        Ok       = ($code -eq 0)
        Lines    = $lines
        Text     = ($lines -join "`n").Trim()
    }
}

function Get-ProjectVersion {
    param([Parameter(Mandatory = $true)][string]$Python)
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("version")
    if (-not $result.Ok) { throw "could not read the project version: $($result.Text)" }
    return $result.Text
}

# Exit 0 means strictly newer. A re-release of the SAME version is refused
# along with a downgrade: the tag would already exist (the preflight catches
# that too), and a release whose version did not move publishes an installer
# the auto-updater will never offer to anyone.
function Test-VersionIsNewer {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Version
    )
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("compare", $Version)
    return [pscustomobject]@{ IsNewer = $result.Ok; Detail = $result.Text }
}

function Test-VersionLiteralsAgree {
    param([Parameter(Mandatory = $true)][string]$Python)
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("check")
    return [pscustomobject]@{ Ok = $result.Ok; Detail = $result.Text }
}

function Get-ChangelogHeading {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Version
    )
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("changelog-has", $Version)
    return [pscustomobject]@{ Found = $result.Ok; Detail = $result.Text }
}

# Writes the version to every literal location and reports which files changed,
# so the release commit can stage exactly those paths instead of `git add -A`.
# The distinction matters: `-A` would sweep up anything a verification run
# happened to leave in the tree, and a release commit must contain the release
# and nothing else.
function Sync-ProjectVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Version,
        [switch]$DryRun
    )
    $arguments = @("sync", $Version, "--json")
    if ($DryRun) { $arguments += "--dry-run" }
    $result = Invoke-ReleaseSupport -Python $Python -Arguments $arguments

    $report = $null
    try { $report = $result.Text | ConvertFrom-Json } catch { $report = $null }

    if ($null -eq $report) {
        throw "version sync produced no readable report: $($result.Text)"
    }
    if (@($report.errors).Count -gt 0) {
        throw ("version sync failed:`n  " + ((@($report.errors)) -join "`n  "))
    }
    return $report
}

function Get-VersionLocationReport {
    param([Parameter(Mandatory = $true)][string]$Python)
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("locations")
    return $result.Text
}
