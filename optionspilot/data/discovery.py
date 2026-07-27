"""Capability discovery — let a provider measure its own limits, and persist it.

`capabilities.py` holds the shipped per-interval depth table. Those numbers are
**measured**, not guessed, and the measuring was done by `scripts/marketdata_
probe.py`. This module is that measurement, lifted out of the script so there
is one implementation of it, plus the two things a script cannot provide: a
place to keep the answer, and a rule for when to ask again.

    measure_depth(adapter, symbol, timeframe, now)  -> deepest days accepted
    discover(adapter, symbol)                       -> a full per-interval table
    CapabilityStore(path)                           -> persist + refresh policy
    drift(discovered, capabilities)                 -> what the table gets wrong

## Discovery is advisory, and off by default

It does **not** silently rewrite `capabilities.py`, and `MarketDataConfig.
capability_discovery` defaults to False. Three reasons, in order of weight:

1. **A probe costs real upstream requests** — roughly a dozen per interval, per
   provider. Doing that on every launch to re-derive numbers that change maybe
   once a year is a poor trade, and on a rate-limited feed it is a harmful one.
2. **The shipped table is a deliberate floor.** It sits one day *inside* each
   measured cliff so a request built moments before midnight UTC cannot land on
   the far side of it. A discovery run that measured 60 and wrote 60 would undo
   that margin on every install.
3. **A probe can be wrong.** A network hiccup mid-measurement reads exactly
   like a shallower provider. A wrong number written to disk is then believed
   until something re-measures it, which is a worse failure than a table that
   is a day conservative.

So discovery reports and persists; `drift()` turns that into "the table
promises more than the provider serves", which is the finding a maintainer acts
on. What it buys is that a *future* provider need not have its depth
hand-measured before it can ship — `discover()` fills the table in, and the
stored result is refreshed on the configured cadence rather than never.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderError, ProviderRangeError,
)
from optionspilot.data.capabilities import ProviderCapabilities

log = get_logger("data")

#: Days back to try, coarse to fine. The cliff is bracketed by the last value
#: that worked and the first that did not, then narrowed by a binary search —
#: about a dozen requests per interval instead of a linear walk of hundreds.
LADDER = [1, 3, 7, 8, 15, 30, 59, 60, 61, 90, 180, 365, 729, 730, 731,
          1000, 2000, 4000, 8000, 20000]

#: Reaching the end of the ladder means "no limit we can detect", not "20000".
UNLIMITED = LADDER[-1]


@dataclass(slots=True)
class IntervalFinding:
    """What one interval's probe measured."""

    timeframe: str
    max_lookback_days: int | None      # None == unlimited (or nothing served)
    served: bool                       # did the provider serve this at all?
    refusal: str = ""
    probes: int = 0

    def as_dict(self) -> dict:
        return {"timeframe": self.timeframe,
                "max_lookback_days": self.max_lookback_days,
                "served": self.served, "refusal": self.refusal,
                "probes": self.probes}

    @classmethod
    def from_dict(cls, data: dict) -> "IntervalFinding":
        return cls(timeframe=data["timeframe"],
                   max_lookback_days=data.get("max_lookback_days"),
                   served=bool(data.get("served", False)),
                   refusal=data.get("refusal", ""),
                   probes=int(data.get("probes", 0)))


@dataclass(slots=True)
class DiscoveryResult:
    """One provider's measured capabilities, as of `measured_at`."""

    provider: str
    symbol: str
    measured_at: datetime
    intervals: dict[str, IntervalFinding] = field(default_factory=dict)
    requests_spent: int = 0

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "measured_at": self.measured_at.isoformat(),
            "requests_spent": self.requests_spent,
            "intervals": {k: v.as_dict() for k, v in self.intervals.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiscoveryResult":
        return cls(
            provider=data["provider"],
            symbol=data.get("symbol", ""),
            measured_at=datetime.fromisoformat(data["measured_at"]),
            requests_spent=int(data.get("requests_spent", 0)),
            intervals={k: IntervalFinding.from_dict(v)
                       for k, v in (data.get("intervals") or {}).items()},
        )

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.measured_at).total_seconds() / 86400.0


# ── measurement ──────────────────────────────────────────────────────────────

def _attempt(adapter: HistoryAdapter, symbol: str, tf: Timeframe, days: int,
             now: datetime) -> str:
    """One probe: "OK" | "EMPTY" | "REFUSED" | "ERROR". Never raises."""
    spec = adapter.capabilities.spec(tf)
    if spec is None:
        return "REFUSED"
    try:
        # Bypass the capability clamp deliberately — the point is to measure
        # the provider's real limit, not to confirm our own table clamps to
        # itself.
        frame = adapter._fetch_native(symbol, spec, now - timedelta(days=days),
                                      now, False)
    except ProviderRangeError:
        return "REFUSED"
    except ProviderError:
        return "ERROR"
    except Exception:  # noqa: BLE001 — a measurement must never crash the app
        return "ERROR"
    return "OK" if not frame.empty else "EMPTY"


def measure_depth(adapter: HistoryAdapter, symbol: str, tf: Timeframe,
                  now: datetime | None = None, *,
                  pause: float = 0.15) -> IntervalFinding:
    """Walk the ladder until the provider refuses, then binary-search the cliff.

    An EMPTY answer is *not* a refusal — it is a weekend, a holiday, or a
    pre-listing window — so the walk continues through it. Conflating the two
    is the original sin this whole subsystem was rebuilt to avoid, and it would
    make every measurement taken on a Sunday wrong.
    """
    now = now or datetime.now(timezone.utc)
    finding = IntervalFinding(timeframe=str(tf), max_lookback_days=None,
                              served=False)
    deepest: int | None = None
    first_refused: int | None = None

    for days in LADDER:
        status = _attempt(adapter, symbol, tf, days, now)
        finding.probes += 1
        if pause:
            time.sleep(pause)
        if status == "OK":
            deepest = days
            finding.served = True
            continue
        if status == "EMPTY":
            continue
        finding.refusal = status
        first_refused = days
        break

    if deepest is None:
        return finding                      # nothing served at any depth
    if first_refused is None:
        finding.max_lookback_days = None    # reached the end of the ladder
        return finding

    lo, hi = deepest, first_refused
    while hi - lo > 1:
        mid = (lo + hi) // 2
        status = _attempt(adapter, symbol, tf, mid, now)
        finding.probes += 1
        if pause:
            time.sleep(pause)
        if status in ("OK", "EMPTY"):
            lo = mid
        else:
            hi = mid
    finding.max_lookback_days = lo
    return finding


def discover(adapter: HistoryAdapter, symbol: str = "SPY", *,
             timeframes: list[Timeframe] | None = None,
             now: datetime | None = None,
             pause: float = 0.15) -> DiscoveryResult:
    """Measure every interval the adapter claims to support."""
    now = now or datetime.now(timezone.utc)
    targets = timeframes if timeframes is not None else [
        tf for tf in Timeframe if adapter.supports_interval(tf)]
    result = DiscoveryResult(provider=adapter.provider_name, symbol=symbol,
                             measured_at=now)
    for tf in targets:
        finding = measure_depth(adapter, symbol, tf, now, pause=pause)
        result.intervals[str(tf)] = finding
        result.requests_spent += finding.probes
    log.info("capability discovery for %s spent %d requests across %d intervals",
             adapter.provider_name, result.requests_spent, len(result.intervals))
    return result


def drift(result: DiscoveryResult,
          capabilities: ProviderCapabilities) -> list[str]:
    """Where the shipped table promises MORE than the provider actually serves.

    One-directional on purpose: a table that is conservative (promises less than
    is served) costs a little depth and nothing else, while a table that
    over-promises produces guaranteed-422 requests on every scroll — the exact
    bug that made intraday history retry forever before V0.5.2.
    """
    problems: list[str] = []
    for name, finding in result.intervals.items():
        try:
            tf = Timeframe.from_string(name)
        except Exception:  # noqa: BLE001 — an unknown interval in a stored file
            continue
        shipped = capabilities.max_lookback_days(tf)
        if not finding.served:
            problems.append(f"{name}: the provider served nothing at any depth")
            continue
        measured = finding.max_lookback_days
        if measured is None:
            continue                       # unlimited: the table cannot exceed it
        if shipped is None:
            problems.append(f"{name}: the table says unlimited but only "
                            f"{measured}d is served")
        elif shipped > measured:
            problems.append(f"{name}: the table says {shipped}d but only "
                            f"{measured}d is served")
    return problems


# ── persistence ──────────────────────────────────────────────────────────────

class CapabilityStore:
    """Discovered capabilities on disk, with a refresh policy.

    A small JSON document rather than a table in `cache.db`: it is tiny, it is
    read once at startup, and being plain text means a user can read it, mail
    it, or delete it to force a re-measurement.

    Every read path tolerates a missing, unreadable or malformed file by
    returning nothing — a broken discovery cache must degrade to "we have not
    measured this yet", never to a failed launch.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._doc: dict = {"version": 1, "providers": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("capability store at %s is unreadable (%s) — "
                        "starting fresh", self.path, exc)
            return
        if isinstance(doc, dict) and isinstance(doc.get("providers"), dict):
            self._doc = doc

    def get(self, provider: str) -> DiscoveryResult | None:
        raw = (self._doc.get("providers") or {}).get(provider)
        if not raw:
            return None
        try:
            return DiscoveryResult.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("stored capabilities for %s are malformed (%s) — "
                        "ignoring", provider, exc)
            return None

    def save(self, result: DiscoveryResult) -> None:
        self._doc.setdefault("providers", {})[result.provider] = result.as_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._doc, indent=2), encoding="utf-8")
        tmp.replace(self.path)          # atomic: never a half-written document

    def is_stale(self, provider: str, refresh_days: int,
                 now: datetime | None = None) -> bool:
        """True when `provider` has never been measured, or was measured longer
        ago than the refresh interval."""
        stored = self.get(provider)
        if stored is None:
            return True
        return stored.age_days(now) >= refresh_days

    def providers(self) -> list[str]:
        return sorted(self._doc.get("providers") or {})

    def as_dict(self) -> dict:
        return dict(self._doc)


def refresh_if_stale(adapter: HistoryAdapter, store: CapabilityStore, *,
                     refresh_days: int = 30, symbol: str = "SPY",
                     now: datetime | None = None,
                     pause: float = 0.15) -> DiscoveryResult | None:
    """Re-measure `adapter` if its stored result has aged out, and persist it.

    Returns the fresh result, or None when the stored one is still current.
    Best-effort throughout: discovery is a convenience, and a failure to
    measure must never prevent the app from serving charts.
    """
    if not store.is_stale(adapter.provider_name, refresh_days, now):
        return None
    try:
        result = discover(adapter, symbol, now=now, pause=pause)
    except Exception as exc:  # noqa: BLE001 — never break startup over a probe
        log.warning("capability discovery for %s failed: %s",
                    adapter.provider_name, exc)
        return None
    try:
        store.save(result)
    except OSError as exc:
        log.warning("could not persist discovered capabilities: %s", exc)
    problems = drift(result, adapter.capabilities)
    for problem in problems:
        log.warning("capability drift for %s — %s", adapter.provider_name,
                    problem)
    return result


__all__ = ["measure_depth", "discover", "drift", "refresh_if_stale",
           "CapabilityStore", "DiscoveryResult", "IntervalFinding",
           "LADDER", "UNLIMITED"]
