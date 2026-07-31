"""Market-data configuration — every operational knob, declared in one place.

The goal of this module is a specific, testable one:

> **Adding or retuning a provider must not require editing provider code.**

Before it, a provider's timeout lived in its adapter, its retry count was a
module constant in `service.py`, its breaker thresholds were module constants in
`registry.py`, and its ordering was a class attribute. Four files to answer
"why is Stooq being skipped?", and nothing a user could change without a source
edit. `MarketDataConfig` collects all of it, and `build_provider` applies it.

## Why dataclasses here and pydantic in `config/settings.py`

`data/` may import only `core/` (enforced by `tests/test_architecture.py`), so
it cannot depend on the pydantic config layer. That is the right constraint:
the data subsystem should be usable from a script or a test with a literal
config object and no YAML at all. So this module holds the *runtime* shape —
frozen dataclasses, like `capabilities.ProviderCapabilities` alongside it — and
`config/settings.py` holds a pydantic mirror that validates `config.yaml` and is
translated here by `MarketDataConfig.from_mapping`. One translation point, and
the schema is validated exactly once, at startup, by the same machinery as
every other section.

## Unknown keys are an error, unknown providers are not

`from_mapping` rejects a key it does not recognise, because a silently-ignored
`timout: 30` is worse than a startup failure. But it happily accepts settings
for a provider that is not registered in this build: that is how a user pins a
future provider's configuration before the adapter ships, and how a config file
survives a downgrade.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace

from optionspilot.data.health import BreakerPolicy
from optionspilot.data.ratelimit import RateLimitPolicy

#: What a redacted key looks like everywhere it is displayed.
REDACTED = "***"

# ── ordering modes (V0.5.7) ──────────────────────────────────────────────────
#
# `dynamic_ranking: true|false` answered "may health reorder the chain?" and
# nothing else, which turned out to be two questions wearing one coat. A user
# who sets their own provider order wants it respected — but not so rigidly
# that a dead provider stays at the head of the chain. Splitting it into three
# named modes lets that be said, and each one is one sentence long:
#
#   static   Ask providers in exactly the configured order. Health is ignored
#            for ORDERING; a provider that is unavailable is still skipped,
#            because asking a provider with no API key is not an ordering
#            decision. This reproduces the pre-V0.5.3 behaviour exactly and is
#            the setting to reach for if a ranking ever misbehaves.
#
#   hybrid   The configured order stands, and a provider only loses its place
#            when it is genuinely FAILING — not when it is merely slower.
#            Mechanically this is the full rank formula minus its latency term
#            (see `health.rank(include_latency=False)`), which is the only term
#            that reorders two healthy providers.
#
#   dynamic  Full health ranking: latency, recent failure rate, consecutive
#            failures, breaker history, data quality and budget pressure all
#            move a provider off its configured anchor. The shipped default,
#            and what V0.5.3 introduced.
ORDER_STATIC = "static"
ORDER_HYBRID = "hybrid"
ORDER_DYNAMIC = "dynamic"
ORDERING_MODES = (ORDER_STATIC, ORDER_HYBRID, ORDER_DYNAMIC)

#: Plain-English wording, shared by the settings page and the text report so
#: the explanation a user reads is the explanation the export carries.
ORDERING_TEXT: dict[str, str] = {
    ORDER_STATIC: "Always ask providers in the order below. Their measured "
                  "speed and reliability are ignored; only providers that "
                  "cannot answer at all are skipped.",
    ORDER_HYBRID: "Use the order below, but move a provider down when it is "
                  "actually failing. A provider that is merely slower keeps "
                  "its place.",
    ORDER_DYNAMIC: "Pick the fastest healthy provider for each request. Your "
                   "order is the starting point, and measured latency, "
                   "failures and remaining quota move providers around it.",
}

#: Requests per provider for RETRYABLE failures. Deliberately small: a second
#: provider is a better answer than a third attempt at a sick one, and a user is
#: watching a chart.
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF = 0.4
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Everything tunable about one provider.

    `priority=None` means "keep the adapter's own declared priority", which is
    what makes the shipped chain work with an empty config file.
    """

    enabled: bool = True
    priority: int | None = None
    #: Seconds before an outbound request is abandoned. None keeps the
    #: adapter's own `default_timeout`, which is per-provider for a reason —
    #: Stooq's CSV endpoint is reliably slower than Yahoo's JSON one, and
    #: flattening both to a single global number would either cut Stooq off
    #: early or let Yahoo hang.
    timeout: float | None = None
    #: Attempts per provider for retryable failures (1 = no retry).
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    #: Minimum seconds between outbound requests from this provider.
    min_request_interval: float | None = None
    # circuit breaker
    breaker_threshold: int = 3
    breaker_base_cooldown: float = 15.0
    breaker_max_cooldown: float = 300.0
    #: Frames scoring below this are refused and the service fails over. 0
    #: accepts anything `quality.validate_history` considers usable at all.
    min_quality_score: float = 0.0

    # ── credentials (V0.5.4) ─────────────────────────────────────────────────
    #: The API key, for providers that need one. Keyless providers ignore it.
    #:
    #: Putting a secret in `config.yaml` is supported but NOT the recommended
    #: path — that file is the one users paste into forum posts and attach to
    #: bug reports. `api_key_env` names an environment variable to read
    #: instead, and every adapter also falls back to a conventional
    #: `<PROVIDER>_API_KEY` variable, so the common case needs no config at all.
    api_key: str | None = None
    #: Environment variable to read the key from. Takes precedence over
    #: `api_key`, so a checked-in config can be overridden per machine.
    api_key_env: str | None = None
    # ── budget (V0.5.4) ──────────────────────────────────────────────────────
    #: Overrides for the provider's published limits. None keeps the adapter's
    #: declared policy — worth overriding only on a paid plan, where the free
    #: tier's numbers no longer apply.
    requests_per_minute: int | None = None
    requests_per_day: int | None = None

    def breaker_policy(self) -> BreakerPolicy:
        return BreakerPolicy(threshold=self.breaker_threshold,
                             base_cooldown=self.breaker_base_cooldown,
                             max_cooldown=self.breaker_max_cooldown)

    def rate_limit_policy(self, default: RateLimitPolicy) -> RateLimitPolicy:
        """The adapter's published limits, with any configured override."""
        return RateLimitPolicy(
            per_minute=(self.requests_per_minute
                        if self.requests_per_minute is not None
                        else default.per_minute),
            per_day=(self.requests_per_day if self.requests_per_day is not None
                     else default.per_day),
        )

    def resolve_api_key(self, *fallback_env: str,
                        environ: dict | None = None) -> str | None:
        """The effective key, or None. Environment beats the config file.

        Order: `api_key_env` (explicit), then the adapter's conventional
        variables, then `api_key` from the config file. Environment first
        because it is the safer place for a secret and the one an operator
        reaches for to override a shipped config.

        Whitespace-only values count as absent — a `FINNHUB_API_KEY=` left in a
        shell profile is a missing key, not a key of length zero, and treating
        it as present produces a confusing auth failure instead of the accurate
        "no API key configured".
        """
        env = os.environ if environ is None else environ
        names = ([self.api_key_env] if self.api_key_env else []) + list(fallback_env)
        for name in names:
            value = (env.get(name) or "").strip()
            if value:
                return value
        return (self.api_key or "").strip() or None

    def as_dict(self, *, redact: bool = True) -> dict:
        """Settings as a plain dict. **The API key is redacted by default.**

        This dict reaches the diagnostics endpoint, the JSON export and the
        text report — all of which a user is explicitly invited to attach to a
        public bug report. Defaulting to redaction means a leak requires
        someone to opt in, rather than requiring every future caller to
        remember. `redact=False` exists only for round-trip tests.
        """
        data = asdict(self)
        if redact and data.get("api_key"):
            data["api_key"] = REDACTED
        return data


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Disk-cache policy. Not per-provider: the cache is keyed by symbol and
    timeframe, and bars from two providers for the same bar are the same bar."""

    enabled: bool = True
    #: Drop cached bars older than this. None keeps everything, which is the
    #: shipped default — history is small and a deep cache is the last tier
    #: before a blank chart.
    retention_days: int | None = None
    #: Log a warning once the file passes this size, so a runaway cache is
    #: visible in diagnostics rather than only in Explorer.
    warn_bytes: int = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    """The whole subsystem's configuration."""

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    cache: CacheConfig = field(default_factory=CacheConfig)
    #: Rank providers by measured health rather than by static priority alone.
    #: Off reproduces the pre-V0.5.3 fixed order exactly, which is what makes
    #: this a safe thing to turn off if a ranking ever misbehaves.
    #:
    #: **Superseded by `ordering_mode`** and kept because it is a documented
    #: `config.yaml` key that existing installs have set. `False` still means
    #: `static`; `True` means "whatever `ordering_mode` says". Removing it
    #: would silently re-enable ranking for anyone who had turned it off,
    #: which is the one group who explicitly did not want it.
    dynamic_ranking: bool = True
    #: How the provider chain is ordered — see the ORDER_* block above.
    ordering_mode: str = ORDER_DYNAMIC
    #: The user's explicit provider order, best first, set from Settings.
    #: Empty means "use each adapter's shipped priority". Names not registered
    #: in this build are ignored (a config must survive a downgrade), and
    #: registered names missing from the list keep their shipped priority and
    #: sort after the listed ones.
    provider_order: tuple[str, ...] = ()
    #: Cap on the in-memory memo (one entry per symbol/timeframe/session).
    memo_max_entries: int = 400
    #: Emit one structured line per history request to `logs/data.log`.
    structured_logging: bool = True
    #: Let providers discover their own capabilities in the background and
    #: persist the result. Off by default: the shipped depth table is MEASURED
    #: (`scripts/marketdata_probe.py`) and a live probe costs real requests.
    capability_discovery: bool = False
    capability_refresh_days: int = 30
    #: Persist each metered provider's daily request count, so restarting the
    #: app cannot mint a fresh allowance it does not have. Set by the
    #: composition root to `<data>/quota.json`; None keeps counts in memory.
    quota_state_path: str | None = None
    #: Where API keys pasted into Settings are stored. Set by the composition
    #: root to `<data>/credentials.json`; None keeps keys in memory only (which
    #: is what every test wants — a test that wrote a key to a real path would
    #: be a test that can leak one). See `data/credentials.py`.
    credentials_path: str | None = None
    #: Where live control-centre choices (enable/disable, order, ordering mode)
    #: are persisted. Set by the composition root to `<data>/marketdata.json`.
    control_state_path: str | None = None
    #: Expose the developer QA panel: temporarily disable providers, trip
    #: breakers, simulate outages/latency/rate limits, corrupt a cache copy.
    #: **Off by default and off in every shipped build.** The endpoints that
    #: reach it return 404 when this is False, so a normal install cannot arm
    #: a fault even by guessing a URL. See `data/faults.py`.
    qa_mode: bool = False

    def ordering(self) -> str:
        """The effective ordering mode.

        `dynamic_ranking: false` is honoured as `static` regardless of
        `ordering_mode`, because it is the older and more explicit statement:
        someone who turned ranking off in `config.yaml` did so to stop the
        chain being reordered, and a newer field defaulting to `dynamic` must
        not quietly overrule them.
        """
        if not self.dynamic_ranking:
            return ORDER_STATIC
        mode = (self.ordering_mode or ORDER_DYNAMIC).strip().lower()
        return mode if mode in ORDERING_MODES else ORDER_DYNAMIC

    def for_provider(self, name: str) -> ProviderConfig:
        """Config for `name`, or the defaults when the file says nothing."""
        return self.providers.get(name, _DEFAULT_PROVIDER)

    def with_provider(self, name: str, **changes) -> "MarketDataConfig":
        """A copy with one provider's settings adjusted — the shape tests and
        the benchmark script want, without mutating a frozen value."""
        providers = dict(self.providers)
        providers[name] = replace(self.for_provider(name), **changes)
        return replace(self, providers=providers)

    def as_dict(self, *, redact: bool = True) -> dict:
        """The whole configuration, **with API keys redacted by default** —
        this is what the diagnostics export carries."""
        return {
            "providers": {k: v.as_dict(redact=redact)
                          for k, v in self.providers.items()},
            "cache": asdict(self.cache),
            "dynamic_ranking": self.dynamic_ranking,
            "ordering_mode": self.ordering_mode,
            "effective_ordering": self.ordering(),
            "provider_order": list(self.provider_order),
            "memo_max_entries": self.memo_max_entries,
            "structured_logging": self.structured_logging,
            "capability_discovery": self.capability_discovery,
            "capability_refresh_days": self.capability_refresh_days,
            "quota_state_path": self.quota_state_path,
            # PATHS, not contents. `credentials_path` naming a file is not a
            # secret; what the file holds is, and nothing here can reach it.
            "credentials_path": self.credentials_path,
            "control_state_path": self.control_state_path,
            "qa_mode": self.qa_mode,
        }

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_mapping(cls, data: dict | None) -> "MarketDataConfig":
        """Build from a plain dict (what `config/settings.py` hands over).

        Raises ValueError on an unrecognised key so a typo fails at startup
        rather than silently doing nothing for the life of the install.
        """
        if not data:
            return cls()
        top = dict(data)
        raw_providers = top.pop("providers", None) or {}
        raw_cache = top.pop("cache", None) or {}

        unknown = set(top) - _TOP_FIELDS
        if unknown:
            raise ValueError(
                f"unknown market_data settings: {sorted(unknown)} "
                f"(allowed: {sorted(_TOP_FIELDS | {'providers', 'cache'})})")
        unknown_cache = set(raw_cache) - _CACHE_FIELDS
        if unknown_cache:
            raise ValueError(
                f"unknown market_data.cache settings: {sorted(unknown_cache)} "
                f"(allowed: {sorted(_CACHE_FIELDS)})")

        providers = {}
        for name, raw in raw_providers.items():
            raw = raw or {}
            unknown_provider = set(raw) - _PROVIDER_FIELDS
            if unknown_provider:
                raise ValueError(
                    f"unknown market_data.providers.{name} settings: "
                    f"{sorted(unknown_provider)} "
                    f"(allowed: {sorted(_PROVIDER_FIELDS)})")
            providers[name] = ProviderConfig(**raw)

        # `provider_order` arrives from YAML/JSON as a list and must be stored
        # as a tuple: this dataclass is frozen, and a frozen dataclass holding
        # a mutable list is only *nominally* immutable — anything holding a
        # reference could reorder the provider chain by appending to it.
        if "provider_order" in top:
            top["provider_order"] = tuple(top["provider_order"] or ())
        if "ordering_mode" in top and top["ordering_mode"] is not None:
            mode = str(top["ordering_mode"]).strip().lower()
            if mode not in ORDERING_MODES:
                raise ValueError(
                    f"market_data.ordering_mode must be one of "
                    f"{sorted(ORDERING_MODES)}, got {top['ordering_mode']!r}")
            top["ordering_mode"] = mode

        return cls(providers=providers, cache=CacheConfig(**raw_cache), **top)


_DEFAULT_PROVIDER = ProviderConfig()
_PROVIDER_FIELDS = frozenset(ProviderConfig.__dataclass_fields__)
_CACHE_FIELDS = frozenset(CacheConfig.__dataclass_fields__)
_TOP_FIELDS = frozenset(MarketDataConfig.__dataclass_fields__) - {"providers",
                                                                  "cache"}

DEFAULT_CONFIG = MarketDataConfig()

__all__ = ["MarketDataConfig", "ProviderConfig", "CacheConfig", "DEFAULT_CONFIG",
           "DEFAULT_MAX_ATTEMPTS", "DEFAULT_RETRY_BACKOFF", "DEFAULT_TIMEOUT",
           "ORDER_STATIC", "ORDER_HYBRID", "ORDER_DYNAMIC", "ORDERING_MODES",
           "ORDERING_TEXT", "REDACTED"]
