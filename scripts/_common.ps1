# Shared helpers dot-sourced by the other scripts/*.ps1 entry points.
# Not meant to be run directly.
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
    if (Test-Path $lock) {
        Write-Step "Ensuring dependencies ($spec, version-locked)"
        & $venvPython -m pip install -q -e $spec -c $lock
    } else {
        Write-Step "Ensuring dependencies ($spec, UNLOCKED - requirements-lock.txt not found)"
        & $venvPython -m pip install -q -e $spec
    }
    if ($LASTEXITCODE -ne 0) { throw "pip install -e $spec failed" }
    return $venvPython
}
