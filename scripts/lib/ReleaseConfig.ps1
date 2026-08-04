# ReleaseConfig.ps1 — the one place a release's fixed choices are written down.
#
# Dot-sourced by scripts/release.ps1. Not meant to be run directly.
#
# Everything here is a DECISION, not a tuning knob. Change a value in this file
# rather than passing a flag every time; the flags on release.ps1 exist for the
# one-off case (a hotfix cut from a different branch), not for the normal one.

function Get-ReleaseConfig {
    @{
        # ── the release branch ────────────────────────────────────────────────
        #
        # THIS IS THE VALUE MOST LIKELY TO NEED CHANGING. It is `V3-ui` because
        # that is where releases are actually cut today: `git ls-remote --tags`
        # shows v0.9.2 pointing at 6919bbc, which is the head of V3-ui, and
        # V3-ui is 57 commits ahead of origin/main. Setting this to `main`
        # because main is the repository's default branch would make the very
        # first run of this script abort on a branch check that was wrong.
        #
        # Set it to "main" the moment V3-ui merges. Override for a single run
        # with `-Branch <name>`.
        ReleaseBranch = "V3-ui"

        # The remote a release is pushed to and monitored on.
        Remote        = "origin"

        # `v0.9.3`. The tag prefix is not free-form: .github/workflows/release.yml
        # triggers on `tags: ["v*"]` and its guard compares "v$__version__" to
        # the pushed tag, so changing this silently stops publishing releases.
        TagPrefix     = "v"

        # ── the workflow this script watches ──────────────────────────────────
        #
        # Matched by FILENAME against the GitHub Actions API rather than by
        # display name, because a workflow's `name:` is prose someone will
        # eventually reword and the filename is what the API keys on.
        WorkflowFile  = "release.yml"

        # How long to wait for the run to APPEAR after the tag is pushed.
        # GitHub usually creates it within seconds; a few minutes of patience
        # costs nothing and a false "the workflow never started" costs a
        # confused re-tag.
        RunAppearTimeoutSec = 300

        # Seconds between polls once the run exists. The release build is a
        # multi-minute PyInstaller job plus an Inno Setup compile, so polling
        # faster only spends the unauthenticated API's 60-requests-per-hour
        # budget without learning anything sooner.
        RunPollSeconds      = 15

        # Total patience for the run itself. The observed build is well under
        # this; the ceiling exists so an abandoned runner cannot leave this
        # script polling forever.
        RunTimeoutMinutes   = 60
    }
}
