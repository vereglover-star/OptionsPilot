"""The Market Data Control Centre — administration, as opposed to selection.

Everything under `data/` up to this point answers questions the *application*
asks: which provider should serve this request, is it healthy, may it be spent,
were the bars usable. This module answers the questions a *person* asks:

    where is my data coming from?          `dashboard()["providers"]`
    why is that one not being used?        each row's `health_state` + detail
    how do I set up another one?           `set_api_key()`
    what happens if this one dies?         `dashboard()["failover"]`
    how many requests do I have left?      each row's `quota`
    is this key actually working?          `test_connection()`
    something is wrong — what do I do?     `recommendations()`
    how do I fix a broken cache?           `start_maintenance()`

Those are administration questions, and mixing them into the registry (which
must stay a fast, hot-path decision maker) or into the service (which must stay
a tier ladder) is how both would have grown a settings API. So this is a
separate object composed *over* them, and the direction of dependency is
strictly one way: control knows about the registry, the registry knows nothing
about control.

## The rule this module exists to enforce

> **The application explains itself. A user should never need to read a log.**

Concretely, that means every refusal carries a remedy. A provider is not
"unavailable" — it is "missing a free API key, get one here". A request is not
"failed" — it names which providers were asked and what each said. A quota is
not "exceeded" — it says when it resets and which provider to add instead.

## Three things that are deliberately NOT here

1. **Selection.** `dashboard()` reports the ranking; it never computes one.
   The order shown is `registry.ranking()`, so the page and the chart can never
   disagree about which provider goes first.
2. **Secrets.** No method on this class returns a plaintext key, and the only
   call to `CredentialStore.resolve()` hands it straight to the adapter that
   must send it. Every payload leaving here carries a mask.
3. **Anything a background timer can reach.** `test_connection`, the
   maintenance actions and the QA hooks all spend real upstream requests, so
   every one of them is an explicit POST behind an explicit click. The
   dashboard poll is free — it reads counters that already exist.
"""

from __future__ import annotations

import shutil
import threading
import time as _time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import Timeframe
from optionspilot.data import quality as dq
from optionspilot.data import replay as mdreplay
from optionspilot.data.adapter import (
    HistoryRequest, ProviderAuthError, ProviderEntitlementError, ProviderError,
    ProviderQuotaExceeded, ProviderRateLimited, ProviderTimeout,
    ProviderUnavailable,
)
from optionspilot.data.config import (
    ORDERING_MODES, ORDERING_TEXT, MarketDataConfig,
)
from optionspilot.data.credentials import SOURCE_ENV, CredentialStore, mask
from optionspilot.data.faults import ALL_FAULTS, FAULT_TEXT, FAULTS
from optionspilot.data.health import HEALTH_TEXT

log = get_logger("data")

# ── connection-test outcomes ─────────────────────────────────────────────────
#
# A closed vocabulary, because "Test Connection" reporting free-form text is
# how a support conversation becomes a transcription exercise. Each code has
# exactly one plain-English sentence and exactly one recommended action.

TEST_CONNECTED = "connected"
TEST_MISSING_KEY = "missing_key"
TEST_AUTH_FAILED = "authentication_failed"
#: The key works and the plan does not cover historical prices. A separate
#: outcome from `authentication_failed` because the two are opposite diagnoses
#: — see `adapter.ProviderEntitlementError` for the incident that forced the
#: distinction.
TEST_PREMIUM_REQUIRED = "premium_required"
TEST_RATE_LIMITED = "rate_limited"
TEST_UNREACHABLE = "provider_unreachable"
TEST_NETWORK = "network_failure"
TEST_UNEXPECTED = "unexpected_response"
TEST_DISABLED = "disabled"
TEST_UNKNOWN = "unknown_provider"

TEST_TEXT: dict[str, str] = {
    TEST_CONNECTED: "Connected. The provider answered with usable bars.",
    TEST_MISSING_KEY: "No API key is configured for this provider.",
    TEST_AUTH_FAILED: "The provider rejected the API key.",
    TEST_PREMIUM_REQUIRED: "Your API key is valid, but this provider's plan "
                           "does not include historical price data.",
    TEST_RATE_LIMITED: "The provider is out of requests for now.",
    TEST_UNREACHABLE: "The provider did not answer in time.",
    TEST_NETWORK: "Could not reach the provider.",
    TEST_UNEXPECTED: "The provider answered, but not with usable data.",
    TEST_DISABLED: "This provider is switched off.",
    TEST_UNKNOWN: "No such provider in this build.",
}

#: What to do about each outcome. Shown verbatim next to the result — a test
#: that says only "failed" has told the user nothing they did not already know.
TEST_ACTION: dict[str, str] = {
    TEST_CONNECTED: "Nothing to do.",
    TEST_MISSING_KEY: "Paste a key above, or set the provider's environment "
                      "variable and restart.",
    TEST_AUTH_FAILED: "Check the key for a stray space or a missing character, "
                      "then paste it again. If it is definitely right, the key "
                      "may have been revoked — generate a new one.",
    TEST_PREMIUM_REQUIRED: "There is nothing wrong with your key and nothing to "
                           "fix — do not regenerate it. This provider has moved "
                           "historical prices to a paid plan, so it cannot be "
                           "used for charts on a free account. Leave it switched "
                           "off, or upgrade the plan. Your other providers are "
                           "unaffected.",
    TEST_RATE_LIMITED: "Wait for the limit to reset, or configure another "
                       "provider so the load is shared.",
    TEST_UNREACHABLE: "Check your internet connection. If other providers work, "
                      "this one is probably having an outage — the app will "
                      "route around it automatically.",
    TEST_NETWORK: "Check your internet connection, a VPN, or a firewall that "
                  "may be blocking this host.",
    TEST_UNEXPECTED: "This usually means the provider changed its response "
                     "format. Please report it — Help ▸ Diagnostics ▸ Export.",
    TEST_DISABLED: "Turn the provider on to test it.",
    TEST_UNKNOWN: "Nothing to do.",
}

#: The probe used by Test Connection: daily bars over the last three weeks.
#:
#: Daily because it is the ONE interval every provider in the chain serves
#: (Stooq has no intraday at all), and three weeks because it spans holidays
#: and long weekends — a probe that could legitimately return zero bars cannot
#: tell "working" from "broken", which is the exact confusion this whole
#: subsystem was rebuilt to eliminate.
PROBE_TIMEFRAME = Timeframe.D1
PROBE_DAYS = 21
#: The symbol every probe uses. SPY because it is the most liquid instrument in
#: the world, is listed on every one of these feeds, and has never had a day
#: without bars — so an empty answer is unambiguously the provider's fault.
PROBE_SYMBOL = "SPY"

# ── maintenance ──────────────────────────────────────────────────────────────

ACTION_CLEAR_CACHE = "clear_cache"
ACTION_REBUILD_CACHE = "rebuild_cache"
ACTION_VERIFY_CACHE = "verify_cache"
ACTION_VALIDATE = "validate"
ACTION_REPLAY = "replay"
ACTION_BENCHMARK = "benchmark"
ACTION_DIAGNOSTICS = "diagnostics"
ACTION_CAPABILITIES = "capabilities"

ALL_ACTIONS = (ACTION_CLEAR_CACHE, ACTION_REBUILD_CACHE, ACTION_VERIFY_CACHE,
               ACTION_VALIDATE, ACTION_REPLAY, ACTION_BENCHMARK,
               ACTION_DIAGNOSTICS, ACTION_CAPABILITIES)

#: (label, what it does, does it spend upstream requests). The third field is
#: not decoration: on a 25-request-per-day key, a user is entitled to know
#: which button costs them requests BEFORE pressing it.
ACTION_INFO: dict[str, tuple[str, str, bool]] = {
    ACTION_CLEAR_CACHE: (
        "Clear chart cache",
        "Deletes every locally stored candle. Charts re-download what they "
        "need, so the next few loads are slower. Nothing else is affected.",
        False),
    ACTION_REBUILD_CACHE: (
        "Rebuild cache",
        "Starts a brand-new cache file. The old one is kept beside it (renamed "
        "with a .corrupt- timestamp) rather than deleted, in case it is needed.",
        False),
    ACTION_VERIFY_CACHE: (
        "Verify cache integrity",
        "Checks the cache file for structural damage and for bars that could "
        "never be drawn. Read-only — it repairs nothing.",
        False),
    ACTION_VALIDATE: (
        "Run validation",
        "Re-runs the semantic checks on the candles already cached: gaps, "
        "interval conformance, duplicate and impossible bars.",
        False),
    ACTION_REPLAY: (
        "Run provider replay",
        "Re-runs the most recent chart request against every provider "
        "individually and compares their answers.",
        True),
    ACTION_BENCHMARK: (
        "Run provider benchmark",
        "Times one identical request against each usable provider and reports "
        "latency, bar count and data quality side by side.",
        True),
    ACTION_DIAGNOSTICS: (
        "Run diagnostics",
        "Collects the full health snapshot — the same data as Help ▸ "
        "Diagnostics and the export.",
        False),
    ACTION_CAPABILITIES: (
        "Re-measure capabilities",
        "Asks each provider how far back its history really goes, by probing "
        "until it refuses. Slow and request-hungry; the shipped table is "
        "already measured, so this is for verifying drift.",
        True),
}


class MaintenanceJob:
    """One maintenance run's live state. Thread-safe.

    A single slot, exactly like `UIServer.backtest_job`: two concurrent cache
    rebuilds is not a use case, and refusing the second is a clearer answer
    than queueing it somewhere the user cannot see.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._action = ""
        self._step = ""
        self._done = 0
        self._total = 0
        self._lines: list[str] = []
        self._summary: dict = {}
        self._error = ""
        self._started = 0.0
        self._finished = 0.0
        #: Cooperative stop. A capability re-measurement probes every provider
        #: at every depth and takes MINUTES; without this it holds the single
        #: slot for all of them and a user who started it by mistake has no way
        #: out but a restart. Checked between units of work — `discover()` is
        #: not interruptible mid-provider, and pretending otherwise (a killed
        #: thread) would leave the provider's counters half-written.
        self._cancel = threading.Event()

    def claim(self, action: str) -> bool:
        """Take the single slot atomically, or report that it is taken.

        The slot used to be taken by the worker thread's own ``begin`` call,
        which left a window between accepting an action and the job reporting
        itself as running. In that window a second request read ``running ==
        False`` and started a *second* worker — two concurrent cache rebuilds,
        which is exactly what one slot exists to prevent — and the dict handed
        back to the caller described the previous, idle job.
        """
        with self._lock:
            if self._state == "running":
                return False
            self._begin_locked(action, 1)
        self._cancel.clear()
        return True

    def begin(self, action: str, total: int) -> None:
        with self._lock:
            continuing = self._state == "running" and self._action == action
            self._begin_locked(action, total)
        # Cleared here rather than in `cancel()`, so a cancellation arriving
        # between two runs cannot silently apply to the next one. A worker
        # re-declaring the total of the job it is ALREADY running (validation
        # does this once it knows how many pairs it found) is a continuation,
        # not a new run, and must not discard a cancellation the user has
        # already asked for in the meantime.
        if not continuing:
            self._cancel.clear()

    def _begin_locked(self, action: str, total: int) -> None:
        """Reset to a running job. Caller holds ``_lock``."""
        self._state = "running"
        self._action = action
        self._step = "Starting…"
        self._done = 0
        self._total = max(1, total)
        self._lines = []
        self._summary = {}
        self._error = ""
        self._started = _time.time()
        self._finished = 0.0

    def cancel(self) -> bool:
        """Ask the running action to stop at its next checkpoint."""
        if not self.running:
            return False
        self._cancel.set()
        with self._lock:
            self._step = "Stopping…"
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def stopped(self, summary: dict) -> None:
        """Finish early because cancellation was requested. Distinct from
        `finish` so the UI can say "stopped" rather than implying the work
        completed, and distinct from `fail` because nothing went wrong."""
        with self._lock:
            self._state = "cancelled"
            self._step = "Stopped"
            self._summary = summary
            self._finished = _time.time()

    def step(self, label: str, *, advance: int = 1) -> None:
        with self._lock:
            self._step = label
            self._done = min(self._total, self._done + advance)
            self._lines.append(label)

    def note(self, line: str) -> None:
        """Add a result line without advancing progress."""
        with self._lock:
            self._lines.append(line)

    def finish(self, summary: dict) -> None:
        with self._lock:
            self._state = "done"
            self._step = "Finished"
            self._done = self._total
            self._summary = summary
            self._finished = _time.time()

    def fail(self, error: str) -> None:
        with self._lock:
            self._state = "error"
            self._step = "Failed"
            self._error = error
            self._finished = _time.time()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state == "running"

    def as_dict(self) -> dict:
        with self._lock:
            elapsed = ((self._finished or _time.time()) - self._started
                       if self._started else 0.0)
            label, description, spends = ACTION_INFO.get(
                self._action, (self._action, "", False))
            return {
                "state": self._state,
                "action": self._action,
                "label": label,
                "description": description,
                "spends_requests": spends,
                "cancellable": self._state == "running" and not self._cancel.is_set(),
                "cancelled": self._cancel.is_set(),
                "step": self._step,
                "done": self._done,
                "total": self._total,
                "progress": round(self._done / self._total, 3) if self._total else 0.0,
                "lines": list(self._lines),
                "summary": dict(self._summary),
                "error": self._error,
                "elapsed_seconds": round(elapsed, 1),
            }


class MarketDataControl:
    """Administration surface over a live `MarketDataService`. Thread-safe.

    Constructed once by the composition root (`orchestrator.py`) and reached
    only through the `/api/marketdata/*` endpoints.
    """

    def __init__(self, service, *, config: MarketDataConfig | None = None,
                 credentials: CredentialStore | None = None,
                 state_path: str | Path | None = None,
                 environ: dict | None = None):
        self.service = service
        self.registry = service.registry
        self.config = config or service.config
        self.credentials = (credentials if credentials is not None
                            else getattr(self.registry, "credentials", None)
                            or CredentialStore(self.config.credentials_path))
        self._environ = environ
        self._state_path = (Path(state_path) if state_path is not None
                            else (Path(self.config.control_state_path)
                                  if self.config.control_state_path else None))
        self._lock = threading.Lock()
        self.job = MaintenanceJob()
        self._last_test: dict[str, dict] = {}
        self._last_replay: dict | None = None
        #: The order the adapters shipped with, captured before any stored
        #: order is applied. "Reset to default" needs to mean the DEFAULT, not
        #: whatever was in force when the app last started — which is what a
        #: reset computed from the live registry would give.
        self._default_order = [a.provider_name for a in sorted(
            self.registry.adapters, key=lambda a: type(a).provider_priority)]

    # ── reporting ────────────────────────────────────────────────────────────

    def dashboard(self) -> dict:
        """Everything the control centre displays, in one payload.

        One request, because the page auto-refreshes and N requests per refresh
        would make the diagnostics screen the busiest client of the system it
        is diagnosing. Nothing here spends an upstream request: every number is
        a counter that already exists.
        """
        rows = self.registry.health_report()
        order = self.registry.order()
        by_name = {a.provider_name: a for a in self.registry.adapters}
        providers = []
        for row in rows:
            adapter = by_name.get(row["name"])
            if adapter is None:               # pragma: no cover — registry race
                continue
            providers.append(self._provider_row(row, adapter, order))
        return {
            "available": True,
            "providers": providers,
            "ranking": self.registry.ranking(),
            "order": order,
            "default_order": list(self._default_order),
            "ordering_mode": self.config.ordering(),
            "ordering_modes": [
                {"mode": m, "label": m.title(), "explanation": ORDERING_TEXT[m]}
                for m in ORDERING_MODES],
            "cache": (self.service.cache.stats() if self.service.cache else None),
            "requests": self.service.diagnostics.summary(),
            "failover": self._failover(),
            "recommendations": self.recommendations(),
            "maintenance": {
                "job": self.job.as_dict(),
                "actions": [{"action": a, "label": ACTION_INFO[a][0],
                             "description": ACTION_INFO[a][1],
                             "spends_requests": ACTION_INFO[a][2]}
                            for a in ALL_ACTIONS],
            },
            "qa_mode": bool(self.config.qa_mode),
            "qa": self.qa_state() if self.config.qa_mode else None,
            "health_text": dict(HEALTH_TEXT),
        }

    def _provider_row(self, row: dict, adapter, order: list[str]) -> dict:
        """One provider's full record: health + credentials + capability.

        `row` is `registry.health_report()`'s entry verbatim — everything it
        already carries is passed through untouched rather than recomputed, so
        the control centre and the diagnostics export cannot disagree about a
        number. What is ADDED here is the administrative view: where the key
        comes from, what it looks like masked, and where the provider sits in
        the user's own order.
        """
        name = row["name"]
        provider_config = self.config.for_provider(name)
        source = self.credentials.source_for(
            name, provider_config, tuple(adapter.api_key_env_vars),
            environ=self._environ)
        stored = self.credentials.describe(name)
        return {
            **row,
            "position": order.index(name) + 1 if name in order else None,
            "enabled": bool(adapter.config.enabled),
            "credential": {
                "required": adapter.requires_api_key,
                "source": source,
                # The mask is of the EFFECTIVE key, so a user reading
                # "••••••••9f3a" is looking at the key actually being sent —
                # not at a stored key an environment variable is shadowing.
                "masked_key": mask(adapter.api_key),
                "configured_at": stored["configured_at"],
                "last_success_at": (stored["last_success_at"]
                                    or row.get("last_success_at")),
                "env_vars": list(adapter.api_key_env_vars),
                # The one thing a user cannot work out for themselves, and the
                # single most likely support question about this screen.
                "env_overrides": source == SOURCE_ENV and stored["stored"],
                "signup_url": adapter.signup_url,
            },
            "feed": self._feed(adapter),
            "capability": {
                "intervals": row.get("intervals", []),
                "intraday": any(tf.minutes < Timeframe.D1.minutes
                                for tf in adapter.capabilities.intervals),
                "daily": adapter.supports_interval(Timeframe.D1),
                "weekly": adapter.supports_interval(Timeframe.W1),
                "monthly": adapter.supports_interval(Timeframe.MN1),
                "extended_hours": adapter.capabilities.extended_hours,
                "max_lookback_days": row.get("max_lookback_days", {}),
                "asset_classes": row.get("asset_classes", []),
            },
            "last_test": self._last_test.get(name),
        }

    def _feed(self, adapter) -> dict:
        """The plain-English "what kind of source is this?" labels.

        Derived from what the adapter already declares rather than written down
        per provider, so a new provider gets correct labels for free and an
        existing one cannot drift out of date with its own rate-limit policy.
        """
        policy = adapter.quota.policy
        if not policy.metered:
            limits, kind = "Unlimited", "unlimited"
        else:
            parts = []
            if policy.per_minute:
                parts.append(f"{policy.per_minute}/min")
            if policy.per_day:
                parts.append(f"{policy.per_day}/day")
            limits, kind = " · ".join(parts), "rate_limited"
        return {
            "kind": kind,
            "limits": limits,
            # Every source in the shipped chain is a free tier. Stated rather
            # than assumed, because the moment one is not, this label is the
            # thing a user will look for.
            "cost": ("Free" if not adapter.requires_api_key
                     else ("Paid plan needed for history"
                           if not adapter.free_tier_serves_history
                           else "Free tier")),
            "latency": "Real-time" if adapter.capabilities.realtime
                       else "Delayed (poll-only)",
            "key": "No key needed" if not adapter.requires_api_key
                   else "Free API key required",
            # Stated on the card itself, so a user reads it BEFORE spending ten
            # minutes registering for a key that cannot serve charts.
            "free_tier_serves_history": adapter.free_tier_serves_history,
            "description": adapter.capabilities.description,
        }

    def _failover(self) -> dict:
        """What the app would do RIGHT NOW if the head of the chain died.

        This is the "what happens when one fails" question, answered with the
        live chain rather than with a paragraph of documentation — the two can
        disagree, and only one of them is true.
        """
        usable = [a.provider_name for a in self.registry.adapters
                  if a.monitor.available()]
        chain = self.registry.ranking()
        intraday = [a.provider_name for a in self.registry.adapters
                    if a.monitor.available()
                    and any(tf.minutes < Timeframe.D1.minutes
                            for tf in a.capabilities.intervals)]
        # Yahoo and yfinance are two code paths over ONE upstream. Counting
        # them as two sources is the mistake that makes a keyless install look
        # redundant when it has a single point of failure — see the V0.5.5
        # limitation in docs/NEXT_SESSION.md.
        independent = {self._family(n) for n in usable}
        return {
            "usable": usable,
            "usable_count": len(usable),
            "independent_sources": sorted(independent),
            "independent_count": len(independent),
            "intraday_sources": intraday,
            "primary": chain[0]["name"] if chain else None,
            "next": chain[1]["name"] if len(chain) > 1 else None,
            "single_point_of_failure": len(independent) <= 1,
        }

    @staticmethod
    def _family(name: str) -> str:
        """Which upstream a provider actually depends on.

        `yahoo` and `yfinance` are two independent *code paths* to the same
        servers and the same IP-based rate limiter, so a Yahoo outage takes
        both. Anything that treats them as two sources will overstate this
        install's redundancy by exactly one.
        """
        return "yahoo" if name in ("yahoo", "yfinance") else name

    # ── recommendations ──────────────────────────────────────────────────────

    def recommendations(self) -> list[dict]:
        """Specific, actionable advice about the current configuration.

        Ordered by severity, and every entry names a *next action* rather than
        only a condition. A recommendation that says "you have one provider"
        and stops is an observation; one that says "add Finnhub, it is free,
        here is the link" is advice.
        """
        out: list[dict] = []
        adapters = self.registry.adapters
        usable = [a for a in adapters if a.monitor.available()]
        families = {self._family(a.provider_name) for a in usable}

        if not usable:
            out.append({
                "severity": "critical",
                "title": "No market-data provider is currently usable",
                "detail": "Charts and scans cannot load new data. Check your "
                          "internet connection first. If you are online, open "
                          "Help ▸ Diagnostics to see what each provider said, "
                          "then use Test Connection below on the one you "
                          "expect to work.",
                "action": "diagnose",
            })
        elif len(families) <= 1:
            missing = [a for a in adapters
                       if a.requires_api_key and not a.api_key]
            # Prefer a provider whose FREE tier can actually serve history.
            # Recommending Finnhub here — which the chain order would otherwise
            # do, it being the first keyed provider — sends the user to sign up
            # for something that answers 403 to every chart request. Measured,
            # not assumed: see `adapter.free_tier_serves_history`.
            usable_free = [a for a in missing if a.free_tier_serves_history]
            suggest = (usable_free or missing)[0] if missing else None
            out.append({
                "severity": "warning",
                "title": "You have only one independent data source",
                "detail": "Yahoo and yfinance reach the same servers through "
                          "two different code paths, so a Yahoo outage — or an "
                          "IP rate-limit — takes both at once. Adding one keyed "
                          "provider gives you a genuinely independent source"
                          + (f", and {suggest.provider_name} has a free tier."
                             if suggest else "."),
                "action": "add_provider",
                "provider": suggest.provider_name if suggest else None,
                "signup_url": suggest.signup_url if suggest else "",
            })

        for adapter in adapters:
            name = adapter.provider_name
            snap = adapter.monitor.snapshot()
            quota = snap.get("quota") or {}
            if quota.get("exhausted"):
                alternative = self._alternative_to(name)
                out.append({
                    "severity": "warning",
                    "title": f"{name} has used its entire daily allowance",
                    "detail": "It has been taken out of rotation and will "
                              "return when the quota resets."
                              + (f" {alternative} is configured and can carry "
                                 f"the load until then."
                                 if alternative else
                                 " Configuring a second keyed provider would "
                                 "keep an independent source available."),
                    "action": "quota",
                    "provider": name,
                })
            elif quota.get("warning"):
                out.append({
                    "severity": "info",
                    "title": f"{name} is close to its daily limit",
                    "detail": f"{quota.get('used_today')} of "
                              f"{quota.get('per_day')} requests used. It is "
                              f"already being asked less often as it fills up. "
                              f"Adding another provider spreads the load.",
                    "action": "quota",
                    "provider": name,
                })
            if snap.get("breaker_trips", 0) >= 3 and snap.get("requests", 0) >= 5:
                out.append({
                    "severity": "info",
                    "title": f"{name} keeps failing",
                    "detail": f"It has been taken out of rotation "
                              f"{snap['breaker_trips']} times this session "
                              f"(last error: {snap.get('last_error') or 'unknown'}). "
                              f"The app routes around it automatically, but "
                              f"switching it off stops it being tried at all.",
                    "action": "disable",
                    "provider": name,
                })
            if adapter.monitor.auth_failed:
                out.append({
                    "severity": "warning",
                    "title": f"{name} rejected its API key",
                    "detail": "The key is not being retried, because repeated "
                              "authentication failures can get an IP blocked. "
                              "Paste a new key to bring it back.",
                    "action": "fix_key",
                    "provider": name,
                })
            if adapter.monitor.entitlement_failed:
                # Phrased to stop the user doing the wrong thing. Left to their
                # own devices, someone who sees a provider fail with a key
                # configured will regenerate the key — which is exactly what
                # this milestone's live certification caught someone doing,
                # several times, against a key that was never wrong.
                out.append({
                    "severity": "info",
                    "title": f"{name} needs a paid plan for historical prices",
                    "detail": "Your API key is valid — the provider accepted "
                              "it and then declined to serve chart data, which "
                              "means the plan does not include it. There is "
                              "nothing to fix and no reason to regenerate the "
                              "key. Leave this provider switched off unless you "
                              "upgrade; your other providers are unaffected.",
                    "action": "premium",
                    "provider": name,
                })
        order = {"critical": 0, "warning": 1, "info": 2}
        out.sort(key=lambda r: order.get(r["severity"], 3))
        return out

    def _alternative_to(self, name: str) -> str | None:
        """Another usable keyed provider, for a "use this instead" hint."""
        for adapter in self.registry.adapters:
            if (adapter.provider_name != name and adapter.requires_api_key
                    and adapter.monitor.available()):
                return adapter.provider_name
        return None

    # ── credentials ──────────────────────────────────────────────────────────

    def set_api_key(self, name: str, api_key: str) -> dict:
        """Store a key and apply it to the live adapter — no restart.

        Returns the provider's fresh dashboard row plus, when relevant, the one
        warning that cannot be inferred from it: an environment variable is
        winning and the pasted key is therefore inert.
        """
        adapter = self.registry.get(name)
        if adapter is None:
            return {"error": f"no market-data provider named {name!r}"}
        if not adapter.requires_api_key:
            return {"error": f"{name} does not use an API key"}
        api_key = (api_key or "").strip()
        if not api_key:
            return {"error": "an API key is required"}
        self.credentials.set_key(name, api_key)
        effective = adapter.set_api_key(api_key, environ=self._environ)
        self._persist()
        shadowed = bool(effective) and effective != api_key
        if shadowed:
            log.warning("a key was stored for %s but an environment variable "
                        "is overriding it", name)
        return {
            "ok": True,
            "provider": name,
            "masked_key": mask(effective),
            "env_overrides": shadowed,
            "message": (
                f"Saved. Note that an environment variable "
                f"({', '.join(adapter.api_key_env_vars)}) is set and takes "
                f"precedence, so the stored key is not the one being used — "
                f"clear the variable to use it."
                if shadowed else
                f"Saved. {name} is now configured and will be used for chart "
                f"history."),
        }

    def remove_api_key(self, name: str) -> dict:
        """Forget a stored key and take the provider out of use."""
        adapter = self.registry.get(name)
        if adapter is None:
            return {"error": f"no market-data provider named {name!r}"}
        existed = self.credentials.remove_key(name)
        effective = adapter.set_api_key(None, environ=self._environ)
        self._persist()
        return {
            "ok": True,
            "provider": name,
            "removed": existed,
            "masked_key": mask(effective),
            "message": (
                f"Removed the stored key, but an environment variable is still "
                f"providing one — {name} remains configured."
                if effective else
                f"Removed. {name} will not be used until a key is configured."),
        }

    # ── ordering ─────────────────────────────────────────────────────────────

    def set_enabled(self, name: str, enabled: bool) -> dict:
        if not self.registry.set_enabled(name, bool(enabled)):
            return {"error": f"no market-data provider named {name!r}"}
        self._persist()
        return {"ok": True, "provider": name, "enabled": bool(enabled)}

    def set_order(self, names: list[str]) -> dict:
        final = self.registry.reorder([str(n) for n in names or []])
        self._persist()
        return {"ok": True, "order": final}

    def move(self, name: str, direction: str) -> dict:
        """Move one provider one place up or down the configured order."""
        order = self.registry.order()
        if name not in order:
            return {"error": f"no market-data provider named {name!r}"}
        index = order.index(name)
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(order):
            # Not an error: a user pressing Up on the first row has not done
            # anything wrong, and an error toast for it would be noise.
            return {"ok": True, "order": order, "moved": False}
        order[index], order[target] = order[target], order[index]
        return {**self.set_order(order), "moved": True}

    def reset_order(self) -> dict:
        """Restore the shipped provider order."""
        result = self.set_order(list(self._default_order))
        return {**result, "reset": True}

    def set_ordering_mode(self, mode: str) -> dict:
        mode = (mode or "").strip().lower()
        if mode not in ORDERING_MODES:
            return {"error": f"ordering mode must be one of "
                             f"{sorted(ORDERING_MODES)}"}
        with self._lock:
            # `dynamic_ranking` is forced back on: it is the legacy spelling of
            # "static", and leaving it False would silently pin the chain no
            # matter which mode the user just chose (see `config.ordering()`).
            self.config = replace(self.config, ordering_mode=mode,
                                  dynamic_ranking=True)
        self.registry.config = self.config
        self.service.config = self.config
        self._persist()
        log.info("market-data ordering mode set to %s", mode)
        return {"ok": True, "ordering_mode": mode,
                "explanation": ORDERING_TEXT[mode]}

    # ── connection testing ───────────────────────────────────────────────────

    def test_connection(self, name: str) -> dict:
        """Actually call the provider, and report what happened.

        Deliberately end to end: transport, authentication, parsing, canonical
        normalization and semantic validation, all through the SAME
        `fetch_history` a chart uses. A test that stopped at "the socket
        opened" would pass for a provider whose response format had changed —
        which is the failure most worth catching, because it is the one the
        chart cannot route around.

        The result is recorded on the health monitor exactly like any other
        request. That is intentional: a successful test genuinely is evidence
        the provider works, and pretending otherwise would mean the dashboard
        showed a green test next to a red provider.

        **Nothing here is written to the cache.** `fetch_history` returns a
        frame; only the service stores one, and the service is not involved.
        """
        adapter = self.registry.get(name)
        if adapter is None:
            return self._test_result(name, TEST_UNKNOWN, 0.0)
        if not adapter.config.enabled:
            return self._test_result(name, TEST_DISABLED, 0.0)
        if adapter.requires_api_key and not adapter.api_key:
            return self._test_result(name, TEST_MISSING_KEY, 0.0,
                                     signup_url=adapter.signup_url)
        # A budget refusal is answered WITHOUT a request. Sending one anyway to
        # "see what happens" would spend an allowance the user does not have,
        # on a question whose answer is already known.
        spendable, refusal = adapter.can_spend_request()
        if not spendable:
            return self._test_result(name, TEST_RATE_LIMITED, 0.0, detail=refusal)

        timeframe = (PROBE_TIMEFRAME if adapter.supports_interval(PROBE_TIMEFRAME)
                     else next(iter(adapter.capabilities.intervals), None))
        if timeframe is None:                # pragma: no cover — no such adapter
            return self._test_result(name, TEST_UNEXPECTED, 0.0,
                                     detail="this provider serves no intervals")
        now = datetime.now(timezone.utc)
        request = HistoryRequest(PROBE_SYMBOL, timeframe,
                                 now - timedelta(days=PROBE_DAYS), now)
        t0 = _time.perf_counter()
        try:
            frame = adapter.fetch_history(request, now=now)
        except ProviderAuthError as exc:
            return self._test_result(name, TEST_AUTH_FAILED, _ms(t0), detail=str(exc),
                                     signup_url=adapter.signup_url)
        except ProviderEntitlementError as exc:
            # The provider authenticated us and then refused the data. Before
            # reporting that, PROVE the key is good on an endpoint the free
            # plan includes — otherwise this is still an inference, and the
            # user is owed a fact. Without the second call the honest answer
            # would be "one of two things is wrong", which is what the old
            # behaviour effectively said while claiming to know which.
            return self._entitlement_result(adapter, exc, _ms(t0))
        except (ProviderQuotaExceeded, ProviderRateLimited) as exc:
            return self._test_result(name, TEST_RATE_LIMITED, _ms(t0), detail=str(exc))
        except ProviderTimeout as exc:
            return self._test_result(name, TEST_UNREACHABLE, _ms(t0), detail=str(exc))
        except ProviderUnavailable as exc:
            return self._test_result(name, TEST_NETWORK, _ms(t0), detail=str(exc))
        except ProviderError as exc:
            return self._test_result(name, TEST_UNEXPECTED, _ms(t0), detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not 500
            return self._test_result(name, TEST_UNEXPECTED, _ms(t0),
                                     detail=f"{type(exc).__name__}: {exc}")
        latency = _ms(t0)
        if frame.empty:
            return self._test_result(
                name, TEST_UNEXPECTED, latency,
                detail=f"the provider answered with no bars at all for the "
                       f"last {PROBE_DAYS} days, which cannot be correct for "
                       f"a liquid symbol")
        _, report = dq.validate_history(frame, timeframe, now=now,
                                        context=f"connection test {name}")
        if not report.usable:
            return self._test_result(
                name, TEST_UNEXPECTED, latency,
                detail=f"the bars did not pass validation: {report.summary()}")
        # The key demonstrably works. Recorded on the store as well as the
        # monitor because "this key last worked at…" must survive a restart.
        self.credentials.note_success(name)
        return self._test_result(name, TEST_CONNECTED, latency, bars=len(frame),
                                 quality=round(report.score, 1))

    def _entitlement_result(self, adapter, exc, latency_ms: float) -> dict:
        """Turn a 403 into a diagnosis, by checking the key separately.

        A 403 already means "authenticated, not permitted" in HTTP, and for
        Finnhub specifically it is measurably the only thing it can mean (an
        invalid key is a 401). But a provider whose key check is *free* can
        turn that from a strong inference into a demonstrated fact for the
        cost of one small request, and the sentence the user reads is the
        difference between "regenerate your key" and "your key is fine".

        A provider with no free endpoint to ask (`can_verify_credentials` is
        False) still gets the entitlement verdict — the HTTP semantics justify
        it — just without the corroborating line.
        """
        name = adapter.provider_name
        detail = str(exc)
        if adapter.can_verify_credentials:
            accepted, note = adapter.verify_credentials()
            if not accepted:
                # The key really is bad, and the 403 was a red herring. Report
                # the failure we can prove rather than the one we assumed.
                adapter.monitor.clear_auth_failure()
                adapter.monitor.note_auth_failure()
                return self._test_result(
                    name, TEST_AUTH_FAILED, latency_ms,
                    detail=f"{detail} — and the key was also refused by this "
                           f"provider's credential check ({note})",
                    signup_url=adapter.signup_url)
            detail = f"{detail}. Verified separately: {note}."
            # The key demonstrably works, so record it as such — a user who
            # later looks at "last worked" should not see "never" for a key
            # that was just proven good.
            self.credentials.note_success(name)
        return self._test_result(name, TEST_PREMIUM_REQUIRED, latency_ms,
                                 detail=detail)

    def _test_result(self, name: str, code: str, latency_ms: float, *,
                     bars: int = 0, quality: float | None = None,
                     detail: str = "", signup_url: str = "") -> dict:
        result = {
            "provider": name,
            "ok": code == TEST_CONNECTED,
            "code": code,
            "message": TEST_TEXT.get(code, code),
            "action": TEST_ACTION.get(code, ""),
            "detail": detail[:400],
            "latency_ms": round(latency_ms, 1),
            "bars": bars,
            "quality": quality,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if signup_url:
            result["signup_url"] = signup_url
        with self._lock:
            self._last_test[name] = result
        log.info("connection test for %s: %s (%.0f ms)", name, code, latency_ms)
        return result

    # ── maintenance ──────────────────────────────────────────────────────────

    def start_maintenance(self, action: str) -> dict:
        """Kick off a maintenance action on a background thread.

        Background because several of these take tens of seconds (a capability
        re-measurement takes minutes) and a synchronous endpoint would hold a
        request open long past any sensible client timeout, leaving the user
        looking at a spinner with no way to tell a slow job from a dead one.
        Progress is polled instead, which also gives the "Every action should
        display progress" requirement somewhere real to read from.
        """
        if action not in ALL_ACTIONS:
            return {"error": f"unknown maintenance action {action!r} "
                             f"(known: {', '.join(ALL_ACTIONS)})"}
        # Claim the slot BEFORE spawning the worker. Checking `job.running` and
        # then starting a thread that claims it later is a check-then-act: two
        # clicks landing together both passed, and both ran.
        if not self.job.claim(action):
            running = self.job.as_dict()
            # Naming the action matters more than it looks: "Re-measure
            # capabilities" probes every provider at every depth and takes
            # MINUTES, and a user who started it and then pressed "Clear chart
            # cache" needs to be told what they are waiting for — not that
            # something unspecified is in the way.
            return {"error": f"'{running['label']}' is still running "
                             f"({running['step']}). Wait for it to finish, or "
                             f"reload the page — it will keep going.",
                    "job": running}
        thread = threading.Thread(target=self._run_maintenance, args=(action,),
                                  name=f"marketdata-{action}", daemon=True)
        try:
            thread.start()
        except RuntimeError as exc:
            # A claimed slot with no worker behind it would block every later
            # action for the life of the process.
            self.job.fail(f"could not start the maintenance worker: {exc}")
            return {"error": f"could not start '{action}': {exc}"}
        return {"ok": True, "action": action, "job": self.job.as_dict()}

    def maintenance_status(self) -> dict:
        return self.job.as_dict()

    def cancel_maintenance(self) -> dict:
        """Ask the running action to stop at its next checkpoint.

        Cooperative, never forcible. The actions that take long enough to want
        cancelling are the ones spending upstream requests, and abandoning one
        of those mid-flight would leave a provider's counters inconsistent with
        the requests it actually served — a worse state than simply finishing
        the provider in hand.
        """
        if not self.job.cancel():
            return {"ok": True, "cancelled": False,
                    "message": "No maintenance action is running."}
        return {"ok": True, "cancelled": True,
                "message": "Stopping after the current step.",
                "job": self.job.as_dict()}

    def _run_maintenance(self, action: str) -> None:
        runner = {
            ACTION_CLEAR_CACHE: self._do_clear_cache,
            ACTION_REBUILD_CACHE: self._do_rebuild_cache,
            ACTION_VERIFY_CACHE: self._do_verify_cache,
            ACTION_VALIDATE: self._do_validate,
            ACTION_REPLAY: self._do_replay,
            ACTION_BENCHMARK: self._do_benchmark,
            ACTION_DIAGNOSTICS: self._do_diagnostics,
            ACTION_CAPABILITIES: self._do_capabilities,
        }[action]
        try:
            runner()
        except Exception as exc:  # noqa: BLE001 — a maintenance failure is a
            log.error("maintenance action %s failed: %s", action, exc,  # result,
                      exc_info=True)                                    # not a
            self.job.fail(f"{type(exc).__name__}: {exc}")               # crash

    def _do_clear_cache(self) -> None:
        self.job.begin(ACTION_CLEAR_CACHE, 2)
        self.job.step("Clearing stored candles…")
        removed = self.service.cache.purge() if self.service.cache else 0
        self.job.step("Dropping in-memory frames…")
        memo = self.service.invalidate()
        self.job.finish({
            "bars_removed": removed, "memo_entries_dropped": memo,
            "message": f"Removed {removed:,} cached bars and {memo} in-memory "
                       f"frame(s). Charts will re-download what they need.",
        })

    def _do_rebuild_cache(self) -> None:
        self.job.begin(ACTION_REBUILD_CACHE, 2)
        if not self.service.cache:
            self.job.finish({"message": "There is no local cache in this "
                                        "session — nothing to rebuild."})
            return
        self.job.step("Quarantining the current cache file…")
        result = self.service.cache.rebuild()
        self.job.step("Dropping in-memory frames…")
        self.service.invalidate()
        self.job.finish({
            **result,
            "message": f"Rebuilt. {result['bars_discarded']:,} bar(s) were "
                       f"discarded; the old file was kept alongside the new "
                       f"one rather than deleted.",
        })

    def _do_verify_cache(self) -> None:
        self.job.begin(ACTION_VERIFY_CACHE, 1)
        if not self.service.cache:
            self.job.finish({"message": "There is no local cache to verify."})
            return
        self.job.step("Checking the cache file…")
        report = self.service.cache.verify()
        for finding in report["findings"]:
            self.job.note(finding)
        self.job.finish({
            **report,
            "message": ("The cache is sound." if report["ok"] else
                        f"Found {report['suspect_bars']:,} unusable bar(s) in "
                        f"{report['bars']:,}. Rebuild the cache to clear them."),
        })

    def _do_validate(self) -> None:
        """Re-validate what is already cached — no upstream requests.

        Runs over the symbols the cache actually holds rather than the
        watchlist, because the question is "is the data on this disk usable",
        and a watchlist symbol with nothing cached has nothing to say about it.
        """
        self.job.begin(ACTION_VALIDATE, 1)
        cache = self.service.cache
        if not cache:
            self.job.finish({"message": "There is no local cache to validate."})
            return
        pairs = cache.coverage_pairs() if hasattr(cache, "coverage_pairs") else []
        self.job.begin(ACTION_VALIDATE, max(1, len(pairs)))
        now = datetime.now(timezone.utc)
        checked = failed = 0
        scores: list[float] = []
        for symbol, timeframe in pairs:
            if self.job.cancelled:
                self.job.stopped({
                    "frames_checked": checked, "frames_failed": failed,
                    "message": f"Stopped after {checked} frame(s)."})
                return
            self.job.step(f"Validating {symbol} {timeframe}…")
            frame = cache.load(symbol, timeframe,
                               datetime.fromtimestamp(0, tz=timezone.utc), now)
            if frame.empty:
                continue
            checked += 1
            _, report = dq.validate_history(frame, timeframe, now=now,
                                            context=f"maintenance {symbol}")
            scores.append(report.score)
            if not report.usable:
                failed += 1
                self.job.note(f"  ✗ {symbol} {timeframe}: {report.summary()}")
        average = round(sum(scores) / len(scores), 1) if scores else None
        self.job.finish({
            "frames_checked": checked, "frames_failed": failed,
            "average_quality": average,
            "message": (f"Checked {checked} cached frame(s); all usable "
                        f"(average quality {average})." if not failed else
                        f"Checked {checked} cached frame(s) — {failed} would "
                        f"be refused by the chart. Rebuild the cache to clear "
                        f"them."),
        })

    def _do_replay(self) -> None:
        self.job.begin(ACTION_REPLAY, 2)
        self.job.step("Finding the most recent chart request…")
        traces = self.service.diagnostics.recent(1)
        if not traces:
            self.job.finish({"message": "No chart request has been made yet "
                                        "in this session — load a chart first."})
            return
        trace = traces[0]
        self.job.step(f"Replaying #{trace['id']} ({trace['symbol']} "
                      f"{trace['timeframe']}) against every provider…")
        result = mdreplay.replay(self.service, trace).as_dict()
        with self._lock:
            self._last_replay = result
        for answer in result.get("answers", []):
            verdict = (answer.get("skipped") or
                       ("answered" if answer["ok"] else
                        f"failed — {answer.get('error', '')[:80]}"))
            self.job.note(f"  {answer['provider']}: {verdict} "
                          f"({answer['bars']} bars, "
                          f"{answer['duration_ms']:.0f} ms)")
        answered = sum(1 for a in result.get("answers", []) if a["ok"])
        self.job.finish({
            "replay": result,
            "message": f"{answered} provider(s) answered. They "
                       f"{'agreed' if result.get('agreed') else 'DISAGREED'} "
                       f"about the prices.",
        })

    def _do_benchmark(self) -> None:
        """One identical request per usable provider, timed and validated.

        Reimplemented here rather than importing `scripts/marketdata_benchmark`
        because `scripts/` is a developer tool, not an importable package — and
        because this needs to answer to a progress bar, which a CLI script has
        no notion of. The measurement is the same one: same request, same
        validation, same quality score.
        """
        adapters = [a for a in self.registry.adapters
                    if a.can_spend_request()[0]
                    and a.supports_interval(PROBE_TIMEFRAME)]
        self.job.begin(ACTION_BENCHMARK, max(1, len(adapters)))
        if not adapters:
            self.job.finish({"message": "No provider is currently usable, so "
                                        "there is nothing to benchmark."})
            return
        now = datetime.now(timezone.utc)
        request = HistoryRequest(PROBE_SYMBOL, PROBE_TIMEFRAME,
                                 now - timedelta(days=PROBE_DAYS), now)
        rows = []
        for adapter in adapters:
            name = adapter.provider_name
            if self.job.cancelled:
                self.job.stopped({"results": rows,
                                  "message": f"Stopped after {len(rows)} "
                                             f"provider(s)."})
                return
            self.job.step(f"Timing {name}…")
            t0 = _time.perf_counter()
            try:
                frame = adapter.fetch_history(request, now=now)
            except Exception as exc:  # noqa: BLE001 — a benchmark must finish
                rows.append({"provider": name, "ok": False,
                             "error": f"{type(exc).__name__}: {exc}"[:160]})
                self.job.note(f"  {name}: failed — {type(exc).__name__}")
                continue
            elapsed = _ms(t0)
            score = None
            if not frame.empty:
                _, report = dq.validate_history(frame, PROBE_TIMEFRAME, now=now,
                                                context=f"benchmark {name}")
                score = round(report.score, 1)
            rows.append({"provider": name, "ok": True, "bars": len(frame),
                         "latency_ms": round(elapsed, 1), "quality": score})
            self.job.note(f"  {name}: {len(frame)} bars in {elapsed:.0f} ms "
                          f"(quality {score})")
        ok = [r for r in rows if r.get("ok")]
        fastest = min(ok, key=lambda r: r["latency_ms"], default=None)
        self.job.finish({
            "results": rows,
            "fastest": fastest["provider"] if fastest else None,
            "message": (f"{len(ok)} of {len(rows)} provider(s) answered. "
                        f"Fastest: {fastest['provider']} "
                        f"({fastest['latency_ms']:.0f} ms)." if fastest else
                        "No provider answered."),
        })

    def _do_diagnostics(self) -> None:
        self.job.begin(ACTION_DIAGNOSTICS, 1)
        self.job.step("Collecting the health snapshot…")
        health = self.service.health()
        providers = health.get("providers", [])
        healthy = sum(1 for p in providers if p.get("available"))
        self.job.finish({
            "health": health,
            "message": f"{healthy} of {len(providers)} provider(s) are "
                       f"currently usable. "
                       f"{health['requests']['total_requests']} history "
                       f"request(s) this session, "
                       f"{health['requests']['success_rate'] * 100:.1f}% served.",
        })

    def _do_capabilities(self) -> None:
        """Re-measure each provider's real history depth.

        Imports `discovery` lazily: it is the only consumer, and a maintenance
        action nobody runs should not add its import cost to every launch.
        """
        from optionspilot.data import capabilities as caps
        from optionspilot.data import discovery

        adapters = [a for a in self.registry.adapters if a.can_spend_request()[0]]
        self.job.begin(ACTION_CAPABILITIES, max(1, len(adapters)))
        if not adapters:
            self.job.finish({"message": "No provider can currently be probed."})
            return
        findings = {}
        drifts: list[str] = []
        for adapter in adapters:
            name = adapter.provider_name
            # Checked BETWEEN providers, not inside `discover()`. One provider's
            # probe is a few dozen requests over ~30 seconds, which is a
            # reasonable worst-case wait; interrupting mid-provider would need
            # the flag threaded through `discovery`, and killing the thread
            # would leave that provider's counters half-written.
            if self.job.cancelled:
                self.job.stopped({
                    "findings": findings, "drift": drifts,
                    "message": f"Stopped after probing {len(findings)} "
                               f"provider(s). Nothing was changed — the "
                               f"shipped capability table is unaffected."})
                return
            self.job.step(f"Probing {name} — this spends real requests…")
            result = discovery.discover(adapter, PROBE_SYMBOL)
            findings[name] = result.as_dict()
            for line in discovery.drift(result, adapter.capabilities):
                drifts.append(f"{name}: {line}")
                self.job.note(f"  ⚠ {name}: {line}")
            self.job.note(f"  {name}: probed {len(result.intervals)} interval(s) "
                          f"with {result.requests_spent} request(s)")
        self.job.finish({
            "findings": findings,
            "drift": drifts,
            "shipped_table": caps.__name__,
            "message": ("The shipped capability table matches what every "
                        "provider actually serves." if not drifts else
                        f"{len(drifts)} discrepancy(ies) found — the shipped "
                        f"table promises more than a provider serves. This is "
                        f"worth reporting."),
        })

    # ── QA mode ──────────────────────────────────────────────────────────────
    #
    # Every method below is unreachable unless `market_data.qa_mode` is true:
    # the endpoints 404 first. They are guarded here as well, because a second
    # caller (a script, a test, a future endpoint) must not be able to reach
    # them by forgetting the check — the gate belongs with the capability, not
    # only with one route to it.

    def qa_state(self) -> dict:
        if not self.config.qa_mode:
            return {"enabled": False}
        return {
            "enabled": True,
            "faults": FAULTS.armed(),
            "available_faults": [{"kind": k, "description": FAULT_TEXT[k]}
                                 for k in ALL_FAULTS],
            "providers": [a.provider_name for a in self.registry.adapters],
        }

    def qa_arm(self, provider: str, kind: str, *, count: int | None = None,
               seconds: float = 2.0) -> dict:
        if not self.config.qa_mode:
            return {"error": "QA mode is not enabled"}
        if self.registry.get(provider) is None:
            return {"error": f"no market-data provider named {provider!r}"}
        try:
            armed = FAULTS.arm(provider, kind, count=count, seconds=seconds)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True, "armed": armed, "qa": self.qa_state()}

    def qa_clear(self, provider: str | None = None) -> dict:
        if not self.config.qa_mode:
            return {"error": "QA mode is not enabled"}
        cleared = (FAULTS.clear_all() if provider is None
                   else int(FAULTS.clear(provider)))
        return {"ok": True, "cleared": cleared, "qa": self.qa_state()}

    def qa_trip_breaker(self, provider: str, seconds: float = 30.0) -> dict:
        """Force a provider out of rotation immediately, to watch failover."""
        if not self.config.qa_mode:
            return {"error": "QA mode is not enabled"}
        if self.registry.get(provider) is None:
            return {"error": f"no market-data provider named {provider!r}"}
        self.registry.force_open(provider, seconds)
        return {"ok": True, "provider": provider, "seconds": seconds}

    def qa_reset_health(self) -> dict:
        """Clear every breaker and failure streak, keeping lifetime totals."""
        if not self.config.qa_mode:
            return {"error": "QA mode is not enabled"}
        self.registry.reset()
        return {"ok": True, "message": "Every circuit breaker was reset."}

    def qa_corrupt_cache(self) -> dict:
        """Prove the corruption-recovery path, on a COPY of the real cache.

        Deliberately never damages the user's cache. The recovery path being
        demonstrated (`CandleCache._open` → integrity failure → quarantine →
        fresh file) is identical whichever file it runs on, and running it on a
        scratch copy means a maintainer can watch it work without gambling the
        history they actually have. That is not a weaker test — it is the same
        test with the blast radius removed.
        """
        if not self.config.qa_mode:
            return {"error": "QA mode is not enabled"}
        from optionspilot.data.cache import CandleCache

        cache = self.service.cache
        source = getattr(cache, "_path", None) if cache else None
        if source is None or not Path(source).exists():
            return {"error": "there is no cache file to copy"}
        scratch = Path(source).with_suffix(".qa-corrupt-test")
        try:
            shutil.copy2(source, scratch)
            with open(scratch, "r+b") as handle:
                handle.seek(0)
                handle.write(b"NOT-A-SQLITE-FILE")     # break the header
            probe = CandleCache(scratch, allow_rebuild=True)
            stats = probe.stats()
            probe.close()
            return {
                "ok": True,
                "recovered": True,
                "bars_after_recovery": stats["bars"],
                "message": "The damaged copy was quarantined and a fresh cache "
                           "was created automatically — which is exactly what "
                           "would happen to the real one. Your cache was not "
                           "touched.",
            }
        except Exception as exc:  # noqa: BLE001 — a drill must not 500
            return {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(scratch) + suffix).unlink(missing_ok=True)
            for leftover in scratch.parent.glob(scratch.name + ".corrupt-*"):
                leftover.unlink(missing_ok=True)

    # ── persistence ──────────────────────────────────────────────────────────

    def state(self) -> dict:
        """The live-editable settings, in the shape they are persisted."""
        return {
            "version": 1,
            "ordering_mode": self.config.ordering(),
            "order": self.registry.order(),
            "providers": {a.provider_name: {"enabled": bool(a.config.enabled)}
                          for a in self.registry.adapters},
        }

    def _persist(self) -> None:
        """Write the control state. Never raises.

        A settings change that cannot be saved has still been APPLIED — the
        adapters were updated before this ran — so failing loudly here would
        report an error for a change the user can see took effect. It is logged
        and the change simply does not survive a restart.
        """
        if self._state_path is None:
            return
        import json
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state(), indent=2), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as exc:
            log.error("could not persist market-data control state: %s", exc)


def load_control_state(path: str | Path | None) -> dict:
    """Read persisted control-centre settings, tolerantly.

    Used by the composition root BEFORE the registry is built, to fold stored
    choices into the `MarketDataConfig` the providers are constructed from. A
    missing or malformed file yields `{}`, which is the shipped default — a
    corrupt settings file must cost a user their preferences, never their app.
    """
    if path is None:
        return {}
    import json
    file = Path(path)
    if not file.exists():
        return {}
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("market-data control state at %s is unreadable (%s) — "
                    "using defaults", file, exc)
        return {}
    return doc if isinstance(doc, dict) else {}


def apply_control_state(config: MarketDataConfig,
                        state: dict | None) -> MarketDataConfig:
    """Fold persisted control-centre choices into a startup `MarketDataConfig`.

    Called by the composition root before the providers are constructed, so a
    provider switched off last session is off from the very first request —
    not merely from whenever the settings page is next opened.

    **`config.yaml` loses to the stored state, deliberately.** The stored state
    is a record of what the user did in the app, and `config.yaml`'s
    market-data section is shipped empty; a file that says nothing cannot
    meaningfully override a click. The exception is anything the file states
    that the control centre never writes (timeouts, breaker thresholds,
    credentials) — those keys are untouched here and keep working exactly as
    documented.

    Every field is validated on the way in. This file lives in a directory the
    user can open, so a hand-edited `ordering_mode: "fastest"` must degrade to
    the default rather than reach `config.ordering()` and be silently ignored
    somewhere less obvious.
    """
    if not state:
        return config
    changes: dict = {}
    mode = state.get("ordering_mode")
    if isinstance(mode, str) and mode.strip().lower() in ORDERING_MODES:
        changes["ordering_mode"] = mode.strip().lower()
        # The legacy `dynamic_ranking: false` spelling would pin the chain to
        # `static` regardless of the stored mode (see `config.ordering()`), so
        # a stored mode has to clear it or choosing "Dynamic" in the UI would
        # appear to do nothing on the next launch.
        changes["dynamic_ranking"] = True
    order = state.get("order")
    if isinstance(order, list) and order:
        changes["provider_order"] = tuple(str(n) for n in order if str(n))
    if changes:
        config = replace(config, **changes)
    # `isinstance` rather than `or {}`: a hand-edited file can put a LIST here,
    # and `[].items()` is an AttributeError raised from the composition root —
    # i.e. the app refusing to start because a preferences file was edited
    # badly. That is precisely the failure this function promises not to be.
    providers = state.get("providers")
    if isinstance(providers, dict):
        for name, entry in providers.items():
            if isinstance(entry, dict) and "enabled" in entry:
                config = config.with_provider(str(name),
                                              enabled=bool(entry["enabled"]))
    return config


def _ms(t0: float) -> float:
    return (_time.perf_counter() - t0) * 1000.0


__all__ = ["MarketDataControl", "MaintenanceJob", "load_control_state",
           "apply_control_state",
           "ALL_ACTIONS", "ACTION_INFO", "TEST_TEXT", "TEST_ACTION",
           "TEST_CONNECTED", "TEST_MISSING_KEY", "TEST_AUTH_FAILED",
           "TEST_RATE_LIMITED", "TEST_UNREACHABLE", "TEST_NETWORK",
           "TEST_UNEXPECTED", "TEST_DISABLED", "TEST_UNKNOWN",
           "PROBE_TIMEFRAME", "PROBE_DAYS"]
