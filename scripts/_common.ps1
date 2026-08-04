# Shared helpers dot-sourced by the other scripts/*.ps1 entry points.
# Not meant to be run directly.
#
# ── RUNNER SCRIPTS AND STDERR (read this before copying "Stop" around) ───────
#
# "Stop" is right for code that computes something: an error should not be
# stepped over. It is WRONG for a script whose whole job is to run other
# programs and report their exit codes, because PowerShell 5.1 wraps every line
# a native process writes to stderr in a NativeCommandError — and under "Stop"
# that TERMINATES the caller, even when the process exited 0.
#
# It only happens when the host's stderr is redirected, which is exactly what a
# CI log, a `*> file` capture and any non-interactive runner do, and never what
# an interactive console does. That is why it went unnoticed: pip's "a new
# release of pip is available" notice killed Ensure-Environment below, and a
# StarletteDeprecationWarning from one gate script killed verify.ps1 mid-run —
# not reporting that gate as FAIL, but abandoning the six gates after it.
#
# So `test.ps1`, `verify.ps1`, `docs.ps1`, `build.ps1` and `release.ps1` each
# set $ErrorActionPreference = "Continue" and decide from $LASTEXITCODE, which
# is the only honest signal a native process gives. `throw` is unaffected —
# it is a terminating error regardless of this preference — so genuine failures
# in the helpers below still propagate.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "  OK    $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
}

# Creates .venv if it doesn't exist and installs the package editable with
# the given extras. Idempotent and fast (~1-2s) when already satisfied -
# safe to call at the top of every script instead of asking the developer
# to remember `python -m venv` + `pip install -e .[...]` themselves.
function Ensure-Environment {
    param([string[]]$Extras = @("dev"))
    Set-Location $RepoRoot
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Step "No .venv found - creating one"
        python -m venv (Join-Path $RepoRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
    }
    $spec = ".[$($Extras -join ',')]"
    # Version-locked install. This function is the ONLY dependency install on
    # the release path (release.yml -> build.ps1 -> here), so an unconstrained
    # install here means the shipped executable is built against whatever
    # resolved that day, however carefully CI is pinned. pyproject.toml still
    # decides which packages are needed; requirements-lock.txt only decides
    # which version. Missing lock = fall back rather than fail, so a fresh
    # clone that has not fetched it can still bootstrap.
    $lock = Join-Path $RepoRoot "requirements-lock.txt"

    # $ErrorActionPreference is "Stop" for this whole file, and under that
    # setting PowerShell 5.1 turns any line a native process writes to stderr
    # into a TERMINATING NativeCommandError - but only when the host's stderr is
    # redirected, which is exactly what a CI log, a `*> file` capture or any
    # non-interactive runner does. pip exits 0 and writes its "a new release of
    # pip is available" notice to stderr, so this function threw on a completely
    # successful install, and it threw for every caller: test.ps1, verify.ps1,
    # build.ps1 and release.ps1 all start here. Interactively it never
    # reproduced, which is why it survived.
    #
    # The exit code is the only honest signal a native process gives, and the
    # line below already consults it. Dropping to "Continue" for the duration of
    # the call is what makes that consultation reachable.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if (Test-Path $lock) {
            Write-Step "Ensuring dependencies ($spec, version-locked)"
            & $venvPython -m pip install -q -e $spec -c $lock
        } else {
            Write-Step "Ensuring dependencies ($spec, UNLOCKED - requirements-lock.txt not found)"
            & $venvPython -m pip install -q -e $spec
        }
        $pipExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($pipExit -ne 0) { throw "pip install -e $spec failed (exit $pipExit)" }
    return $venvPython
}
