<#
.SYNOPSIS
  Package the built app into a clean, versioned release zip.
.DESCRIPTION
  Takes the PyInstaller one-dir bundle (dist\OptionsPilot\) and stages a
  distributable zip: the app folder plus LICENSE, README, and CHANGELOG.
  Deliberately EXCLUDES any local user state (data\, logs\) that a local build
  may have left beside the exe, so a release never ships a developer's paper
  account. Source, tests, and build caches are already absent from the bundle.

  The version comes from optionspilot.__version__ (the single source of truth),
  so the output is dist\OptionsPilot-v<version>.zip.
.EXAMPLE
  .\scripts\package_release.ps1
#>
param(
    [string]$DistDir = "dist\OptionsPilot",
    [string]$OutDir = "dist"
)
. "$PSScriptRoot\_common.ps1"
Set-Location $RepoRoot

if (-not (Test-Path (Join-Path $DistDir "OptionsPilot.exe"))) {
    throw "No built app at $DistDir\OptionsPilot.exe - run scripts\build.ps1 first."
}

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$version = (& $python -c "import optionspilot; print(optionspilot.__version__)").Trim()
if (-not $version) { throw "could not read optionspilot.__version__" }

Write-Step "Staging OptionsPilot v$version"
$stage = Join-Path $env:TEMP ("optionspilot-pkg-" + (Get-Date -Format yyyyMMddHHmmss))
$appDest = Join-Path $stage "OptionsPilot"
New-Item -ItemType Directory -Force -Path $appDest | Out-Null

# The application bundle...
Copy-Item (Join-Path $DistDir "*") $appDest -Recurse -Force
# ...minus any user state a local build left behind (never ship it).
foreach ($junk in @("data", "logs")) {
    $p = Join-Path $appDest $junk
    if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "  excluded $junk\ from the package" }
}

# Top-level docs alongside the app folder.
foreach ($f in @("LICENSE", "README.md")) {
    if (Test-Path $f) { Copy-Item $f $stage -Force }
    else { Write-Host "  WARN: $f not found - not included" -ForegroundColor Yellow }
}
if (Test-Path "docs\CHANGELOG.md") {
    Copy-Item "docs\CHANGELOG.md" (Join-Path $stage "CHANGELOG.md") -Force
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$zip = Join-Path $OutDir "OptionsPilot-v$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

Write-Step "Compressing to $zip"
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
Remove-Item $stage -Recurse -Force

$size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)
Write-Ok "Packaged: $zip ($size)"
Write-Output $zip
