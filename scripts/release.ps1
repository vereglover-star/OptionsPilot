<#
.SYNOPSIS
  Release OptionsPilot in one command: verify, bump, commit, tag, push, watch.
.DESCRIPTION
  Takes a release from a clean checkout to a published GitHub Release with no
  further manual git or GitHub steps.

    1. PREFLIGHT   every safety check, before anything is modified
    2. BUMP        write the version to every location holding a literal copy
    3. VERIFY      scripts/check_docs.py, then the full scripts/verify.ps1 gate
    4. COMMIT      "Release vX.Y.Z" plus an annotated vX.Y.Z tag
    5. PUSH        branch, then tag - the tag is what triggers the build
    6. MONITOR     watch .github/workflows/release.yml to completion and report
                   the Release URL and its artifacts, or the exact failing step

  Anything that fails BEFORE the push rolls the repository back to exactly
  where it started: the version bump is reverted, the release commit is reset
  away, the tag is deleted. Nothing is committed, tagged or pushed unless every
  gate is green. After the push the rollback is disarmed, because undoing a
  published tag means rewriting published history and that is not a decision a
  script gets to make.

  Run it with -DryRun first. The dry run performs every real check against the
  real repository and the real remote, and prints exactly what it would do.
.PARAMETER Version
  The new version, as a plain X.Y.Z. Must be strictly newer than the current one.
.PARAMETER DryRun
  Validate everything and print what would happen. Modifies nothing, anywhere.
.PARAMETER Branch
  Release from this branch instead of the one configured in
  scripts/lib/ReleaseConfig.ps1. For a one-off; change the config for a move.
.PARAMETER SkipBrowser
  Passed through to verify.ps1. The browser suites need the [browser] extra.
.PARAMETER SkipVerify
  Skip the verification phase. Accepted ONLY with -DryRun, where it turns a
  ~6-minute rehearsal into a ~10-second one.
.PARAMETER NoMonitor
  Push and stop. Does not watch the GitHub workflow.
.PARAMETER AllowMissingChangelog
  Release even though docs/CHANGELOG.md has no section naming this version.
  The published notes will be the generic one-line fallback.
.PARAMETER TimeoutMinutes
  How long to watch the workflow before giving up. Giving up is not a failure
  of the release - the build continues on GitHub either way.
.EXAMPLE
  .\scripts\release.ps1 0.9.3 -DryRun
.EXAMPLE
  .\scripts\release.ps1 0.9.3
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)][string]$Version,
    [switch]$DryRun,
    [string]$Branch,
    [switch]$SkipBrowser,
    [switch]$SkipVerify,
    [switch]$NoMonitor,
    [switch]$AllowMissingChangelog,
    [int]$TimeoutMinutes
)

. "$PSScriptRoot\_common.ps1"
. "$PSScriptRoot\lib\ReleaseConfig.ps1"
. "$PSScriptRoot\lib\ReleaseLog.ps1"
. "$PSScriptRoot\lib\ReleaseRollback.ps1"
. "$PSScriptRoot\lib\ReleaseGit.ps1"
. "$PSScriptRoot\lib\ReleaseVersion.ps1"
. "$PSScriptRoot\lib\ReleaseGitHub.ps1"

# This script RUNS other programs and reports their exit codes, so a native
# process writing to stderr must not terminate it. See the "runner scripts and
# stderr" note at the top of _common.ps1 for why this line exists. `throw` is a
# terminating error regardless, so the helper libraries still propagate real
# failures - and the trap below turns those into a rollback.
$ErrorActionPreference = "Continue"

Set-Location $RepoRoot
Reset-RollbackStack

$config = Get-ReleaseConfig
if ($Branch) { $config.ReleaseBranch = $Branch }
if ($PSBoundParameters.ContainsKey("TimeoutMinutes")) { $config.RunTimeoutMinutes = $TimeoutMinutes }

$tag = "$($config.TagPrefix)$Version"
$totalPhases = 6

# Ends the release. Unwinds whatever has already been done, then exits non-zero.
# Every abort in this script goes through here so that "did it clean up after
# itself" has exactly one answer.
function Stop-Release {
    param([Parameter(Mandatory = $true)][string]$Reason)
    Invoke-Rollback -Because $Reason | Out-Null
    Write-Banner "RELEASE ABORTED - $Reason" "Red"
    Write-Host "  Nothing was pushed. No tag was published." -ForegroundColor Red
    Write-Host ""
    exit 1
}

# The helper libraries signal real failures by throwing (a bad version sync, a
# git command that exited non-zero). Without this, a throw between the bump and
# the push would unwind straight out of the script and leave the bump, the
# commit or the tag behind - the exact state the rollback exists to prevent,
# reachable by the exact mechanism the rollback was written for. Every abort
# goes through Stop-Release so there is one answer to "did it clean up".
trap {
    Write-Host ""
    Write-Fail "Unexpected error: $($_.Exception.Message)"
    Write-Note $_.ScriptStackTrace
    Stop-Release "an unexpected error interrupted the release"
}

Write-Banner "OptionsPilot release $tag$(if ($DryRun) { '  [DRY RUN - nothing will be modified]' })" $(if ($DryRun) { "Magenta" } else { "Cyan" })

if ($SkipVerify -and -not $DryRun) {
    Write-Fail "-SkipVerify is only accepted with -DryRun."
    Write-Note "A real release does not get to skip its own verification gate."
    exit 2
}

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 - PREFLIGHT
# ═════════════════════════════════════════════════════════════════════════════
#
# Every check here is read-only, so they ALL run and every failure is printed
# before the release stops. Reporting one failure at a time would turn a single
# five-minute run into four.

Write-Phase 1 $totalPhases "Preflight - safety checks (nothing is modified)"

$failures = @()
function Add-Failure { param([string]$Message) ; $script:failures += $Message }

# -- the environment ---------------------------------------------------------
$python = Ensure-Environment -Extras @("dev", "ui")

try {
    $gitVersion = Assert-GitAvailable
    Write-Check $true "git is available" $gitVersion
} catch {
    Write-Check $false "git is available" $_.Exception.Message
    Add-Failure "git is not available"
    Stop-Release "git is not available"
}

# -- the requested version ---------------------------------------------------
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Check $false "version '$Version' is a plain X.Y.Z" `
        "Pre-release suffixes are refused deliberately: the tag, the zip name, the installer's AppVersion and the auto-updater would each need a decision about them."
    Add-Failure "version '$Version' is not a plain X.Y.Z"
} else {
    Write-Check $true "version '$Version' is a plain X.Y.Z"
}

# -- the working tree --------------------------------------------------------
$dirty = Get-GitDirtyState
if ($dirty.IsClean) {
    Write-Check $true "working tree is clean (status, diff and staged index all checked)"
} else {
    Write-Check $false "working tree is clean" $dirty.Detail
    Add-Failure "the working tree has uncommitted changes"
}

# -- no half-finished git operation -----------------------------------------
$inProgress = Get-GitInProgressOperation
if ($inProgress) {
    Write-Check $false "no git operation in progress" `
        "$inProgress is in progress. Finish or abort it before releasing."
    Add-Failure "$inProgress is in progress"
} else {
    Write-Check $true "no git operation in progress"
}

# -- the release branch ------------------------------------------------------
$currentBranch = Get-GitCurrentBranch
if ($currentBranch -eq "HEAD") {
    Write-Check $false "on the release branch '$($config.ReleaseBranch)'" `
        "HEAD is detached. Check out the release branch first."
    Add-Failure "HEAD is detached"
} elseif ($currentBranch -ne $config.ReleaseBranch) {
    Write-Check $false "on the release branch '$($config.ReleaseBranch)'" `
        "Currently on '$currentBranch'. Either check out '$($config.ReleaseBranch)', pass -Branch $currentBranch for a one-off, or change ReleaseBranch in scripts/lib/ReleaseConfig.ps1."
    Add-Failure "on branch '$currentBranch', not the release branch '$($config.ReleaseBranch)'"
} else {
    Write-Check $true "on the release branch '$($config.ReleaseBranch)'"
}

# -- in sync with the remote -------------------------------------------------
$upstream = Get-GitUpstreamState
if (-not $upstream.HasUpstream) {
    Write-Check $false "branch tracks a remote branch" `
        "No upstream configured. Run: git push -u $($config.Remote) $currentBranch"
    Add-Failure "the branch has no upstream"
} elseif ($upstream.Behind -gt 0) {
    Write-Check $false "up to date with $($upstream.Upstream)" `
        "$($upstream.Behind) commit(s) behind. Releasing would tag a tree the remote does not have, and the push would be rejected after the commit and tag already existed locally. Pull first."
    Add-Failure "the branch is $($upstream.Behind) commit(s) behind $($upstream.Upstream)"
} else {
    Write-Check $true "up to date with $($upstream.Upstream) ($($upstream.Ahead) ahead, 0 behind)"
}

# -- the version moves forward ----------------------------------------------
$currentVersion = Get-ProjectVersion -Python $python
$newer = Test-VersionIsNewer -Python $python -Version $Version
if ($newer.IsNewer) {
    Write-Check $true "$Version is newer than the current $currentVersion"
} else {
    Write-Check $false "$Version is newer than the current $currentVersion" $newer.Detail
    Add-Failure "$Version is not newer than the current version $currentVersion"
}

# -- the version is not already half-bumped ---------------------------------
$literals = Test-VersionLiteralsAgree -Python $python
if ($literals.Ok) {
    Write-Check $true "every existing version literal agrees with $currentVersion"
} else {
    Write-Check $false "every existing version literal agrees with $currentVersion" $literals.Detail
    Add-Failure "the current version has drifted between the files that state it"
}

# -- the tag is free, locally and remotely -----------------------------------
if (Test-GitLocalTag -Tag $tag) {
    Write-Check $false "tag $tag does not exist locally" `
        "Delete it first if it is a leftover: git tag -d $tag"
    Add-Failure "tag $tag already exists locally"
} else {
    Write-Check $true "tag $tag does not exist locally"
}

$remoteTag = Get-GitRemoteTagState -Remote $config.Remote -Tag $tag
if (-not $remoteTag.Reachable) {
    Write-Check $false "remote '$($config.Remote)' is reachable" $remoteTag.Detail
    Add-Failure "cannot reach remote '$($config.Remote)'"
} elseif ($remoteTag.Exists) {
    Write-Check $false "tag $tag does not exist on '$($config.Remote)'" `
        "$($remoteTag.Detail)`nThat version is already released. Pick the next one."
    Add-Failure "tag $tag already exists on '$($config.Remote)'"
} else {
    Write-Check $true "tag $tag does not exist on '$($config.Remote)' (and the remote is reachable)"
}

# -- the changelog -----------------------------------------------------------
$changelog = Get-ChangelogHeading -Python $python -Version $Version
if ($changelog.Found) {
    Write-Check $true "docs/CHANGELOG.md has a section for $Version" $changelog.Detail
} elseif ($AllowMissingChangelog) {
    Write-Caution "docs/CHANGELOG.md has no section naming $Version - continuing because -AllowMissingChangelog was passed."
    Write-Note "The published release notes will be the generic one-line fallback."
} else {
    Write-Check $false "docs/CHANGELOG.md has a section for $Version" `
        "scripts/release_notes.py picks the GitHub Release body from the first '## ' heading naming the version. Without one the release publishes a generic stub.`nAdd a dated '## <date> - v${Version}: ...' section and commit it, or pass -AllowMissingChangelog."
    Add-Failure "docs/CHANGELOG.md has no section for $Version"
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "  $($failures.Count) preflight check(s) failed:" -ForegroundColor Red
    foreach ($failure in $failures) { Write-Host "    - $failure" -ForegroundColor Red }
    Stop-Release "preflight failed"
}
Write-Host ""
Write-Ok "Preflight passed - the release may proceed."

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 - VERSION BUMP
# ═════════════════════════════════════════════════════════════════════════════

Write-Phase 2 $totalPhases "Version bump - $currentVersion -> $Version"

$sync = Sync-ProjectVersion -Python $python -Version $Version -DryRun:$DryRun
$changedFiles = @($sync.changed)

if ($DryRun) {
    foreach ($file in $changedFiles) { Write-Would "write $Version to $file" }
    foreach ($file in @($sync.unchanged)) { Write-Note "already ${Version}: $file" }
} else {
    foreach ($file in $changedFiles) { Write-Ok "wrote $Version to $file" }
    if ($changedFiles.Count -gt 0) {
        # Plain scriptblocks over $script: state, deliberately NOT closures.
        # .GetNewClosure() would bind these blocks into a fresh dynamic module,
        # and a dynamic module does not see functions that were dot-sourced into
        # this script's scope - Restore-GitFiles would be "not recognized" at the
        # exact moment a rollback needs it, which is the worst possible time to
        # discover a scoping subtlety.
        $script:BumpedFiles = $changedFiles
        Register-Rollback -Description "revert the version bump in $($changedFiles -join ', ')" -Action {
            Restore-GitFiles -Paths $script:BumpedFiles
        }
    }
}

Write-Host ""
Write-Note "Every other consumer derives the version and needs no edit:"
foreach ($derived in @($sync.derived)) {
    Write-Note "  $($derived.what)"
    Write-Note "      $($derived.how)"
}

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 - VERIFICATION
# ═════════════════════════════════════════════════════════════════════════════

Write-Phase 3 $totalPhases "Verification - the gates that decide whether this ships"

if ($SkipVerify) {
    Write-Caution "verification skipped (-SkipVerify, dry run only)."
    Write-Note "A real release cannot take this path."
} else {
    # check_docs.py first, on its own. It is the gate the version bump is most
    # likely to have broken (it is the check that reads the version out of the
    # documentation), and it answers in seconds where verify.ps1 takes minutes.
    Write-Step "Documentation consistency (scripts/check_docs.py)"
    & $python "$PSScriptRoot\check_docs.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "check_docs.py failed."
        Stop-Release "documentation consistency check failed"
    }
    Write-Ok "check_docs.py passed"

    Write-Step "Full verification (scripts/verify.ps1)"
    $verifyArgs = @()
    if ($SkipBrowser) { $verifyArgs += "-SkipBrowser" }
    & "$PSScriptRoot\verify.ps1" @verifyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "verify.ps1 failed."
        Stop-Release "verification failed"
    }
    Write-Ok "verify.ps1 passed"
}

# Verification runs the test suite, a headless browser and a market-data
# simulation; if any of that left a tracked file modified, the release commit
# would silently carry it. Reported rather than fatal - the commit below stages
# named paths only, so the stray change stays out of the release either way.
if (-not $DryRun) {
    $status = Invoke-GitCapture @("status", "--porcelain")
    $unexpected = @($status.Lines |
        Where-Object { $_.Trim() } |
        Where-Object {
            # Porcelain v1: two status characters, a space, then the path.
            $path = ($_ -replace '^.{3}', '').Trim()
            -not ($changedFiles -contains $path)
        })
    if (@($unexpected).Count -gt 0) {
        Write-Caution "verification left changes in files the release did not bump:"
        foreach ($line in $unexpected) { Write-Note $line }
        Write-Note "These will NOT be included - the release commit stages named paths only."
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 - COMMIT AND TAG
# ═════════════════════════════════════════════════════════════════════════════

Write-Phase 4 $totalPhases "Commit and tag"

$commitMessage = @"
Release $tag

Version $currentVersion -> $Version, written to every location holding a
literal copy ($($changedFiles -join ', ')). Every other consumer - pyproject's
dynamic metadata, the in-app version display, the installer's AppVersion, the
portable zip's filename, the git tag and the release notes - derives it from
optionspilot.__version__ and needed no edit.

Gated on scripts/check_docs.py and the full scripts/verify.ps1 suite, both
green against this tree before this commit existed.

Created by scripts/release.ps1.
"@

$tagMessage = "OptionsPilot $tag"

if ($DryRun) {
    Write-Would "git add -- $($changedFiles -join ' ')"
    Write-Would "git commit -F <message file>, with subject: Release $tag"
    Write-Would "git tag -a $tag -F <message file>  (annotated, `"$tagMessage`")"
    Write-Host ""
    Write-Note "Commit message that would be used:"
    foreach ($line in ($commitMessage -split "`n")) { Write-Note "  | $line" }
} else {
    $preReleaseSha = Get-GitHeadSha

    if ($changedFiles.Count -eq 0) {
        Write-Fail "the version bump changed no files, so there is nothing to release."
        Stop-Release "nothing to commit"
    }

    Add-GitPaths -Paths $changedFiles
    New-GitCommit -Message $commitMessage | Out-Null
    $releaseSha = Get-GitHeadSha
    Write-Ok "committed $($releaseSha.Substring(0, 8)) - Release $tag"

    $script:PreReleaseSha = $preReleaseSha
    Register-Rollback -Description "reset the release commit (back to $($preReleaseSha.Substring(0, 8)))" -Action {
        Reset-GitToSha -Sha $script:PreReleaseSha
    }

    New-GitTag -Tag $tag -Message $tagMessage
    Write-Ok "tagged $tag (annotated) at $($releaseSha.Substring(0, 8))"

    $script:ReleaseTagName = $tag
    Register-Rollback -Description "delete the local tag $tag" -Action {
        Remove-GitLocalTag -Tag $script:ReleaseTagName
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5 - PUSH
# ═════════════════════════════════════════════════════════════════════════════

Write-Phase 5 $totalPhases "Push - the point of no return"

if ($DryRun) {
    Write-Would "git push $($config.Remote) $($config.ReleaseBranch)"
    Write-Would "git push $($config.Remote) refs/tags/$tag   <- this is what triggers the release build"
    $releaseSha = "0000000000000000000000000000000000000000"
} else {
    Write-Caution "Everything from here is public. Rollback stops being automatic."
    Write-Doing "pushing $($config.ReleaseBranch) to $($config.Remote)"

    $pushCode = Push-GitBranch -Remote $config.Remote -Branch $config.ReleaseBranch
    if ($pushCode -ne 0) {
        Write-Fail "git push of the branch failed (exit $pushCode) - nothing was published."
        Stop-Release "the branch push was rejected"
    }
    Write-Ok "pushed $($config.ReleaseBranch) to $($config.Remote)"

    # The commit is public now. Undoing it would mean a force-push, which this
    # project forbids and which a script must never choose on someone's behalf.
    Disarm-Rollback -Because "the release commit is now on $($config.Remote)"

    Write-Doing "pushing tag $tag to $($config.Remote)"
    $tagPushCode = Push-GitTag -Remote $config.Remote -Tag $tag
    if ($tagPushCode -ne 0) {
        Write-Banner "TAG PUSH FAILED" "Red"
        Write-Host "  The release commit IS pushed; the tag is not, so no build was triggered." -ForegroundColor Red
        Write-Host "  The tag still exists locally. Retry just the push:" -ForegroundColor Yellow
        Write-Host "      git push $($config.Remote) refs/tags/$tag" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
    Write-Ok "pushed tag $tag - the release build is now triggered"
}

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 6 - MONITOR THE GITHUB RELEASE BUILD
# ═════════════════════════════════════════════════════════════════════════════

Write-Phase 6 $totalPhases "GitHub release build"

$remoteUrl = Get-GitRemoteUrl -Remote $config.Remote
$slug = Get-GitHubRepoSlug -Python $python -RemoteUrl $remoteUrl
$actionsUrl = "https://github.com/$slug/actions/workflows/$($config.WorkflowFile)"
$releasesUrl = "https://github.com/$slug/releases/tag/$tag"

if (-not $slug) {
    Write-Caution "could not read owner/repo from '$remoteUrl' - skipping monitoring."
    Write-Note "Watch the build yourself under the repository's Actions tab."
} elseif ($NoMonitor) {
    Write-Note "-NoMonitor: not watching the build."
    Write-Note "Workflow: $actionsUrl"
    Write-Note "Release:  $releasesUrl"
} elseif ($DryRun) {
    Write-Would "watch $($config.WorkflowFile) in $slug for the commit the tag points at"
    Write-Would "poll until it completes, then print the Release URL and its artifacts"
    Write-Note "Workflow would be watched at: $actionsUrl"
    Write-Note "Release would appear at:      $releasesUrl"
} else {
    Initialize-GitHubTransport
    $auth = Get-GitHubToken
    Write-Note "GitHub API auth: $($auth.Source)"

    $pollSeconds = Get-GitHubPollInterval -Configured $config.RunPollSeconds -Token $auth.Token
    if ($pollSeconds -ne $config.RunPollSeconds) {
        Write-Note "Polling every ${pollSeconds}s (anonymous API allows 60 requests/hour). Set `$env:GH_TOKEN for faster polling and job logs."
    }

    Write-Doing "waiting for GitHub to create the workflow run..."
    $run = Wait-GitHubWorkflowRun -Slug $slug -WorkflowFile $config.WorkflowFile `
        -HeadSha $releaseSha -Python $python -Token $auth.Token `
        -AppearTimeoutSec $config.RunAppearTimeoutSec -PollSeconds $pollSeconds

    if (-not $run) {
        Write-Banner "COULD NOT FIND THE WORKFLOW RUN" "Yellow"
        Write-Host "  The tag was pushed successfully; only the monitoring failed." -ForegroundColor Yellow
        Write-Host "  Workflow: $actionsUrl" -ForegroundColor Yellow
        Write-Host "  Release:  $releasesUrl" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    $final = Wait-GitHubRunCompletion -Slug $slug -Run $run -Token $auth.Token `
        -PollSeconds $pollSeconds -TimeoutMinutes $config.RunTimeoutMinutes

    if (-not $final) {
        Write-Banner "STOPPED WATCHING - THE BUILD IS STILL RUNNING" "Yellow"
        Write-Host "  This is a timeout in the watcher, not a failed release." -ForegroundColor Yellow
        Write-Host "  Run:     $($run.html_url)" -ForegroundColor Yellow
        Write-Host "  Release: $releasesUrl" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    $jobs = Get-GitHubRunJobs -Slug $slug -RunId $final.id -Token $auth.Token

    $payload = @{ run = $final; jobs = $jobs } | ConvertTo-Json -Depth 12 -Compress
    $summaryResult = Invoke-ReleaseSupport -Python $python -Arguments @("summarize-run") -StdIn $payload
    $summary = $null
    try { $summary = $summaryResult.Text | ConvertFrom-Json } catch { $summary = $null }

    if (-not $summary) {
        Write-Caution "could not summarise the run; falling back to raw fields."
        $summary = [pscustomobject]@{
            ok = ($final.conclusion -eq "success")
            conclusion = $final.conclusion
            run_url = $final.html_url
            reason = "conclusion: $($final.conclusion)"
            failed_job = $null; failed_step = $null; failed_step_number = $null
        }
    }

    if ($summary.ok) {
        $release = Get-GitHubReleaseByTag -Slug $slug -Tag $tag -Token $auth.Token
        Write-Banner "RELEASE $tag PUBLISHED" "Green"

        if ($release) {
            $releaseResult = Invoke-ReleaseSupport -Python $python `
                -Arguments @("summarize-release") -StdIn (($release | ConvertTo-Json -Depth 12 -Compress))
            $info = $null
            try { $info = $releaseResult.Text | ConvertFrom-Json } catch { $info = $null }

            Write-Host "  Release URL   $($release.html_url)" -ForegroundColor Green
            Write-Host "  Workflow URL  $($final.html_url)" -ForegroundColor Green
            if ($release.draft) { Write-Caution "the release is a DRAFT - the auto-updater always skips drafts." }
            if ($release.prerelease) { Write-Caution "the release is a PRERELEASE - only beta-channel users are offered it." }
            Write-Host ""
            Write-Host "  Artifacts:" -ForegroundColor Green
            if ($info -and @($info.assets).Count -gt 0) {
                foreach ($asset in @($info.assets)) {
                    Write-Host ("    {0,-42} {1,8} MB" -f $asset.name, $asset.size_mb) -ForegroundColor Green
                }
            } else {
                Write-Caution "the release has no attached assets - check the workflow's upload step."
            }
        } else {
            Write-Host "  Workflow URL  $($final.html_url)" -ForegroundColor Green
            Write-Caution "the workflow succeeded but no Release could be read for $tag yet."
            Write-Note "Check $releasesUrl in a moment."
        }
        Write-Host ""
        Write-Host "  Nothing further to do. The release is live." -ForegroundColor Green
        Write-Host ""
        exit 0
    }

    # ---- failure report -----------------------------------------------------
    $logTail = $null
    if ($summary.failed_job_id) {
        $logTail = Get-GitHubJobLogTail -Slug $slug -JobId $summary.failed_job_id -Token $auth.Token
    }

    Write-Banner "RELEASE BUILD FAILED" "Red"
    Write-Host "  Conclusion    $($summary.conclusion)" -ForegroundColor Red
    if ($summary.failed_job) {
        Write-Host "  Failed job    $($summary.failed_job)" -ForegroundColor Red
    }
    if ($summary.failed_step) {
        Write-Host "  Failed step   #$($summary.failed_step_number) $($summary.failed_step)" -ForegroundColor Red
    }
    Write-Host "  Reason        $($summary.reason)" -ForegroundColor Red
    Write-Host "  Workflow URL  $($summary.run_url)" -ForegroundColor Red
    if ($summary.failed_job_url) {
        Write-Host "  Job URL       $($summary.failed_job_url)" -ForegroundColor Red
    }

    if ($logTail) {
        Write-Host ""
        Write-Host "  Tail of the failed job's log:" -ForegroundColor DarkGray
        foreach ($line in ($logTail -split "`n")) { Write-Host "    $line" -ForegroundColor DarkGray }
    } elseif (-not $auth.Token) {
        Write-Host ""
        Write-Note "Set `$env:GH_TOKEN to have the failing job's log tail printed here too."
    }

    Write-Host ""
    Write-Host "  The tag $tag IS pushed. Fix the cause, then re-run the build by" -ForegroundColor Yellow
    Write-Host "  moving the tag (this rewrites a tag, so only do it while the release" -ForegroundColor Yellow
    Write-Host "  is still failing and nobody has consumed it):" -ForegroundColor Yellow
    Write-Host "      git push --delete $($config.Remote) $tag" -ForegroundColor Yellow
    Write-Host "      git tag -d $tag" -ForegroundColor Yellow
    Write-Host "      .\scripts\release.ps1 $Version        # after committing the fix" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────

if ($DryRun) {
    Write-Banner "DRY RUN COMPLETE - nothing was modified" "Magenta"
    Write-Host "  Every check above ran against the real repository and the real remote." -ForegroundColor Magenta
    Write-Host "  Re-run without -DryRun to release $tag for real." -ForegroundColor Magenta
    Write-Host ""
    exit 0
}

Write-Banner "PUSHED $tag" "Green"
Write-Host "  Workflow: $actionsUrl" -ForegroundColor Green
Write-Host "  Release:  $releasesUrl" -ForegroundColor Green
Write-Host ""
exit 0
