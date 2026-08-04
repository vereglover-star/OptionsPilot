# ReleaseRollback.ps1 — undo, in reverse order, everything already done.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
#
# The contract: a release that fails before the push leaves the repository
# EXACTLY where it started. That is only achievable if every mutating step
# registers its own undo at the moment it succeeds — a rollback written as one
# block at the bottom of the script inevitably tries to undo a step that never
# ran, and the first thing it does then is fail while the caller is already in
# a bad state.
#
# So each step pushes a scriptblock here immediately after it works, and a
# failure unwinds the stack from the top. A rollback action that itself throws
# is REPORTED and the unwind continues: a release script's worst possible
# behaviour is to abandon the tree half-restored and say nothing.
#
# After the push, `Disarm-Rollback` empties the stack. Undoing a pushed branch
# or tag means rewriting published history, which this project's standing rules
# forbid and which no script should decide to do on a human's behalf.

$script:ReleaseRollbackStack = @()

function Reset-RollbackStack {
    $script:ReleaseRollbackStack = @()
}

function Register-Rollback {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    $script:ReleaseRollbackStack += [pscustomobject]@{
        Description = $Description
        Action      = $Action
    }
}

function Get-RollbackDepth {
    return @($script:ReleaseRollbackStack).Count
}

# Called once the push has succeeded: from here the release is public and the
# only correct recovery is a new commit, never an undo.
function Disarm-Rollback {
    param([string]$Because = "the release has been pushed")
    if ((Get-RollbackDepth) -gt 0) {
        Write-Note "Rollback disarmed - $Because."
    }
    $script:ReleaseRollbackStack = @()
}

function Invoke-Rollback {
    param([string]$Because)

    $stack = @($script:ReleaseRollbackStack)
    if ($stack.Count -eq 0) {
        Write-Host ""
        Write-Host "  Nothing to roll back - the repository was never modified." -ForegroundColor Yellow
        return $true
    }

    Write-Banner "ROLLING BACK ($Because)" "Yellow"

    $allOk = $true
    # Reverse order: the last thing done is the first thing undone.
    for ($i = $stack.Count - 1; $i -ge 0; $i--) {
        $entry = $stack[$i]
        Write-Doing "undo: $($entry.Description)"
        try {
            & $entry.Action
            Write-Ok "undone: $($entry.Description)"
        } catch {
            $allOk = $false
            Write-Fail "could not undo: $($entry.Description)"
            Write-Note $_.Exception.Message
        }
    }

    $script:ReleaseRollbackStack = @()

    if ($allOk) {
        Write-Host ""
        Write-Host "  Repository restored to its pre-release state." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  ROLLBACK INCOMPLETE - inspect the repository by hand before retrying." -ForegroundColor Red
        Write-Host "  Start with: git status; git log --oneline -3; git tag -l" -ForegroundColor Red
    }
    return $allOk
}
