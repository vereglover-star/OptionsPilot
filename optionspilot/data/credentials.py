"""API-key storage — the one place a secret is written down, and how it is shown.

Until V0.5.7 a key could reach the app by exactly two routes: an environment
variable, or `api_key:` in `config.yaml`. Both are fine for an operator and
useless for the person this application is actually for — someone who has just
signed up for a free Finnhub key and wants to paste it into a box. This module
is that box's backing store.

## Why a separate file, and not `settings.json`

`RuntimeSettings` already owns the live-editable preferences and would have
been one fewer file. It is deliberately not used, for one reason: **everything
in `settings.json` is treated as ordinary user data** — it is copied by
`core/migration.create_backup()`, it is small enough that a user will open it
in Notepad, and nothing about it says "this file is dangerous to share".
A secret needs the opposite defaults. `credentials.json` is written with
owner-only permissions, is excluded from every export path by construction (no
export code imports this module), and its very name tells a user what it is.

The rule this enforces is stated once, here, and depended on everywhere else:

> **A plaintext key leaves this module only through `resolve()`.** Every other
> accessor returns a mask.

## Resolution order

    environment variable   →   this store   →   config.yaml   →   missing

Environment beats everything because that is the override an operator reaches
for and the one a machine-specific deployment sets; a stored key beats
`config.yaml` because pasting a key into the app is a *later, more deliberate*
act than whatever a checked-in config file happens to say. The mechanism is
`ProviderConfig.resolve_api_key`, which already consults the environment before
its own `api_key` field — so this store simply supplies that field, and the
ordering falls out with no second implementation to keep in sync.

The consequence a user must be told about: while an environment variable is
set, a key pasted into the UI **has no effect**. Hiding that would produce the
worst possible bug report ("I typed my key in and it still says no key"), so
`key_source` is reported in the dashboard and the UI says so in words.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

from optionspilot.core.logging_setup import get_logger

log = get_logger("data")

#: Where a key can come from, most authoritative first. Reported to the UI so
#: the page can explain an environment variable shadowing a stored key.
SOURCE_ENV = "environment"
SOURCE_STORED = "stored"
SOURCE_CONFIG = "config"
SOURCE_NONE = "none"

#: How many trailing characters of a key stay visible. Four is the convention
#: every payment form and cloud console uses, and it is enough for a user to
#: confirm *which* key is installed without being enough to use it.
VISIBLE_TAIL = 4

#: Keys shorter than this are masked completely — a 6-character secret with 4
#: characters shown is not masked in any meaningful sense.
MIN_MASKABLE = 8


def mask(key: str | None) -> str:
    """`sk_live_9f3a` -> `••••••••9f3a`. Never raises, never echoes a short key.

    The return value is safe to log, render, export and screenshot. It is the
    ONLY representation of a key that any caller outside this module should
    ever hold.
    """
    # Stripped BEFORE the emptiness test, not after: a whitespace-only value is
    # an absent key everywhere else in this module (`set_key` treats it as a
    # removal, `resolve_api_key` ignores it), and returning a row of dots for
    # one would show the user a key that does not exist.
    key = str(key or "").strip()
    if not key:
        return ""
    if len(key) < MIN_MASKABLE:
        return "•" * 8
    return "•" * 8 + key[-VISIBLE_TAIL:]


class CredentialStore:
    """Per-provider API keys on disk. Thread-safe.

    Tolerant by design: an unreadable or malformed file degrades to "no keys
    stored" rather than to a failed launch. The application must start and
    serve charts with zero keys configured — that is the shipped default — so
    a broken credential file can never be more than a missing feature.
    """

    VERSION = 1

    def __init__(self, path: str | Path | None):
        #: None means "keep credentials in memory only". Used by tests and by
        #: any embedding that does not want a key touching a disk it owns.
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._providers: dict[str, dict] = {}
        self._load()

    # ── reading ──────────────────────────────────────────────────────────────

    def resolve(self, provider: str) -> str | None:
        """**The one plaintext accessor.** The stored key for `provider`, or None.

        Deliberately named so that a grep for `resolve(` finds every place a
        secret is materialised. There are two: `overlay()` below, which hands
        it to the adapter that must send it, and `MarketDataControl` when it
        applies a key change to a live adapter.
        """
        with self._lock:
            entry = self._providers.get(provider) or {}
        key = (entry.get("api_key") or "").strip()
        return key or None

    def masked(self, provider: str) -> str:
        """The stored key as `••••••••abcd`, or "" when nothing is stored."""
        return mask(self.resolve(provider))

    def configured_at(self, provider: str) -> str | None:
        """ISO-8601 timestamp of when this key was saved, or None."""
        with self._lock:
            return ((self._providers.get(provider) or {}).get("configured_at")
                    or None)

    def last_success_at(self, provider: str) -> str | None:
        """ISO-8601 timestamp of the last request this key was known to serve.

        Recorded here rather than only on the health monitor because it must
        survive a restart: "this key worked yesterday" is the single most
        useful fact when a provider starts failing today, and an in-memory
        counter forgets it every launch.
        """
        with self._lock:
            return ((self._providers.get(provider) or {}).get("last_success_at")
                    or None)

    def has_key(self, provider: str) -> bool:
        return self.resolve(provider) is not None

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(self._providers)

    def describe(self, provider: str) -> dict:
        """Everything the UI may know about a stored key. **Masked.**

        There is no `redact=False` escape hatch, unlike `ProviderConfig.
        as_dict()`. That one needs a round-trip for its own tests; this one has
        no legitimate caller that wants plaintext, and adding the parameter
        would be inviting the leak.
        """
        return {
            "stored": self.has_key(provider),
            "masked_key": self.masked(provider),
            "configured_at": self.configured_at(provider),
            "last_success_at": self.last_success_at(provider),
        }

    # ── writing ──────────────────────────────────────────────────────────────

    def set_key(self, provider: str, api_key: str) -> None:
        """Store (or replace) a provider's key.

        Whitespace is stripped: users paste from a web page and bring a
        trailing newline with them roughly half the time, and a key with an
        invisible newline fails authentication in a way nothing can explain.
        An empty result is a removal, not a key of length zero.
        """
        api_key = (api_key or "").strip()
        if not api_key:
            self.remove_key(provider)
            return
        with self._lock:
            entry = dict(self._providers.get(provider) or {})
            entry["api_key"] = api_key
            entry["configured_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            # A replaced key has not proven itself yet, so the old key's
            # success time must not be inherited — it would claim the NEW key
            # worked at a moment it did not exist.
            entry.pop("last_success_at", None)
            self._providers[provider] = entry
            self._save()
        log.info("stored an API key for market-data provider %s", provider)

    def remove_key(self, provider: str) -> bool:
        """Forget a provider's key. Returns True when there was one."""
        with self._lock:
            existed = provider in self._providers
            self._providers.pop(provider, None)
            if existed:
                self._save()
        if existed:
            log.info("removed the stored API key for market-data provider %s",
                     provider)
        return existed

    def note_success(self, provider: str) -> None:
        """Record that this key just served a request. Best-effort."""
        with self._lock:
            entry = self._providers.get(provider)
            if entry is None:
                return
            entry["last_success_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            self._save()

    # ── composition ──────────────────────────────────────────────────────────

    def overlay(self, config):
        """`config` with every stored key merged into its `ProviderConfig`.

        This is how a stored key reaches an adapter, and it is why the
        resolution order needs no second implementation: the adapter still
        calls `ProviderConfig.resolve_api_key`, which consults the environment
        *first* and only then the `api_key` field this fills in. Environment
        therefore keeps winning, for free.

        A stored key overwrites a `config.yaml` one because pasting a key into
        the application is a later and more deliberate act than a file that may
        have been checked in months ago.
        """
        for name in self.providers():
            key = self.resolve(name)
            if key:
                config = config.with_provider(name, api_key=key)
        return config

    def source_for(self, provider: str, provider_config,
                   env_vars: tuple[str, ...] = (), *,
                   environ: dict | None = None) -> str:
        """Where this provider's effective key actually comes from.

        `env_vars` is the adapter's own `api_key_env_vars` — passed in rather
        than looked up, because this module must not know which providers
        exist. The precedence checked here mirrors
        `ProviderConfig.resolve_api_key` exactly; `tests/test_credentials.py`
        asserts the two agree for every combination, so a change to one that is
        not made to the other fails the suite rather than producing a page that
        confidently names the wrong source.
        """
        env = os.environ if environ is None else environ
        names = ([provider_config.api_key_env] if provider_config.api_key_env
                 else []) + list(env_vars)
        for name in names:
            if name and (env.get(name) or "").strip():
                return SOURCE_ENV
        if self.has_key(provider):
            return SOURCE_STORED
        if (provider_config.api_key or "").strip():
            return SOURCE_CONFIG
        return SOURCE_NONE

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("credential store at %s is unreadable (%s) — starting "
                        "with no stored keys", self.path, exc)
            return
        providers = doc.get("providers") if isinstance(doc, dict) else None
        if isinstance(providers, dict):
            self._providers = {str(k): dict(v) for k, v in providers.items()
                               if isinstance(v, dict)}

    def _save(self) -> None:
        """Called with the lock held. Never raises — a key that cannot be
        persisted is still usable for this session, and a failed write must not
        take the settings page down with it."""
        if self.path is None:
            return
        doc = json.dumps({"version": self.VERSION,
                          "providers": self._providers}, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(doc, encoding="utf-8")
            _restrict(tmp)
            tmp.replace(self.path)      # atomic: never a half-written secret
            _restrict(self.path)
        except OSError as exc:
            log.error("could not persist the credential store: %s", exc)


def _restrict(path: Path) -> None:
    """Owner-only permissions, best-effort.

    On POSIX this is `chmod 600`. On Windows the file already sits under the
    user's own `%LOCALAPPDATA%`, which is not world-readable, and tightening
    the ACL further needs `icacls`/pywin32 — a dependency and a failure mode
    for a marginal gain over the directory ACL already in force. Failing to
    restrict is logged at debug and never fatal: a key that cannot be chmod'ed
    is still better stored than lost.
    """
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover — platform/filesystem dependent
        log.debug("could not restrict permissions on %s", path)


__all__ = ["CredentialStore", "mask", "SOURCE_ENV", "SOURCE_STORED",
           "SOURCE_CONFIG", "SOURCE_NONE", "VISIBLE_TAIL"]
