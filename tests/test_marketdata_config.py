"""Market-data configuration — parsing, validation, and actually taking effect.

The point of `data/config.py` is that retuning or disabling a provider requires
no code change. A test that only checked the dataclass parsed would miss the
half that matters, so each block here asserts the setting reaches the thing it
is supposed to control: the registry's membership, the adapter's timeout, the
service's retry loop, the breaker's threshold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.config.settings import AppConfig, load_config
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import ProviderUnavailable
from optionspilot.data.config import (
    CacheConfig, MarketDataConfig, ProviderConfig,
)
from optionspilot.data.registry import ProviderRegistry, default_registry
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=timezone.utc)


def bars(n=20, timeframe=Timeframe.M5):
    """Bars ending at the service's INJECTED now. Using the real wall clock
    here would stamp every bar in the future relative to the frozen clock, and
    validation would (correctly) throw them all away."""
    return frame(n, timeframe, end=NOW)


def _req(service, symbol="SPY", tf=Timeframe.M5):
    return service.get_history(symbol, tf, NOW - timedelta(hours=2), NOW)


class TestParsing:
    def test_an_empty_config_is_the_shipped_defaults(self):
        cfg = MarketDataConfig.from_mapping(None)
        assert cfg.dynamic_ranking is True
        assert cfg.for_provider("yahoo") == ProviderConfig()
        assert cfg.for_provider("anything-at-all").enabled is True

    def test_provider_settings_round_trip(self):
        cfg = MarketDataConfig.from_mapping({
            "providers": {"stooq": {"enabled": False, "priority": 5,
                                    "timeout": 30.0, "max_attempts": 4}},
            "dynamic_ranking": False,
        })
        stooq = cfg.for_provider("stooq")
        assert (stooq.enabled, stooq.priority, stooq.timeout) == (False, 5, 30.0)
        assert stooq.max_attempts == 4
        assert cfg.dynamic_ranking is False
        # An unmentioned provider still gets defaults.
        assert cfg.for_provider("yahoo").enabled is True

    def test_an_unknown_top_level_key_is_rejected(self):
        """A silently-ignored typo would do nothing for the life of the
        install; a startup failure is the kinder outcome."""
        with pytest.raises(ValueError, match="unknown market_data settings"):
            MarketDataConfig.from_mapping({"dynamic_rankng": True})

    def test_an_unknown_provider_key_is_rejected_and_names_the_provider(self):
        with pytest.raises(ValueError, match="market_data.providers.yahoo"):
            MarketDataConfig.from_mapping({"providers": {"yahoo": {"timout": 5}}})

    def test_an_unknown_cache_key_is_rejected(self):
        with pytest.raises(ValueError, match="market_data.cache"):
            MarketDataConfig.from_mapping({"cache": {"retention": 5}})

    def test_settings_for_an_unregistered_provider_are_accepted(self):
        """So a config can pin a future provider's settings before its adapter
        ships, and survive a downgrade."""
        cfg = MarketDataConfig.from_mapping(
            {"providers": {"polygon": {"priority": 5}}})
        assert cfg.for_provider("polygon").priority == 5

    def test_with_provider_returns_a_copy(self):
        base = MarketDataConfig()
        tuned = base.with_provider("yahoo", timeout=99.0)
        assert tuned.for_provider("yahoo").timeout == 99.0
        assert base.for_provider("yahoo").timeout is None

    def test_as_dict_is_json_serializable(self):
        import json

        json.dumps(MarketDataConfig.from_mapping(
            {"providers": {"yahoo": {"timeout": 5.0}}}).as_dict())


class TestPydanticMirror:
    """`config/settings.py` validates the YAML; `data/config.py` is the runtime
    shape. The keys must line up or the translation silently drops settings."""

    def test_the_default_app_config_has_a_market_data_section(self):
        cfg = AppConfig()
        assert cfg.market_data.dynamic_ranking is True
        assert cfg.market_data.providers == {}

    def test_every_pydantic_key_exists_on_the_runtime_dataclass(self):
        runtime = MarketDataConfig.from_mapping(
            AppConfig().market_data.model_dump())
        assert runtime == MarketDataConfig()

    def test_provider_keys_line_up_exactly(self):
        from optionspilot.config.settings import MarketDataProviderConfig

        assert set(MarketDataProviderConfig.model_fields) == \
            set(ProviderConfig.__dataclass_fields__)

    def test_cache_keys_line_up_exactly(self):
        from optionspilot.config.settings import MarketDataCacheConfig

        assert set(MarketDataCacheConfig.model_fields) == \
            set(CacheConfig.__dataclass_fields__)

    def test_yaml_flows_through_to_the_runtime_config(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "market_data:\n"
            "  dynamic_ranking: false\n"
            "  providers:\n"
            "    stooq:\n"
            "      enabled: false\n"
            "      breaker_threshold: 7\n", encoding="utf-8")
        app = load_config(path, environ={})
        runtime = MarketDataConfig.from_mapping(app.market_data.model_dump())
        assert runtime.dynamic_ranking is False
        assert runtime.for_provider("stooq").enabled is False
        assert runtime.for_provider("stooq").breaker_policy().threshold == 7

    def test_a_bad_yaml_value_fails_at_startup(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("market_data:\n  memo_max_entries: 0\n", encoding="utf-8")
        with pytest.raises(Exception):
            load_config(path, environ={})


class TestConfigTakesEffect:
    def test_a_disabled_provider_is_never_constructed(self):
        registry = default_registry(config=MarketDataConfig.from_mapping(
            {"providers": {"stooq": {"enabled": False}}}))
        assert [a.provider_name for a in registry.adapters] == ["yahoo", "yfinance"]

    def test_a_priority_override_reorders_the_shipped_chain(self):
        registry = default_registry(config=MarketDataConfig.from_mapping(
            {"providers": {"stooq": {"priority": 1}}}))
        assert registry.adapters[0].provider_name == "stooq"

    def test_a_timeout_override_reaches_the_adapter(self):
        registry = default_registry(config=MarketDataConfig.from_mapping(
            {"providers": {"yahoo": {"timeout": 3.5}}}))
        assert registry.get("yahoo").timeout == 3.5

    def test_an_unset_timeout_keeps_the_adapters_own_default(self):
        """Providers genuinely differ in speed — flattening them to one global
        number would either cut Stooq off early or let Yahoo hang."""
        registry = default_registry()
        assert registry.get("stooq").timeout == \
            registry.get("stooq").default_timeout
        assert registry.get("stooq").timeout != registry.get("yahoo").timeout

    def test_max_attempts_controls_the_service_retry_loop(self):
        adapter = ScriptedAdapter("flaky", [ProviderUnavailable("down")],
                                  config=ProviderConfig(max_attempts=3,
                                                        retry_backoff=0.0))
        service = MarketDataService(ProviderRegistry([adapter]),
                                    clock=lambda: NOW)
        _req(service)
        assert len(adapter.calls) == 3

    def test_max_attempts_of_one_fails_over_immediately(self):
        flaky = ScriptedAdapter("flaky", [ProviderUnavailable("down")],
                                priority=10,
                                config=ProviderConfig(max_attempts=1))
        good = ScriptedAdapter("good", [bars(20)], priority=20)
        service = MarketDataService(ProviderRegistry([flaky, good]),
                                    clock=lambda: NOW)
        assert _req(service).ok
        assert len(flaky.calls) == 1

    def test_a_custom_breaker_threshold_is_honoured(self):
        adapter = ScriptedAdapter("bad", [ProviderUnavailable("down")],
                                  config=ProviderConfig(breaker_threshold=1,
                                                        max_attempts=1))
        registry = ProviderRegistry([adapter])
        service = MarketDataService(registry, clock=lambda: NOW)
        _req(service)
        assert registry.candidates("SPY", Timeframe.M5) == []

    def test_min_quality_score_rejects_a_usable_but_poor_frame(self):
        """A frame can pass `usable` and still be worse than a config says is
        acceptable — an operator's lever on top of the validator's verdict.

        The defect used here is an isolated price spike: it is a structurally
        valid bar, so `validate_candles` keeps it, but `validate_history` drops
        it as a bad print and docks the score. That is precisely the band this
        setting governs — "usable, but not good enough for me".
        """
        dirty = bars(40)
        for col in ("open", "high", "low", "close"):
            dirty.iloc[20, dirty.columns.get_loc(col)] *= 50     # a bad print
        strict = ScriptedAdapter("strict", [dirty], priority=10,
                                 config=ProviderConfig(min_quality_score=100.0,
                                                       max_attempts=1))
        clean = ScriptedAdapter("clean", [bars(40)], priority=20)
        service = MarketDataService(ProviderRegistry([strict, clean]),
                                    clock=lambda: NOW)
        assert _req(service).provider == "clean"

    def test_the_default_min_quality_score_accepts_a_repairable_frame(self):
        """The same frame, with the setting left alone, is served rather than
        failed over — repairable data is still data."""
        dirty = bars(40)
        for col in ("open", "high", "low", "close"):
            dirty.iloc[20, dirty.columns.get_loc(col)] *= 50
        adapter = ScriptedAdapter("only", [dirty])
        service = MarketDataService(ProviderRegistry([adapter]),
                                    clock=lambda: NOW)
        result = _req(service)
        assert result.ok and result.provider == "only"
        assert result.report.score < 100.0

    def test_the_memo_cap_is_configurable(self):
        service = MarketDataService(
            ProviderRegistry([ScriptedAdapter("a", [bars(20)])]),
            config=MarketDataConfig(memo_max_entries=3), clock=lambda: NOW)
        for i in range(10):
            _req(service, symbol=f"SYM{i}")
        assert len(service._mem) <= 3

    def test_disabling_the_cache_stops_a_cache_being_built(self, tmp_path):
        service = MarketDataService(
            ProviderRegistry([ScriptedAdapter("a", [bars(20)])]),
            cache_db=tmp_path / "c.db",
            config=MarketDataConfig(cache=CacheConfig(enabled=False)),
            clock=lambda: NOW)
        assert service.cache is None
        assert _req(service).ok        # still serves, just without a cache

    def test_health_reports_the_config_in_force(self):
        """So a diagnostics export answers 'what settings was this running
        with?' without asking the user to find their config file."""
        service = MarketDataService(ProviderRegistry([ScriptedAdapter("a")]),
                                    config=MarketDataConfig(dynamic_ranking=False),
                                    clock=lambda: NOW)
        assert service.health()["config"]["dynamic_ranking"] is False
