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

from dataclasses import asdict, dataclass, field, replace

from optionspilot.data.health import BreakerPolicy

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

    def breaker_policy(self) -> BreakerPolicy:
        return BreakerPolicy(threshold=self.breaker_threshold,
                             base_cooldown=self.breaker_base_cooldown,
                             max_cooldown=self.breaker_max_cooldown)

    def as_dict(self) -> dict:
        return asdict(self)


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
    dynamic_ranking: bool = True
    #: Cap on the in-memory memo (one entry per symbol/timeframe/session).
    memo_max_entries: int = 400
    #: Emit one structured line per history request to `logs/data.log`.
    structured_logging: bool = True
    #: Let providers discover their own capabilities in the background and
    #: persist the result. Off by default: the shipped depth table is MEASURED
    #: (`scripts/marketdata_probe.py`) and a live probe costs real requests.
    capability_discovery: bool = False
    capability_refresh_days: int = 30

    def for_provider(self, name: str) -> ProviderConfig:
        """Config for `name`, or the defaults when the file says nothing."""
        return self.providers.get(name, _DEFAULT_PROVIDER)

    def with_provider(self, name: str, **changes) -> "MarketDataConfig":
        """A copy with one provider's settings adjusted — the shape tests and
        the benchmark script want, without mutating a frozen value."""
        providers = dict(self.providers)
        providers[name] = replace(self.for_provider(name), **changes)
        return replace(self, providers=providers)

    def as_dict(self) -> dict:
        return {
            "providers": {k: v.as_dict() for k, v in self.providers.items()},
            "cache": asdict(self.cache),
            "dynamic_ranking": self.dynamic_ranking,
            "memo_max_entries": self.memo_max_entries,
            "structured_logging": self.structured_logging,
            "capability_discovery": self.capability_discovery,
            "capability_refresh_days": self.capability_refresh_days,
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

        return cls(providers=providers, cache=CacheConfig(**raw_cache), **top)


_DEFAULT_PROVIDER = ProviderConfig()
_PROVIDER_FIELDS = frozenset(ProviderConfig.__dataclass_fields__)
_CACHE_FIELDS = frozenset(CacheConfig.__dataclass_fields__)
_TOP_FIELDS = frozenset(MarketDataConfig.__dataclass_fields__) - {"providers",
                                                                  "cache"}

DEFAULT_CONFIG = MarketDataConfig()

__all__ = ["MarketDataConfig", "ProviderConfig", "CacheConfig", "DEFAULT_CONFIG",
           "DEFAULT_MAX_ATTEMPTS", "DEFAULT_RETRY_BACKOFF", "DEFAULT_TIMEOUT"]
