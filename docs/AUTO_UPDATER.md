# OptionsPilot Auto-Updater

**Status:** V0.9.0 — Auto-Updater 1.2 (in-app check, download, verify, backup,
silent install, restart; **published SHA-256 checksums** enforced by the client
since C8, and **client-side Authenticode verification** since C9-2). Signing
releases is deferred by business decision, not unfinished — §5.1. Delta updates
and beta-server delivery remain designed-for and unimplemented (*Future work*).

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
| `validation.py` | Verifies a download before execution (exists, size, name, SHA-256, Authenticode). Platform-free: the signature verdict is **injected**. |
| `installer.py` | Mandatory pre-update backup → launch installer silently → restart. Also the updater's **OS boundary**: `verify_authenticode` (WinVerifyTrust) lives here because this is the one module in `update/` permitted to branch on the platform. |
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
- **Validation gate** runs before anything is launched (exists, size, name,
  SHA-256), structured as an ordered list of checks so further hardening slots
  in with **no caller changes**.
- Downloads land in a scratch temp dir, never in the install or data directories.

### 5.1 Integrity: published checksums (V0.9.0-C8)

Every release publishes a **`SHA256SUMS`** asset in standard `sha256sum`
format, generated by `.github/workflows/release.yml` from the exact files it
uploads. The updater fetches it during download, resolves the digest for the
installer it just fetched, and passes it to `validation.validate`.

Before this, the strongest available check was *the size GitHub reported*.
Size and filename confirm a download completed; they say nothing about whether
it is the file the release intended to publish.

**Assurance is reported, not assumed.** `ValidationResult.assurance` carries one
of three levels, because `ok=True` alone cannot distinguish a cryptographic
check from a length check — and before C8 both rendered as "Update verified."

| Level | Meaning | Installs? |
|---|---|---|
| `signature_verified` | Authenticode: signed by a trusted publisher (V0.9.0-C9) | Yes |
| `hash_verified` | SHA-256 matched the published digest | Yes |
| `size_only` | No manifest published; name and size only | Yes, **and the UI says so** |
| `failed` | A check failed | No |

**Why hash verification is not yet mandatory.** The client performing the check
is the *currently installed* one, and no release before V0.9.0 carries a
manifest. Hard-failing on a missing manifest would strand every existing
installation on its current version — permanently, since the fix ships in an
update they could no longer install. So:

- **Phase 1 (now).** Enforce the digest **when a manifest exists**. When none
  exists, install at `size_only` and surface *"Integrity data unavailable"*.
  Never pass silently.
- **Phase 2 (V0.9.3-C12).** Require a manifest, once every release in the
  supported window carries one.

**Absence is not always the same as absence.** A release that publishes *no*
manifest is an old release. A release that publishes a manifest which does not
cover the downloaded file — or that cannot be fetched or parsed — is a
*discrepancy*, and that **fails**. `validate(checksums_published=True)` is what
distinguishes the two.

**Authenticode: the client half is done, the publishing half is not.** A
checksum proves the file matches what the release published; a signature proves
*who* published it — and an attacker able to serve both the installer and the
manifest satisfies the first completely. Since V0.9.0-C9-2 the updater asks
Windows about the signature (`installer.verify_authenticode` → WinVerifyTrust)
and enforces the same two-phase policy: an **invalid** signature refuses the
install in both phases; an **absent** one is tolerated in Phase 1 and refused in
Phase 2; a host that **cannot check** degrades to the hash result and never
refuses on that basis alone.

**Releases are not signed, deliberately.** Publishing signed builds (C9-3) needs
a purchased certificate and OptionsPilot is not in public distribution, so it is
**deferred as a business decision, not left unfinished** — see `ROADMAP.md` ▸
Deferred for the rationale and the revisit trigger. The consequence here is
simply that every real release lands at `hash_verified`: the check runs, finds
no signature, and correctly declines to treat that as a fault. Nothing regresses
while this stays deferred, which is exactly why Phase 1 tolerates an absent
signature. Full policy matrix: `validation.validate`'s docstring.

Two constraints for whoever resumes C9-3: **`SHA256SUMS` must be generated after
signing** (signing changes the bytes, and §5.1's manifest is enforced), and
`service.REQUIRE_SIGNATURE` must stay `False` until releases are actually
signed — flipping it first makes every build uninstallable.

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

- **Authenticode code signing** of the setup + app exe in the release pipeline
  (removes SmartScreen warnings) — V0.9.0-C9-3, **deferred by business decision**
  while the app is not publicly distributed (`ROADMAP.md` ▸ Deferred). The
  *client-side* verification it feeds shipped complete in C9-2 (§5.1).
- **Delta updates** (patch instead of full installer).
- **Beta channel server** / private update endpoints for enterprise deployment
  (`transport.py` + `github_api.api_base` already parameterize the source).
- Silent/managed enterprise policies (disable auto-update via config).
