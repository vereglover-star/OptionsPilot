"""Synchronization boundaries — what would have to happen before any of this
could exist on two devices.

**Nothing here syncs anything.** V0.7.0 deliberately builds no cloud sync, no
account system and no replication. What it builds is the thing that has to exist
first and which is far harder to add later: a complete, classified inventory of
every durable object the application owns, with a declared policy for each.

The reason to do this now rather than when sync is built: the expensive part of
synchronization is never the transport, it is discovering — usually in
production, usually as data loss — that two objects you assumed were independent
share a key, or that a file you replicated for convenience contained a secret,
or that "last write wins" was silently applied to an append-only log. Every one
of those is a classification question, and every one of them is answerable
today, from a codebase that currently has exactly one device and therefore
cannot yet be wrong.

The classification asks two things about each object:

  `SyncDomain`  What KIND of user fact is this? The domains are the ones the
                V0.7.0 charter named, and they are the granularity at which a
                user would ever say "sync this, not that".

  `SyncPolicy`  What would a second writer mean? This is the load-bearing
                field. `NEVER` is not a weaker `DEVICE_ONLY` — it means moving
                this object off the machine is a defect regardless of transport,
                and `credentials.json` is the reason the distinction exists.

`tests/test_sync_boundaries.py` asserts that every file `AppPaths` can name is
classified here, so a new store cannot be added without someone deciding what it
means on a second device.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from optionspilot.services.contracts import SyncRevision, SyncSnapshot


class SyncDomain(enum.Enum):
    """The kind of user fact an object holds."""

    #: Regenerable, machine-specific, or meaningless elsewhere. Never worth
    #: moving even if moving it were free.
    DEVICE_ONLY = "device_only"
    #: Where the user was looking. The V0.7.0 WorkspaceService domain.
    WORKSPACE = "workspace"
    #: How the user wants the app to behave. Watchlist, modes, update prefs.
    PREFERENCES = "preferences"
    #: The trade record. The system of record for everything downstream.
    JOURNAL = "journal"
    #: The paper account itself: cash, positions, order book.
    ACCOUNT = "account"
    #: What has paid off — learned scorer weights.
    LEARNING = "learning"
    #: The AI's long-term memory and the coach's reviews.
    AI_HISTORY = "ai_history"
    #: Which providers, in what order, enabled or not.
    PROVIDER_CONFIG = "provider_config"
    #: Secrets. Exactly one member, deliberately its own domain.
    CREDENTIALS = "credentials"
    #: Targets the user set and badges they earned.
    GOALS = "goals"


class SyncPolicy(enum.Enum):
    """What a second writer would mean for this object."""

    #: Must not leave the device under any transport. Not a privacy preference —
    #: a defect if violated.
    NEVER = "never"
    #: Could be moved, but there is no reason to: it is regenerable, or it
    #: describes this machine rather than this user.
    DEVICE_ONLY = "device_only"
    #: Small, whole-object, single-user. Last write wins on server receipt time
    #: is correct and CRDTs would be complexity without a user.
    LAST_WRITE_WINS = "last_write_wins"
    #: Append-only history. Two devices produce disjoint records that union
    #: cleanly; an overwrite would destroy the other device's trades.
    APPEND_ONLY = "append_only"
    #: Only one writer may exist at a time, arbitrated by whoever hosts. Not a
    #: merge strategy — a mutual-exclusion requirement.
    SINGLE_WRITER = "single_writer"


class SyncProvider(Protocol):
    def snapshot(self, domains: list[str] | None = None) -> SyncSnapshot: ...


class LocalSyncProvider:
    """Read-only local provider used until a cloud/device transport exists."""

    name = "local"

    def snapshot(self, domains: list[str] | None = None) -> SyncSnapshot:
        wanted = set(domains or [d.value for d in SyncDomain])
        now = datetime.now(timezone.utc).isoformat()
        revisions = {
            d.value: SyncRevision(d.value, 0, now)
            for d in SyncDomain if d.value in wanted and by_domain(d)
        }
        return SyncSnapshot(revisions, provider=self.name)


@dataclass(frozen=True, slots=True)
class PersistedObject:
    """One durable thing the application owns."""

    #: Path relative to the storage root, as `AppPaths` lays it out.
    path: str
    domain: SyncDomain
    policy: SyncPolicy
    #: What it holds, in one sentence.
    holds: str
    #: Why this policy and not the obvious alternative. Present on every entry
    #: where the obvious alternative is wrong, which is most of them.
    rationale: str = ""
    #: True where the object is written by more than one subsystem — the
    #: entries most likely to break first under any replication scheme.
    shared_writer: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "domain": self.domain.value,
                "policy": self.policy.value, "holds": self.holds,
                "rationale": self.rationale, "shared_writer": self.shared_writer}


#: The complete inventory. Ordered by domain for reading, not by importance.
INVENTORY: tuple[PersistedObject, ...] = (
    # ── account ──────────────────────────────────────────────────────────────
    PersistedObject(
        "data/paper.db", SyncDomain.ACCOUNT, SyncPolicy.SINGLE_WRITER,
        "the paper account: cash, equity, open positions",
        "Two processes holding one paper account is the corruption the "
        "single-instance lock already exists to prevent on one machine; a "
        "second DEVICE would be the same bug over a network. Whoever hosts is "
        "the only writer — which is the whole reason the mobile design is "
        "desktop-as-host rather than peer-to-peer.",
    ),
    PersistedObject(
        "data/orders.db", SyncDomain.ACCOUNT, SyncPolicy.SINGLE_WRITER,
        "working and historical manual orders",
        "An order book with two writers can fill the same order twice. This is "
        "the object idempotency keys protect on the wire; the store itself "
        "still needs exactly one writer behind them.",
    ),
    PersistedObject(
        "data/state/open_trades.json", SyncDomain.ACCOUNT, SyncPolicy.SINGLE_WRITER,
        "per-trade journal context for positions still open",
        "In-flight state belonging to whichever process is running the cycle "
        "loop. Meaningless without the positions in paper.db, so it moves with "
        "them or not at all.",
    ),
    PersistedObject(
        "data/state/manual_trades.json", SyncDomain.ACCOUNT, SyncPolicy.SINGLE_WRITER,
        "entry timestamps for manual positions awaiting a coach review",
    ),
    # ── journal ──────────────────────────────────────────────────────────────
    PersistedObject(
        "data/journal.db", SyncDomain.JOURNAL, SyncPolicy.APPEND_ONLY,
        "every completed round trip — the system of record",
        "Trades are written once and never edited. Two devices trading the same "
        "account cannot happen (see paper.db), but a device restored from "
        "backup can hold records another does not, and the union is the correct "
        "answer. Last-write-wins on this file would silently delete history.",
    ),
    # ── AI history ───────────────────────────────────────────────────────────
    PersistedObject(
        "data/experience.db", SyncDomain.AI_HISTORY, SyncPolicy.APPEND_ONLY,
        "the Experience Engine's rich per-trade context",
        "Written alongside the journal, one row per trade, never edited. Same "
        "union semantics for the same reason.",
    ),
    PersistedObject(
        "data/coach/*.json", SyncDomain.AI_HISTORY, SyncPolicy.APPEND_ONLY,
        "one process review per manually-traded round trip",
        "Write-once per trade id — which is exactly why `_coach_dashboard` can "
        "cache on review COUNT. Keyed by trade id, so a union cannot collide.",
    ),
    # ── learning ─────────────────────────────────────────────────────────────
    PersistedObject(
        "data/learning/weights.json", SyncDomain.LEARNING, SyncPolicy.LAST_WRITE_WINS,
        "learned scorer weight overrides and their version",
        "Derived entirely from the journal, so the worst case of a bad merge is "
        "a stale derivation that the next learning run corrects — unlike the "
        "journal itself, where a bad merge is permanent loss.",
    ),
    # ── goals ────────────────────────────────────────────────────────────────
    PersistedObject(
        "data/intelligence/goals.json", SyncDomain.GOALS, SyncPolicy.LAST_WRITE_WINS,
        "user-set trading goals (achievements are DERIVED, not stored)",
        "Small, whole-object, edited on one screen at a time. Worth noting the "
        "parenthetical: achievements look like state and are not — they are "
        "recomputed from the fact set on every snapshot, so there is nothing to "
        "sync and nothing that can drift.",
    ),
    # ── preferences ──────────────────────────────────────────────────────────
    PersistedObject(
        "data/settings.json", SyncDomain.PREFERENCES, SyncPolicy.LAST_WRITE_WINS,
        "watchlist, pinned, favorites, trading/operating mode, custom knobs, "
        "update prefs, guide progress, and the V0.7.0 workspace document",
        "One file, several domains, and that is the entry most likely to cause "
        "trouble: a phone writing its workspace must not clobber the desktop's "
        "trading mode. This is why WorkspaceService merges a PARTIAL patch "
        "rather than accepting a whole document — the split is enforced at the "
        "key level today so the file can be split for real later.",
        shared_writer=True,
    ),
    # ── workspace ────────────────────────────────────────────────────────────
    PersistedObject(
        "data/settings.json#workspace", SyncDomain.WORKSPACE,
        SyncPolicy.LAST_WRITE_WINS,
        "selected tab, symbol, timeframe, indicators, sidebar, recent symbols "
        "and saved layouts",
        "Listed SEPARATELY from `data/settings.json` even though it lives "
        "inside it. The self-audit that added this entry found the WORKSPACE "
        "domain empty — every workspace fact was folded into a PREFERENCES row, "
        "so `report()` omitted the domain entirely and the inventory looked "
        "complete while being silent about the one domain V0.7.0 built. A "
        "domain with no entries is not evidence that nothing is in it.",
        shared_writer=True,
    ),
    # ── provider configuration ───────────────────────────────────────────────
    PersistedObject(
        "data/marketdata.json", SyncDomain.PROVIDER_CONFIG, SyncPolicy.LAST_WRITE_WINS,
        "provider order, enable flags and ordering mode",
        "A preference in shape, but note it references providers by name only — "
        "no key material — which is what makes it syncable at all.",
    ),
    # ── credentials ──────────────────────────────────────────────────────────
    PersistedObject(
        "data/credentials.json", SyncDomain.CREDENTIALS, SyncPolicy.NEVER,
        "provider API keys in plaintext",
        "The one file in the inventory whose policy is a prohibition rather than "
        "a strategy. `data/credentials.py` is built so a plaintext key leaves "
        "through exactly one method and every other accessor masks it; a sync "
        "layer that treated this as 'just another preferences file' would "
        "defeat all of that in one line. If keys must reach a second device, "
        "the user re-enters them there.",
    ),
    # ── device-only ──────────────────────────────────────────────────────────
    PersistedObject(
        "data/cache.db", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "cached OHLCV bars",
        "Fully regenerable from providers, large, and identical everywhere. "
        "Syncing it would move the most bytes for the least value in the whole "
        "inventory.",
    ),
    PersistedObject(
        "data/notifications.db", SyncDomain.PREFERENCES, SyncPolicy.APPEND_ONLY,
        "durable notification inbox and dismissal state",
        "Events are append-and-deduplicate records; dismissal is a small client "
        "state update. A future sync provider can merge event IDs without losing "
        "a notification seen on another client.",
    ),
    PersistedObject(
        "data/quota.json", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "per-provider request budget spent so far",
        "Looks shared — the quota belongs to the KEY, not the machine — and is "
        "still device-only, because syncing a counter across devices with clock "
        "skew produces a budget that is wrong in both directions. Under-count "
        "and a 25/day key is spent by mid-morning; over-count and requests are "
        "refused that would have succeeded. If two devices ever share one key, "
        "this needs a server-side counter, not replication.",
    ),
    PersistedObject(
        "data/state/symbol_meta.json", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "cached company names and market caps",
        "Regenerable from the provider on demand.",
    ),
    PersistedObject(
        "data/reports/*.json", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "saved backtest reports (JSON + HTML)",
        "Reproducible from the same inputs, and the backtest surface is "
        "desktop-only under the companion charter anyway.",
    ),
    PersistedObject(
        "data/backtest_*.db", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "throwaway journals from backtest runs",
        "Explicitly not the user's trade record — a backtester writes here so it "
        "can never touch journal.db.",
    ),
    PersistedObject(
        "logs/", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "rotating per-subsystem logs",
        "Describe this machine's execution, not this user's trading.",
    ),
    PersistedObject(
        "backups/", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "pre-update snapshots",
        "A backup that lives on the same service as the thing it protects is not "
        "a backup. It also contains credentials.json by construction, which "
        "makes replicating it a NEVER violation by another route.",
    ),
    PersistedObject(
        "exports/", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "user-initiated exports",
        "The user chose where these go; that choice is the feature.",
    ),
    PersistedObject(
        "migrations/migration_version.json", SyncDomain.DEVICE_ONLY, SyncPolicy.DEVICE_ONLY,
        "the storage-layout marker for THIS install",
        "Describes the shape of this machine's data directory. Copying another "
        "machine's marker would tell the app a migration had run that had not.",
    ),
)

#: Client-held state that has NO server-side home yet. Listed here rather than
#: omitted, because an inventory that quietly excludes what it cannot classify
#: is worse than one that names the gap — this is the honest answer to "what
#: still blocks a second device".
CLIENT_TRAPPED: tuple[PersistedObject, ...] = (
    PersistedObject(
        "localStorage: chDraw:<symbol>", SyncDomain.WORKSPACE, SyncPolicy.LAST_WRITE_WINS,
        "chart drawings — levels, trends, fibs, zones, notes",
        "User work-product, versioned ({version:3, items:[...]}), per symbol, "
        "and the last domain with no server-side home. NOT moved in V0.7.0: it "
        "needs a real one-time import path and a migration, not a default, and "
        "shipping half of that would risk the annotations it is meant to "
        "protect. Remaining blocker — see docs/ARCHITECTURE-PLATFORM.md §7.",
    ),
    PersistedObject(
        "localStorage: guideResume", SyncDomain.WORKSPACE, SyncPolicy.DEVICE_ONLY,
        "which tutorial step a tour was interrupted at",
        "Correctly device-only: resuming a half-finished walkthrough on a "
        "different screen size would resume it against elements that are not "
        "there. Tutorial COMPLETION already persists server-side in "
        "settings.json (V0.6.1); only the in-flight position is local.",
    ),
)


def by_domain(domain: SyncDomain) -> list[PersistedObject]:
    return [o for o in INVENTORY if o.domain is domain]


def by_policy(policy: SyncPolicy) -> list[PersistedObject]:
    return [o for o in INVENTORY if o.policy is policy]


def never_sync() -> list[PersistedObject]:
    """Objects that must not leave the device. Read this before writing any
    replication code at all."""
    return by_policy(SyncPolicy.NEVER)


def report() -> dict:
    """The whole inventory as data, for a diagnostics page or a design review.

    Contains no user data and no secret — only paths, classifications and
    prose — so it is safe to attach to a public bug report.
    """
    return {
        "domains": {d.value: [o.to_dict() for o in by_domain(d)]
                    for d in SyncDomain if by_domain(d)},
        "never_sync": [o.path for o in never_sync()],
        "shared_writers": [o.path for o in INVENTORY if o.shared_writer],
        "client_trapped": [o.to_dict() for o in CLIENT_TRAPPED],
        "counts": {
            "inventory": len(INVENTORY),
            "client_trapped": len(CLIENT_TRAPPED),
        },
    }
