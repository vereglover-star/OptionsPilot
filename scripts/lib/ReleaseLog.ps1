# ReleaseLog.ps1 — console output for the release pipeline.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
# Builds on scripts/_common.ps1's Write-Step / Write-Ok / Write-Fail rather
# than replacing them, so a release reads like the rest of this repo's tooling.
#
# One rule drives the whole file: a release script's output is read exactly
# twice — once while it runs, and once afterwards when something went wrong.
# The second reader needs to be able to see WHERE it stopped without counting
# lines, which is why phases are banners and every check prints a verdict even
# when it passes.

function Write-Phase {
    param([int]$Number, [int]$Total, [string]$Title)
    Write-Host ""
    Write-Host ("=" * 74) -ForegroundColor DarkCyan
    Write-Host (" PHASE {0}/{1}  {2}" -f $Number, $Total, $Title) -ForegroundColor Cyan
    Write-Host ("=" * 74) -ForegroundColor DarkCyan
}

function Write-Banner {
    param([string]$Text, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host ("=" * 74) -ForegroundColor $Color
    Write-Host "  $Text" -ForegroundColor $Color
    Write-Host ("=" * 74) -ForegroundColor $Color
    Write-Host ""
}

# A preflight verdict. `Detail` is printed under the headline so a failure
# explains itself in place rather than sending the reader to a doc.
function Write-Check {
    param([bool]$Ok, [string]$Message, [string]$Detail)
    if ($Ok) {
        Write-Host "  PASS  $Message" -ForegroundColor Green
    } else {
        Write-Host "  FAIL  $Message" -ForegroundColor Red
    }
    if ($Detail) {
        foreach ($line in ($Detail -split "`n")) {
            Write-Host "        $line" -ForegroundColor DarkGray
        }
    }
}

# The dry-run voice. Every mutation in this pipeline is written so that its
# -DryRun branch calls this instead, which makes "what would happen" a property
# of the code path rather than a separate description of it that can drift.
function Write-Would {
    param([string]$Message)
    Write-Host "  WOULD  $Message" -ForegroundColor Magenta
}

function Write-Note {
    param([string]$Message)
    Write-Host "        $Message" -ForegroundColor DarkGray
}

function Write-Caution {
    param([string]$Message)
    Write-Host "  WARN  $Message" -ForegroundColor Yellow
}

function Write-Doing {
    param([string]$Message)
    Write-Host "  ..    $Message" -ForegroundColor Gray
}
