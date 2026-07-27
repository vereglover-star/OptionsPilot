<#
.SYNOPSIS
  Build the Windows installer (OptionsPilot-Setup-v<version>.exe) with Inno Setup.
.DESCRIPTION
  Compiles installer\OptionsPilot.iss against the PyInstaller bundle in
  dist\OptionsPilot\, stamping the single-source version (optionspilot.__version__).
  Requires the free Inno Setup 6 compiler (ISCC.exe) — install from
  https://jrsoftware.org/isdl.php or `choco install innosetup -y`.

  Reuses an existing dist\OptionsPilot\ if present; pass -Rebuild to force a
  fresh app build first (-SkipTests skips that build's pre-test gate).
.EXAMPLE
  .\scripts\build_installer.ps1
  .\scripts\build_installer.ps1 -Rebuild
#>
param([switch]$Rebuild, [switch]$SkipTests)
. "$PSScriptRoot\_common.ps1"
Set-Location $RepoRoot

function Find-ISCC {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    throw "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6 " +
          "(https://jrsoftware.org/isdl.php or 'choco install innosetup -y'), then re-run."
}

# 1. Version — the single source of truth.
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$version = (& $python -c "import optionspilot; print(optionspilot.__version__)").Trim()
if (-not $version) { throw "could not read optionspilot.__version__" }

# 2. Application bundle — build it if missing (or if -Rebuild).
if ($Rebuild -or -not (Test-Path "dist\OptionsPilot\OptionsPilot.exe")) {
    Write-Step "Building the application bundle first"
    if ($SkipTests) { & "$PSScriptRoot\build.ps1" -SkipTests }
    else { & "$PSScriptRoot\build.ps1" }
    if ($LASTEXITCODE -ne 0) { throw "application build failed" }
} else {
    Write-Host "Using existing dist\OptionsPilot (pass -Rebuild to force a fresh build)."
}

# 3. Compile the installer.
$iscc = Find-ISCC
Write-Step "Compiling installer (v$version) with $iscc"
& $iscc "/DMyAppVersion=$version" "installer\OptionsPilot.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }

$setup = "installer\Output\OptionsPilot-Setup-v$version.exe"
if (-not (Test-Path $setup)) { throw "installer not produced at $setup" }
$size = "{0:N1} MB" -f ((Get-Item $setup).Length / 1MB)
Write-Ok "Installer: $setup ($size)"
Write-Output (Resolve-Path $setup).Path
