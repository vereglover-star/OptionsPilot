# ReleaseGitHub.ps1 — watch the Release workflow, then report what it produced.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
#
# Deliberately does NOT require the GitHub CLI. `gh` is not installed on this
# project's own development machine, and a monitoring step that silently
# degrades to "go and look at the Actions tab yourself" on the one machine that
# cuts releases is not a monitoring step. Everything below runs on
# Invoke-WebRequest against api.github.com; `gh` is consulted for exactly one
# thing, a token, and only if it happens to be there.
#
# Reading a public repository's workflow runs needs no credentials at all. A
# token only raises the rate limit and unlocks job logs, so the absence of one
# degrades the *detail* of a failure report and never its availability.

$script:GitHubApiRoot = "https://api.github.com"

# PowerShell 5.1 negotiates SSL3/TLS1.0 by default and api.github.com has
# required TLS 1.2 since 2018. Without this every call fails with a bare
# "The request was aborted: Could not create SSL/TLS secure channel", which
# reads like a network outage rather than a protocol default.
function Initialize-GitHubTransport {
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {
        Write-Caution "could not force TLS 1.2 - GitHub API calls may fail on this host."
    }
}

# Env first (that is what CI sets), then `gh` if it is installed and logged in.
function Get-GitHubToken {
    foreach ($name in @("GH_TOKEN", "GITHUB_TOKEN")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { return [pscustomobject]@{ Token = $value; Source = "`$env:$name" } }
    }
    $gh = Get-Command gh.exe -ErrorAction SilentlyContinue
    if (-not $gh) { $gh = Get-Command gh -ErrorAction SilentlyContinue }
    if ($gh) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $token = (& $gh.Source auth token 2>$null | Select-Object -First 1)
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previous
        }
        if ($code -eq 0 -and $token) {
            return [pscustomobject]@{ Token = "$token".Trim(); Source = "gh auth token" }
        }
    }
    return [pscustomobject]@{ Token = $null; Source = "none (anonymous)" }
}

# One call. Returns a result object instead of throwing, because every caller
# here has a legitimate non-200 case: a release that does not exist yet (404),
# a rate limit worth reporting differently from an outage (403), a job log that
# needs a token (404 when anonymous).
function Invoke-GitHubApi {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Token,
        [switch]$Raw,
        [int]$TimeoutSec = 30
    )
    $uri = $Path
    if ($Path -notmatch '^https?://') { $uri = "$script:GitHubApiRoot$Path" }

    $headers = @{
        "Accept"               = if ($Raw) { "*/*" } else { "application/vnd.github+json" }
        "User-Agent"           = "OptionsPilot-release-script"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    if ($Token) { $headers["Authorization"] = "Bearer $Token" }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Stop"
    try {
        $response = Invoke-WebRequest -Uri $uri -Headers $headers -UseBasicParsing -TimeoutSec $TimeoutSec
        $data = $null
        if (-not $Raw) {
            try { $data = $response.Content | ConvertFrom-Json } catch { $data = $null }
        }
        return [pscustomobject]@{
            Ok         = $true
            StatusCode = [int]$response.StatusCode
            Data       = $data
            Content    = $response.Content
            Remaining  = $response.Headers["X-RateLimit-Remaining"]
            Error      = $null
        }
    } catch {
        $status = 0
        $remaining = $null
        $exceptionResponse = $_.Exception.Response
        if ($exceptionResponse) {
            try { $status = [int]$exceptionResponse.StatusCode } catch { $status = 0 }
            try { $remaining = $exceptionResponse.Headers["X-RateLimit-Remaining"] } catch { $remaining = $null }
        }
        return [pscustomobject]@{
            Ok         = $false
            StatusCode = $status
            Data       = $null
            Content    = $null
            Remaining  = $remaining
            Error      = $_.Exception.Message
        }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-GitHubRepoSlug {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$RemoteUrl
    )
    $result = Invoke-ReleaseSupport -Python $Python -Arguments @("repo-slug", $RemoteUrl)
    if (-not $result.Ok) { return $null }
    return $result.Text
}

# The unauthenticated API allows 60 requests an hour. A 60-minute watch polling
# every 15 seconds is 240 of them, so an anonymous run that used the configured
# interval would be rate-limited into reporting a failure that never happened.
# The interval is therefore a function of whether a token is present.
function Get-GitHubPollInterval {
    param(
        [Parameter(Mandatory = $true)][int]$Configured,
        [string]$Token
    )
    if ($Token) { return $Configured }
    return [Math]::Max($Configured, 75)
}

# ── waiting for the run ──────────────────────────────────────────────────────

# GitHub creates the run a few seconds after the tag lands, so "not found yet"
# is the normal first answer and must not be reported as "the workflow never
# started" until the appear-timeout has actually elapsed.
function Wait-GitHubWorkflowRun {
    param(
        [Parameter(Mandatory = $true)][string]$Slug,
        [Parameter(Mandatory = $true)][string]$WorkflowFile,
        [Parameter(Mandatory = $true)][string]$HeadSha,
        [string]$Token,
        [int]$AppearTimeoutSec = 300,
        [int]$PollSeconds = 15
    )
    $path = "/repos/$Slug/actions/workflows/$WorkflowFile/runs?head_sha=$HeadSha&per_page=10"
    $deadline = (Get-Date).AddSeconds($AppearTimeoutSec)
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++
        $response = Invoke-GitHubApi -Path $path -Token $Token
        if ($response.Ok -and $response.Data -and $response.Data.total_count -gt 0) {
            # Newest first: a re-run of the same sha creates a second run and
            # the one just triggered is the one being watched.
            $run = @($response.Data.workflow_runs |
                Sort-Object -Property @{Expression = { [int]$_.id }} -Descending)[0]
            Write-Ok "workflow run #$($run.run_number) created: $($run.html_url)"
            return $run
        }
        if (-not $response.Ok -and $response.StatusCode -eq 404) {
            Write-Caution "GitHub has no workflow named '$WorkflowFile' in $Slug (HTTP 404)."
            return $null
        }
        if (-not $response.Ok -and $response.StatusCode -eq 403) {
            Write-Caution "GitHub API refused the request (HTTP 403) - rate limited or private repo without a token."
            return $null
        }
        Write-Doing "waiting for GitHub to create the run (attempt $attempt)..."
        Start-Sleep -Seconds ([Math]::Min($PollSeconds, 10))
    }
    return $null
}

function Wait-GitHubRunCompletion {
    param(
        [Parameter(Mandatory = $true)][string]$Slug,
        [Parameter(Mandatory = $true)]$Run,
        [string]$Token,
        [int]$PollSeconds = 15,
        [int]$TimeoutMinutes = 60
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $current = $Run
    $lastReported = ""
    $started = Get-Date

    while ($current.status -ne "completed") {
        if ((Get-Date) -ge $deadline) {
            Write-Caution "gave up waiting after $TimeoutMinutes minutes - the run is still $($current.status)."
            return $null
        }
        Start-Sleep -Seconds $PollSeconds

        $response = Invoke-GitHubApi -Path "/repos/$Slug/actions/runs/$($current.id)" -Token $Token
        if (-not $response.Ok) {
            # A single failed poll is a network hiccup, not a failed release.
            Write-Caution "poll failed (HTTP $($response.StatusCode)); retrying."
            continue
        }
        $current = $response.Data

        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        $state = "$($current.status)"
        if ($current.conclusion) { $state = "$state/$($current.conclusion)" }
        if ($state -ne $lastReported) {
            Write-Doing ("[{0,4}s] {1}" -f $elapsed, $state)
            $lastReported = $state
        } else {
            Write-Doing ("[{0,4}s] still {1}" -f $elapsed, $state)
        }
    }
    return $current
}

function Get-GitHubRunJobs {
    param(
        [Parameter(Mandatory = $true)][string]$Slug,
        [Parameter(Mandatory = $true)][string]$RunId,
        [string]$Token
    )
    $response = Invoke-GitHubApi -Path "/repos/$Slug/actions/runs/$RunId/jobs?per_page=100" -Token $Token
    if (-not $response.Ok) { return $null }
    return $response.Data
}

# The log endpoint 302s to a plain-text blob and needs a token. Without one the
# failure report is still complete (job, step, conclusion, URL) — this only
# adds the lines that actually printed the error.
function Get-GitHubJobLogTail {
    param(
        [Parameter(Mandatory = $true)][string]$Slug,
        [Parameter(Mandatory = $true)][string]$JobId,
        [string]$Token,
        [int]$Lines = 25
    )
    if (-not $Token) { return $null }
    $response = Invoke-GitHubApi -Path "/repos/$Slug/actions/jobs/$JobId/logs" -Token $Token -Raw
    if (-not $response.Ok -or -not $response.Content) { return $null }
    $all = @($response.Content -split "`r?`n" | Where-Object { $_.Trim() })
    if ($all.Count -eq 0) { return $null }
    $tail = $all[[Math]::Max(0, $all.Count - $Lines)..($all.Count - 1)]
    return ($tail -join "`n")
}

# The Release itself is created by the workflow's last step, so it can lag the
# run's "completed" status by a second or two.
function Get-GitHubReleaseByTag {
    param(
        [Parameter(Mandatory = $true)][string]$Slug,
        [Parameter(Mandatory = $true)][string]$Tag,
        [string]$Token,
        [int]$Attempts = 6
    )
    for ($i = 1; $i -le $Attempts; $i++) {
        $response = Invoke-GitHubApi -Path "/repos/$Slug/releases/tags/$Tag" -Token $Token
        if ($response.Ok -and $response.Data) { return $response.Data }
        if ($i -lt $Attempts) { Start-Sleep -Seconds 5 }
    }
    return $null
}
