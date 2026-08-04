# ReleaseGit.ps1 — every git operation a release performs, and its undo.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
#
# `release.ps1` itself contains no `git` invocation. That is a deliberate
# constraint, asserted by tests/test_release_automation.py: the orchestrator
# decides the ORDER of things and this file decides how each one is done, so
# "does a release ever push before verifying" is answerable by reading one
# short file instead of grepping a long one.
#
# ── one PowerShell 5.1 hazard, stated once ───────────────────────────────────
# scripts/_common.ps1 sets $ErrorActionPreference = "Stop". Under that setting,
# redirecting a native executable's stderr inside PowerShell 5.1 turns each
# line into a terminating NativeCommandError. git writes perfectly ordinary
# progress to stderr — `git push` writes ALL of it there — so the naive
# `git push 2>&1` would throw on a completely successful push. Both wrappers
# below therefore drop to "Continue" for the duration of the call and decide
# success from $LASTEXITCODE, which is the only honest signal git gives.

# Runs git and captures its output. Use for anything whose text is needed.
function Invoke-GitCapture {
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & git @GitArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $lines = @($raw | ForEach-Object { "$_" })
    return [pscustomobject]@{
        ExitCode = $code
        Lines    = $lines
        Text     = ($lines -join "`n").Trim()
        Ok       = ($code -eq 0)
    }
}

# Runs git with its output going straight to the console. Use for anything
# long-running or interactive (a push that may prompt for credentials).
function Invoke-GitStream {
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @GitArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return $code
}

function Assert-GitAvailable {
    $probe = Invoke-GitCapture @("--version")
    if (-not $probe.Ok) { throw "git is not available on PATH." }
    return $probe.Text
}

# ── preflight readers ────────────────────────────────────────────────────────

function Get-GitCurrentBranch {
    $result = Invoke-GitCapture @("rev-parse", "--abbrev-ref", "HEAD")
    if (-not $result.Ok) { return $null }
    return $result.Text
}

function Get-GitHeadSha {
    $result = Invoke-GitCapture @("rev-parse", "HEAD")
    if (-not $result.Ok) { return $null }
    return $result.Text
}

# CLAUDE.md's standing trap: `git status` has printed "working tree clean" in
# this repository while `git diff --stat` showed real uncommitted changes. Both
# are consulted, and either one speaking up is enough to stop a release.
function Get-GitDirtyState {
    $porcelain = Invoke-GitCapture @("status", "--porcelain")
    $diffStat  = Invoke-GitCapture @("diff", "--stat")
    $staged    = Invoke-GitCapture @("diff", "--cached", "--stat")

    $details = @()
    if ($porcelain.Text) { $details += "git status --porcelain:`n$($porcelain.Text)" }
    if ($diffStat.Text)  { $details += "git diff --stat:`n$($diffStat.Text)" }
    if ($staged.Text)    { $details += "git diff --cached --stat:`n$($staged.Text)" }

    return [pscustomobject]@{
        IsClean = ($details.Count -eq 0)
        Detail  = ($details -join "`n")
    }
}

# A merge, rebase, cherry-pick, revert or bisect left half-finished. Committing
# on top of one of these produces a commit nobody intended, and tagging it
# publishes that. Paths come from `git rev-parse --git-path` so this is correct
# inside a worktree, where .git is a file rather than a directory.
function Get-GitInProgressOperation {
    $checks = @(
        @{ Path = "MERGE_HEAD";       Name = "a merge" },
        @{ Path = "rebase-merge";     Name = "an interactive or merge rebase" },
        @{ Path = "rebase-apply";     Name = "a rebase or 'git am'" },
        @{ Path = "CHERRY_PICK_HEAD"; Name = "a cherry-pick" },
        @{ Path = "REVERT_HEAD";      Name = "a revert" },
        @{ Path = "BISECT_LOG";       Name = "a bisect" }
    )
    foreach ($check in $checks) {
        $resolved = Invoke-GitCapture @("rev-parse", "--git-path", $check.Path)
        if (-not $resolved.Ok) { continue }
        if (Test-Path -LiteralPath $resolved.Text) { return $check.Name }
    }
    return $null
}

function Test-GitLocalTag {
    param([Parameter(Mandatory = $true)][string]$Tag)
    $result = Invoke-GitCapture @("rev-parse", "-q", "--verify", "refs/tags/$Tag")
    return $result.Ok
}

# Also proves the remote is reachable and credentials resolve, which is worth
# knowing BEFORE a release spends five minutes verifying.
function Get-GitRemoteTagState {
    param(
        [Parameter(Mandatory = $true)][string]$Remote,
        [Parameter(Mandatory = $true)][string]$Tag
    )
    $result = Invoke-GitCapture @("ls-remote", "--tags", $Remote, "refs/tags/$Tag")
    return [pscustomobject]@{
        Reachable = $result.Ok
        Exists    = ($result.Ok -and $result.Text)
        Detail    = $result.Text
    }
}

function Get-GitRemoteUrl {
    param([Parameter(Mandatory = $true)][string]$Remote)
    $result = Invoke-GitCapture @("remote", "get-url", $Remote)
    if (-not $result.Ok) { return $null }
    return $result.Text
}

# Behind/ahead against the tracking branch. Releasing while behind would tag a
# tree that is not what the remote has, and the branch push would be rejected
# anyway — after the commit and tag already exist locally.
function Get-GitUpstreamState {
    $upstream = Invoke-GitCapture @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if (-not $upstream.Ok) {
        return [pscustomobject]@{ HasUpstream = $false; Behind = 0; Ahead = 0; Upstream = $null }
    }
    $counts = Invoke-GitCapture @("rev-list", "--left-right", "--count", "@{u}...HEAD")
    $behind = 0
    $ahead = 0
    if ($counts.Ok -and $counts.Text -match '^\s*(\d+)\s+(\d+)\s*$') {
        $behind = [int]$Matches[1]
        $ahead = [int]$Matches[2]
    }
    return [pscustomobject]@{
        HasUpstream = $true
        Upstream    = $upstream.Text
        Behind      = $behind
        Ahead       = $ahead
    }
}

# ── mutations ────────────────────────────────────────────────────────────────

function Restore-GitFiles {
    param([Parameter(Mandatory = $true)][string[]]$Paths)
    if (@($Paths).Count -eq 0) { return }
    # `checkout --` rather than `restore`: identical effect here and available
    # on every git this project has ever been built with.
    $result = Invoke-GitCapture (@("checkout", "--") + $Paths)
    if (-not $result.Ok) { throw "git checkout -- failed: $($result.Text)" }
}

function Add-GitPaths {
    param([Parameter(Mandatory = $true)][string[]]$Paths)
    $result = Invoke-GitCapture (@("add", "--") + $Paths)
    if (-not $result.Ok) { throw "git add failed: $($result.Text)" }
}

# Multi-line messages go to git through a FILE, never through `-m`. PowerShell
# 5.1 re-quotes native arguments on its way to CreateProcess and mangles
# embedded newlines and quotes doing it, which would land a release commit with
# a corrupted body — and a commit message is not something this project amends
# after the fact. UTF-8 without a BOM because git reads the file verbatim and a
# BOM would become the first three characters of the subject line.
function New-GitMessageFile {
    param([Parameter(Mandatory = $true)][string]$Message)
    $path = Join-Path ([IO.Path]::GetTempPath()) ("optionspilot-relmsg-" + [Guid]::NewGuid().ToString("N") + ".txt")
    [IO.File]::WriteAllText($path, $Message, (New-Object Text.UTF8Encoding($false)))
    return $path
}

function New-GitCommit {
    param([Parameter(Mandatory = $true)][string]$Message)
    $file = New-GitMessageFile -Message $Message
    try {
        # Never --no-verify: this project's hooks are a gate, not a formality.
        $result = Invoke-GitCapture @("commit", "-F", $file)
    } finally {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
    if (-not $result.Ok) { throw "git commit failed: $($result.Text)" }
    return $result.Text
}

function New-GitTag {
    param(
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $true)][string]$Message
    )
    # Annotated (-a), not lightweight: an annotated tag records who tagged and
    # when, which is the minimum provenance a published release should carry.
    # release.yml's `tags: ["v*"]` trigger fires for either.
    $file = New-GitMessageFile -Message $Message
    try {
        $result = Invoke-GitCapture @("tag", "-a", $Tag, "-F", $file)
    } finally {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
    if (-not $result.Ok) { throw "git tag failed: $($result.Text)" }
}

function Remove-GitLocalTag {
    param([Parameter(Mandatory = $true)][string]$Tag)
    $result = Invoke-GitCapture @("tag", "-d", $Tag)
    if (-not $result.Ok) { throw "git tag -d $Tag failed: $($result.Text)" }
}

# Rollback for the release commit. `--hard` is safe here and ONLY here: the
# preflight proved the tree was clean, so the only thing between $Sha and now
# is work this script itself created.
function Reset-GitToSha {
    param([Parameter(Mandatory = $true)][string]$Sha)
    $result = Invoke-GitCapture @("reset", "--hard", $Sha)
    if (-not $result.Ok) { throw "git reset --hard $Sha failed: $($result.Text)" }
}

function Push-GitBranch {
    param(
        [Parameter(Mandatory = $true)][string]$Remote,
        [Parameter(Mandatory = $true)][string]$Branch
    )
    # No --force, ever. If this is rejected the correct answer is to pull and
    # re-run, not to overwrite whatever the remote had.
    return (Invoke-GitStream @("push", $Remote, $Branch))
}

function Push-GitTag {
    param(
        [Parameter(Mandatory = $true)][string]$Remote,
        [Parameter(Mandatory = $true)][string]$Tag
    )
    return (Invoke-GitStream @("push", $Remote, "refs/tags/$Tag"))
}
