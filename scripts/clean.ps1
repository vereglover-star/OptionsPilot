<#
.SYNOPSIS
  Remove local dev/build cache clutter - never touches data/ or logs/.
.DESCRIPTION
  Deletes __pycache__ directories, .pytest_cache, and *.egg-info. With
  -Dist, also removes the PyInstaller build/ and dist/ output folders.
  Does NOT touch data/, logs/, or .venv/ - those are either real runtime
  state or expensive to rebuild; remove them yourself if you really mean
  to (data/ in particular holds the user's real paper account).
.EXAMPLE
  .\scripts\clean.ps1
  .\scripts\clean.ps1 -Dist
#>
param([switch]$Dist)
. "$PSScriptRoot\_common.ps1"
Set-Location $RepoRoot

Write-Step "Removing __pycache__, .pytest_cache, *.egg-info"
Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.venv\\' } |
    Remove-Item -Recurse -Force
Remove-Item ".pytest_cache" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Directory -Filter "*.egg-info" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

if ($Dist) {
    Write-Step "Removing build/ and dist/ (PyInstaller output)"
    Remove-Item "build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Ok "Clean."
