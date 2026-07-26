# OptionsPilot Auto-Updater

**Status:** V0.5.0 — Auto-Updater 1.0 (in-app check, download, verify, backup,
silent install, restart). Code signing and delta/beta-server delivery are
designed-for but not yet implemented (see *Future work*).

The updater makes OptionsPilot self-updating like a modern desktop app: it
quietly checks GitHub Releases on launch, and when a newer version exists it
offers a professional dialog that downloads the installer, backs up the user's
data, and installs + restarts — without the user ever visiting GitHub, handling
a ZIP, or risking their journal/paper account.

It is built on the storage architecture from V0.4.4: **user data lives in
`%LOCALAPPDATA%\OptionsPilot`, separate from the install directory**, so the
installer only ever replaces Program Files and the updater can never lose data.

---

## 1. Architecture

The updater is a self-contained subpackage, `optionspilot/update/`, that depends
only on `core` (paths, `migration.create_backup`, logging) and the standard
library — **no new runtime dependency** (networking is `urllib`). Every layer is
injectable, so the whole thing is tested offline with fakes: no sockets, no real
installer execution.

| Module | Responsibility |
|---|---|
| `version.py` | Semantic-version parse + **correct (non-lexical) ordering** (`0.4.9 < 0.4.10 < 0.5.0 < 1.0.0`). |
| `models.py` | Immutable value objects (`ReleaseInfo`, `UpdateCheckResult`, `DownloadProgress`, `UpdatePhase`, `UpdateError`). |
| `transport.py` | The **only** networking code: `urllib` opener + timeouts + retries/backoff + proxy support. Everything else takes an injected `opener`. |
| `github_api.py` | GitHub Releases → `ReleaseInfo`; selects **only** the `OptionsPilot-Setup-vX.Y.Z.exe` asset. |
| `checker.py` | "Is there a newer release?" decision (channel, frequency helpers). **Never raises.** |
| `downloader.py` | Streams the installer to `%TEMP%\OptionsPilotUpdater`, progress + cancellation, atomic `.part`→final. |
| `validation.py` | Verifies a download before execution (exists, size, name; hash + Authenticode-ready). |
| `installer.py` | Mandatory pre-update backup → launch installer silently → restart. |
| `ui.py` | Pure presentation helpers (sizes, ETA, safe markdown→HTML for release notes, dialog payloads). |
| `service.py` | `UpdateService` — the app-facing facade + thread-safe state machine tying it all together. |

The FastAPI layer (`ui/server.py`) exposes `UpdateService` over `/api/update/*`;
the static frontend (`ui/static/index.html`) renders the Settings panel, the
Help ▸ Check for Updates menu, and the update dialog. The desktop shell
(`ui/desktop.py`) registers an install hook so the window closes cleanly to let
the installer replace the running exe.

### State machine (`UpdatePhase`)

```
idle → checking → up_to_date
                → available → downloading → downloaded → verifying
                                                       → installing → (restart)
   (any) → error   (offline / download fail / validation fail / backup fail)
```

---

## 2. Flow

```
App launch (desktop, run_loop=True)
   │
   ├─ UIServer builds UpdateService(current_version, RuntimeSettings)
   │
   └─ maybe_check_on_launch()  ── background daemon thread ──┐
         (only if auto_check ON and due per frequency)       │
                                                             ▼
                          GitHubReleases.latest_release(channel)
                                   │ (urllib + retries; offline = quiet)
                                   ▼
                     Version.parse(tag) > current_version ?
                        │no                         │yes + has installer asset
                        ▼                           ▼
                   phase=up_to_date          phase=available
                   (silent, no UI)                 │
                                                   ▼
                                 Frontend polls /api/update/status,
                                 opens the dialog (once) if not skipped
                                                   │
                    ┌───────────── "Update Now" ───┘
                    ▼
        POST /api/update/download → Downloader streams to %TEMP%
                    │  (progress: bytes / speed / ETA; Cancel supported)
                    ▼
             phase=downloaded
                    │  "Install & Restart"
                    ▼
        POST /api/update/apply:
             1. validate(file, expected_size)          ── fail → error, abort
             2. create_backup(paths, "pre-update")     ── fail → error, abort
             3. InstallerLauncher.launch(/VERYSILENT …)
             4. install hook: close window / release single-instance lock
                    │
                    ▼
        Inno Setup upgrades C:\Program Files\OptionsPilot ONLY
        (%LOCALAPPDATA%\OptionsPilot untouched) → /RESTARTAPPLICATIONS relaunches
```

---

## 3. User experience

- **Launch:** a background check runs; if you're current, **nothing happens**.
- **Update available:** a dialog shows current → latest version, release date,
  download size, estimated time, and rendered release notes, with **Update Now /
  Remind Me Later / Skip This Version**.
- **Downloading:** progress bar with downloaded/total MB, speed, and ETA; a
  **Cancel** button that leaves no partial file behind.
- **Install:** one click backs up your data, installs silently, and restarts.
- **Settings ▸ Software updates:** toggle automatic checks, choose frequency
  (Every launch / Daily / Weekly), opt into beta (pre-release) updates, see
  current vs. latest version and when it last checked, and **Check now**.
- **Help ▸ Check for Updates…:** always-available manual check. If current:
  *"You're already using the latest version."*

Preferences persist in `settings.json` under the `updates` key (via
`RuntimeSettings`), so they survive restarts and upgrades.

---

## 4. Failure recovery

Every failure is converted to a clean, presentable message — **no stack traces,
no Python exceptions reach the user**, and startup is never blocked.

| Failure | Behavior |
|---|---|
| GitHub unreachable / offline | Check fails silently; app runs normally. Manual check shows a friendly "couldn't reach the update server." |
| Rate-limited / transient (5xx, timeout) | Bounded retries with exponential backoff, then quiet failure. |
| Download interrupted | Clean error in the dialog; **Retry** re-downloads. No partial file remains (`.part` is discarded). |
| User cancels download | Stops promptly; returns to the "available" state; no file left behind. |
| Disk full | Explicit "Not enough free disk space" message. |
| Validation fails (size/hash) | Install is **aborted**; nothing is executed. |
| Backup fails | Install is **aborted** before launching the installer; the user is told and can retry. |
| Installer fails to start | Presentable error; the app keeps running (data already backed up). |

---

## 5. Security

- **Only the configured repository** (`vereglover-star/OptionsPilot`) is trusted;
  the repo is fixed in code and overridable only for tests.
- **Only a recognized installer asset** (`OptionsPilot-Setup-vX.Y.Z.exe`) is ever
  downloaded — source ZIPs and any other/look-alike asset are ignored by
  construction. The updater never executes an arbitrary download.
- **Validation gate** runs before anything is launched (exists, size, name), and
  is structured as an ordered list of checks so future hardening slots in with
  **no caller changes**:
  - **SHA-256** — pass `expected_sha256` (from a published checksums asset) and
    it is enforced today.
  - **Authenticode** — add a `signtool verify` / WinVerifyTrust check to
    `validation.validate`; every caller already treats a failed result as
    "do not install."
- Downloads land in a scratch temp dir, never in the install or data directories.

---

## 6. Networking

`transport.py` centralizes policy: conservative timeouts (10s), bounded retries
with exponential backoff on *transient* failures only (permanent 4xx are not
retried), offline tolerance (a connection error is an expected outcome, not a
crash), a descriptive User-Agent (GitHub requires one), and proxy compatibility
(urllib honors `HTTP(S)_PROXY`). An optional GitHub token can be supplied to
raise the anonymous rate limit; none is required.

---

## 7. Testing

Fully offline and deterministic — CI never touches the network or runs an
installer. `tests/update_helpers.py` provides an in-memory transport (`FakeOpener`
/ `FakeResponse`) and release-JSON builders. Coverage:

- `test_update_version.py` — parsing + ordering (incl. the non-lexical cases).
- `test_update_github.py` — asset selection, channel filtering, retries.
- `test_update_checker.py` — availability, frequency throttling, offline-quiet.
- `test_update_downloader.py` — streaming, progress, cancellation, atomic finalize.
- `test_update_validation.py` — size/hash/name/missing/empty.
- `test_update_installer.py` — silent flags, restart, mandatory backup, error paths.
- `test_update_service.py` — full check→download→verify→backup→install flow.
- `test_update_endpoints.py` — `/api/update/*` via `TestClient`, fake-wired.

### Manual QA (real Windows, before a public release)

Automated tests can't drive a real Inno Setup upgrade. Verify once on a real box
or via a tagged CI release:

1. Install an older build, generate journal/paper data.
2. Publish a newer tagged release (installer asset attached).
3. Launch → the dialog appears → **Update Now** → download → install → restart.
4. Confirm the new version runs and **all data survived** (journal, paper
   account, coach history, settings, watchlists, backups).
5. Confirm a `pre-update` backup exists under `%LOCALAPPDATA%\OptionsPilot\backups`.

---

## 8. Future work

- **Authenticode code signing** of the setup + app exe (removes SmartScreen
  warnings) and signature verification in `validation.py`.
- **SHA-256 checksums asset** published with releases, enforced by the validator.
- **Delta updates** (patch instead of full installer).
- **Beta channel server** / private update endpoints for enterprise deployment
  (`transport.py` + `github_api.api_base` already parameterize the source).
- Silent/managed enterprise policies (disable auto-update via config).
