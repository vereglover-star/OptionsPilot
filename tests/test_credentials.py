"""API-key storage — what it keeps, what it shows, and what it must never leak.

The tests are grouped by the promise each one holds the module to:

  TestMasking            a key is never displayed in full
  TestStorage            a key survives a restart, and whitespace does not
  TestResolutionOrder    environment beats stored beats config, everywhere
  TestOverlay            a stored key reaches the adapter that must send it
  TestNoLeak             no export path can be made to carry a plaintext key

`TestNoLeak` is the one that matters most: every other test would still pass if
a future change added a plaintext field to some payload, and that field would
reach the diagnostics export a user is explicitly invited to paste into a
public bug report.
"""

from __future__ import annotations

import json

import pytest

from optionspilot.data import report as mdreport
from optionspilot.data.config import MarketDataConfig, ProviderConfig
from optionspilot.data.credentials import (
    SOURCE_CONFIG, SOURCE_ENV, SOURCE_NONE, SOURCE_STORED, CredentialStore,
    mask,
)
from optionspilot.data.registry import default_registry

SECRET = "sk_live_9f3a7c1d4e8b2a6f"


class TestMasking:
    def test_only_the_last_four_characters_survive(self):
        masked = mask(SECRET)
        assert masked == "•" * 8 + "2a6f"
        assert SECRET[:-4] not in masked
        # Enough to confirm WHICH key is installed, not enough to use it.
        assert sum(c != "•" for c in masked) == 4

    def test_a_short_key_is_masked_completely(self):
        """Four visible characters of a six-character secret is not a mask."""
        assert mask("abc123") == "•" * 8
        assert "abc" not in mask("abc123")

    def test_nothing_is_a_blank_not_a_row_of_dots(self):
        """An empty mask lets the UI distinguish "no key" from "a key I am
        hiding" without a second flag."""
        assert mask(None) == ""
        assert mask("") == ""
        assert mask("   ") == ""


class TestStorage:
    def test_a_key_round_trips_through_the_file(self, tmp_path):
        path = tmp_path / "credentials.json"
        CredentialStore(path).set_key("finnhub", SECRET)
        assert CredentialStore(path).resolve("finnhub") == SECRET

    def test_pasted_whitespace_is_stripped(self, tmp_path):
        """Users paste from a web page and bring a newline with them; a key
        with an invisible newline fails auth in a way nothing can explain."""
        store = CredentialStore(tmp_path / "c.json")
        store.set_key("finnhub", f"  {SECRET}\n")
        assert store.resolve("finnhub") == SECRET

    def test_an_all_whitespace_key_is_a_removal_not_an_empty_key(self, tmp_path):
        store = CredentialStore(tmp_path / "c.json")
        store.set_key("finnhub", SECRET)
        store.set_key("finnhub", "   ")
        assert store.resolve("finnhub") is None
        assert store.has_key("finnhub") is False

    def test_replacing_a_key_forgets_the_old_keys_success_time(self, tmp_path):
        """Inheriting it would claim the NEW key worked at a moment it did not
        exist — which is exactly the fact a user checks when auth starts
        failing."""
        store = CredentialStore(tmp_path / "c.json")
        store.set_key("finnhub", SECRET)
        store.note_success("finnhub")
        assert store.last_success_at("finnhub") is not None
        store.set_key("finnhub", "another_key_entirely_1234")
        assert store.last_success_at("finnhub") is None

    def test_removing_reports_whether_there_was_anything_to_remove(self, tmp_path):
        store = CredentialStore(tmp_path / "c.json")
        assert store.remove_key("finnhub") is False
        store.set_key("finnhub", SECRET)
        assert store.remove_key("finnhub") is True

    def test_a_corrupt_file_degrades_to_no_keys_never_to_a_crash(self, tmp_path):
        path = tmp_path / "credentials.json"
        path.write_text("{not json at all", encoding="utf-8")
        store = CredentialStore(path)
        assert store.providers() == []
        # And it must still be usable — a bad file costs the stored keys, not
        # the ability to store new ones.
        store.set_key("finnhub", SECRET)
        assert CredentialStore(path).resolve("finnhub") == SECRET

    def test_a_none_path_keeps_keys_in_memory_only(self, tmp_path):
        """What every test wants: a store that cannot leak a key onto a disk."""
        store = CredentialStore(None)
        store.set_key("finnhub", SECRET)
        assert store.resolve("finnhub") == SECRET
        assert not list(tmp_path.iterdir())


class TestResolutionOrder:
    """environment → stored → config.yaml → missing, with no exceptions.

    Each case asserts BOTH the effective key (via the resolver the adapter
    actually uses) and the reported source, because a page that names the
    wrong source is worse than one that names none.
    """

    ENV = ("FINNHUB_API_KEY",)

    def _both(self, store, config, environ):
        effective = config.resolve_api_key(*self.ENV, environ=environ)
        source = store.source_for("finnhub", config, self.ENV, environ=environ)
        return effective, source

    def test_environment_beats_a_stored_key(self):
        store = CredentialStore(None)
        store.set_key("finnhub", "stored_key_aaaaaaaa")
        config = ProviderConfig(api_key="stored_key_aaaaaaaa")
        effective, source = self._both(
            store, config, {"FINNHUB_API_KEY": "env_key_bbbbbbbb"})
        assert effective == "env_key_bbbbbbbb"
        assert source == SOURCE_ENV

    def test_a_stored_key_beats_config_yaml(self):
        """Pasting a key into the app is a later, more deliberate act than a
        config file that may have been checked in months ago."""
        store = CredentialStore(None)
        store.set_key("finnhub", "stored_key_aaaaaaaa")
        config = MarketDataConfig().with_provider("finnhub",
                                                  api_key="yaml_key_cccccccc")
        overlaid = store.overlay(config).for_provider("finnhub")
        assert overlaid.resolve_api_key(*self.ENV, environ={}) == "stored_key_aaaaaaaa"
        assert store.source_for("finnhub", overlaid, self.ENV,
                                environ={}) == SOURCE_STORED

    def test_config_yaml_is_used_when_nothing_else_is_set(self):
        store = CredentialStore(None)
        config = ProviderConfig(api_key="yaml_key_cccccccc")
        effective, source = self._both(store, config, {})
        assert effective == "yaml_key_cccccccc"
        assert source == SOURCE_CONFIG

    def test_nothing_anywhere_is_reported_as_none(self):
        effective, source = self._both(CredentialStore(None), ProviderConfig(), {})
        assert effective is None
        assert source == SOURCE_NONE

    def test_an_empty_environment_variable_counts_as_absent(self):
        """A `FINNHUB_API_KEY=` left in a shell profile is a missing key, not a
        key of length zero — treating it as present produces a confusing auth
        failure instead of the accurate "no API key configured"."""
        store = CredentialStore(None)
        store.set_key("finnhub", "stored_key_aaaaaaaa")
        config = ProviderConfig(api_key="stored_key_aaaaaaaa")
        effective, source = self._both(store, config, {"FINNHUB_API_KEY": "  "})
        assert effective == "stored_key_aaaaaaaa"
        assert source == SOURCE_STORED

    def test_an_explicit_api_key_env_outranks_the_conventional_one(self):
        store = CredentialStore(None)
        config = ProviderConfig(api_key_env="MY_OWN_VAR")
        effective, source = self._both(
            store, config,
            {"MY_OWN_VAR": "explicit_dddddddd", "FINNHUB_API_KEY": "conv_eeee"})
        assert effective == "explicit_dddddddd"
        assert source == SOURCE_ENV


class TestOverlay:
    def test_a_stored_key_reaches_the_constructed_adapter(self, tmp_path):
        path = tmp_path / "credentials.json"
        CredentialStore(path).set_key("finnhub", SECRET)
        registry = default_registry(
            environ={}, config=MarketDataConfig(credentials_path=str(path)))
        finnhub = registry.get("finnhub")
        assert finnhub.api_key == SECRET
        # And the provider is no longer benched for a missing key.
        assert finnhub.monitor.disabled_reason is None
        assert finnhub.monitor.available() is True

    def test_with_no_stored_key_the_provider_explains_its_own_absence(self, tmp_path):
        registry = default_registry(
            environ={},
            config=MarketDataConfig(credentials_path=str(tmp_path / "none.json")))
        finnhub = registry.get("finnhub")
        assert finnhub.api_key is None
        assert finnhub.monitor.health_state()[0] == "missing_key"
        assert finnhub.signup_url

    def test_the_overlay_does_not_mutate_the_config_it_is_given(self):
        """`MarketDataConfig` is frozen for a reason; an overlay that mutated
        in place would leak one install's key into a shared default."""
        store = CredentialStore(None)
        store.set_key("finnhub", SECRET)
        original = MarketDataConfig()
        overlaid = store.overlay(original)
        assert original.for_provider("finnhub").api_key is None
        assert overlaid.for_provider("finnhub").api_key == SECRET


class TestNoLeak:
    """Nothing a user can be invited to share may carry a plaintext key.

    The list of payloads here is the list of things this repo actively tells
    people to attach to a bug report. If a new export is added, it belongs in
    this test before it ships.
    """

    @pytest.fixture
    def registry(self, tmp_path):
        path = tmp_path / "credentials.json"
        CredentialStore(path).set_key("finnhub", SECRET)
        return default_registry(environ={},
                                config=MarketDataConfig(credentials_path=str(path)))

    def test_the_health_report_carries_only_a_redaction(self, registry):
        blob = json.dumps(registry.health_report())
        assert SECRET not in blob
        assert "***" in blob

    def test_the_text_report_carries_no_key(self, registry):
        from optionspilot.data.service import MarketDataService
        health = MarketDataService(registry).health()
        assert SECRET not in json.dumps(health)
        assert SECRET not in mdreport.render(health)

    def test_the_config_export_redacts_by_default(self):
        config = MarketDataConfig().with_provider("finnhub", api_key=SECRET)
        assert SECRET not in json.dumps(config.as_dict())
        # The opt-out exists only for round-trip tests, and must be explicit.
        assert config.as_dict(redact=False)["providers"]["finnhub"]["api_key"] \
            == SECRET

    def test_describe_has_no_plaintext_escape_hatch(self, tmp_path):
        """Unlike `ProviderConfig.as_dict`, there is deliberately no
        `redact=False` here — no caller wants plaintext, and the parameter
        would only ever be an invitation to leak one."""
        store = CredentialStore(None)
        store.set_key("finnhub", SECRET)
        described = store.describe("finnhub")
        assert SECRET not in json.dumps(described)
        with pytest.raises(TypeError):
            store.describe("finnhub", redact=False)
