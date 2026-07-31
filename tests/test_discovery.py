"""Capability discovery — measuring depth, persisting it, and reporting drift.

Everything here is offline: the "provider" is a scripted adapter with a known
cliff, so the binary search can be asserted exactly rather than approximately.

The property that matters most is the one about EMPTY: a window with no bars is
not a refusal. Conflating the two is the original sin the whole market-data
subsystem was rebuilt to avoid, and it would silently make every measurement
taken on a weekend wrong.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    HistoryAdapter, ProviderRangeError, ProviderUnavailable,
)
from optionspilot.data.capabilities import (
    IntervalSpec, ProviderCapabilities, YAHOO_CAPABILITIES,
)
from optionspilot.data.discovery import (
    CapabilityStore, DiscoveryResult, discover, drift, measure_depth,
    refresh_if_stale,
)
from tests.marketdata_helpers import frame

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class CliffAdapter(HistoryAdapter):
    """A provider that serves bars up to `cliff_days` back and refuses beyond.

    `empty_beyond` optionally makes a band of the range answer with zero bars
    without refusing — a holiday window — so the walk can be shown to continue
    through it.
    """

    provider_name = "cliff"
    provider_priority = 10
    capabilities = ProviderCapabilities(
        intervals={Timeframe.M5: IntervalSpec("5m", max_lookback_days=9999),
                   Timeframe.D1: IntervalSpec("1d")})

    def __init__(self, cliff_days: int | None = 59, empty_between=()):
        self.cliff_days = cliff_days
        self.empty_between = empty_between
        self.probes: list[int] = []
        super().__init__()

    def _fetch_native(self, symbol, spec, start, end, prepost):
        days = round((end - start).total_seconds() / 86400)
        self.probes.append(days)
        if self.cliff_days is not None and days > self.cliff_days:
            raise ProviderRangeError(f"only {self.cliff_days} days allowed")
        if any(lo <= days <= hi for lo, hi in self.empty_between):
            return pd.DataFrame()
        return frame(20, Timeframe.from_string(spec.native), end=NOW)


class TestMeasureDepth:
    def test_it_finds_the_exact_cliff(self):
        adapter = CliffAdapter(cliff_days=59)
        finding = measure_depth(adapter, "SPY", Timeframe.M5, NOW, pause=0)
        assert finding.served is True
        assert finding.max_lookback_days == 59

    def test_it_finds_a_different_cliff(self):
        finding = measure_depth(CliffAdapter(cliff_days=729), "SPY",
                                Timeframe.M5, NOW, pause=0)
        assert finding.max_lookback_days == 729

    def test_an_unlimited_provider_reports_no_limit(self):
        finding = measure_depth(CliffAdapter(cliff_days=None), "SPY",
                                Timeframe.M5, NOW, pause=0)
        assert finding.served is True
        assert finding.max_lookback_days is None

    def test_an_empty_window_is_not_a_refusal(self):
        """A weekend or holiday must not be read as the start of history."""
        adapter = CliffAdapter(cliff_days=59, empty_between=((3, 15),))
        finding = measure_depth(adapter, "SPY", Timeframe.M5, NOW, pause=0)
        assert finding.max_lookback_days == 59      # walked straight through

    def test_a_provider_that_serves_nothing_is_reported_as_such(self):
        finding = measure_depth(CliffAdapter(cliff_days=0), "SPY",
                                Timeframe.M5, NOW, pause=0)
        assert finding.served is False
        assert finding.max_lookback_days is None

    def test_the_binary_search_costs_far_fewer_probes_than_a_linear_walk(self):
        adapter = CliffAdapter(cliff_days=59)
        finding = measure_depth(adapter, "SPY", Timeframe.M5, NOW, pause=0)
        assert finding.probes == len(adapter.probes)
        assert finding.probes < 20          # not 59 requests

    def test_an_unsupported_interval_reports_unserved_without_probing(self):
        adapter = CliffAdapter()
        finding = measure_depth(adapter, "SPY", Timeframe.W1, NOW, pause=0)
        assert finding.served is False
        assert adapter.probes == []

    def test_a_transport_error_terminates_the_walk_rather_than_crashing(self):
        class Broken(CliffAdapter):
            def _fetch_native(self, symbol, spec, start, end, prepost):
                raise ProviderUnavailable("network down")

        finding = measure_depth(Broken(), "SPY", Timeframe.M5, NOW, pause=0)
        assert finding.served is False


class TestDiscover:
    def test_it_measures_every_supported_interval(self):
        result = discover(CliffAdapter(), "SPY", now=NOW, pause=0)
        assert set(result.intervals) == {"5m", "1d"}
        assert result.requests_spent > 0
        assert result.provider == "cliff"

    def test_a_result_round_trips_through_json(self):
        result = discover(CliffAdapter(), "SPY", now=NOW, pause=0)
        restored = DiscoveryResult.from_dict(
            json.loads(json.dumps(result.as_dict())))
        assert restored.provider == result.provider
        assert restored.intervals["5m"].max_lookback_days == \
            result.intervals["5m"].max_lookback_days


class TestDrift:
    def _result(self, **intervals) -> DiscoveryResult:
        from optionspilot.data.discovery import IntervalFinding

        return DiscoveryResult(
            provider="yahoo", symbol="SPY", measured_at=NOW,
            intervals={k: IntervalFinding(k, v, served=v is not False)
                       for k, v in intervals.items()})

    def test_a_table_within_what_is_served_reports_no_drift(self):
        result = self._result(**{"5m": 59, "1h": 729, "1d": None})
        assert drift(result, YAHOO_CAPABILITIES) == []

    def test_an_over_promising_table_is_flagged(self):
        """One-directional on purpose: a table promising MORE than is served
        produces guaranteed-422 requests on every scroll."""
        result = self._result(**{"5m": 30})
        problems = drift(result, YAHOO_CAPABILITIES)
        assert len(problems) == 1
        assert "table says 59d but only 30d" in problems[0]

    def test_a_conservative_table_is_not_flagged(self):
        result = self._result(**{"5m": 120})
        assert drift(result, YAHOO_CAPABILITIES) == []

    def test_unlimited_in_the_table_but_limited_in_practice_is_flagged(self):
        result = self._result(**{"1d": 500})
        problems = drift(result, YAHOO_CAPABILITIES)
        assert "table says unlimited but only 500d" in problems[0]

    def test_a_provider_serving_nothing_is_flagged(self):
        from optionspilot.data.discovery import IntervalFinding

        result = DiscoveryResult(
            provider="yahoo", symbol="SPY", measured_at=NOW,
            intervals={"5m": IntervalFinding("5m", None, served=False)})
        assert "served nothing" in drift(result, YAHOO_CAPABILITIES)[0]

    def test_the_shipped_table_matches_a_measurement_of_itself(self):
        """A self-consistency check: measuring a provider whose capabilities
        ARE the shipped table must produce no drift."""
        result = self._result(**{
            str(tf): YAHOO_CAPABILITIES.max_lookback_days(tf)
            for tf in YAHOO_CAPABILITIES.intervals})
        assert drift(result, YAHOO_CAPABILITIES) == []


class TestCapabilityStore:
    def test_it_persists_and_reloads(self, tmp_path):
        path = tmp_path / "capabilities.json"
        result = discover(CliffAdapter(), "SPY", now=NOW, pause=0)
        CapabilityStore(path).save(result)

        reloaded = CapabilityStore(path).get("cliff")
        assert reloaded is not None
        assert reloaded.intervals["5m"].max_lookback_days == 59
        assert reloaded.measured_at == NOW

    def test_an_unmeasured_provider_is_stale(self, tmp_path):
        store = CapabilityStore(tmp_path / "c.json")
        assert store.is_stale("never-measured", 30) is True

    def test_a_fresh_measurement_is_not_stale(self, tmp_path):
        store = CapabilityStore(tmp_path / "c.json")
        store.save(discover(CliffAdapter(), "SPY", now=NOW, pause=0))
        assert store.is_stale("cliff", 30, now=NOW + timedelta(days=5)) is False

    def test_an_aged_measurement_is_stale(self, tmp_path):
        store = CapabilityStore(tmp_path / "c.json")
        store.save(discover(CliffAdapter(), "SPY", now=NOW, pause=0))
        assert store.is_stale("cliff", 30, now=NOW + timedelta(days=31)) is True

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        store = CapabilityStore(tmp_path / "nope" / "c.json")
        assert store.providers() == []
        assert store.get("anything") is None

    def test_a_corrupt_file_degrades_to_unmeasured(self, tmp_path):
        """A broken discovery cache must mean 'we have not measured this yet',
        never a failed launch."""
        path = tmp_path / "c.json"
        path.write_text("{not json at all", encoding="utf-8")
        store = CapabilityStore(path)
        assert store.get("cliff") is None
        assert store.is_stale("cliff", 30) is True

    def test_a_malformed_provider_entry_is_ignored(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"version": 1,
                                    "providers": {"cliff": {"nonsense": True}}}),
                        encoding="utf-8")
        assert CapabilityStore(path).get("cliff") is None

    def test_saving_is_atomic(self, tmp_path):
        """A crash mid-write must never leave a half-written document that the
        next launch then refuses to parse."""
        path = tmp_path / "c.json"
        store = CapabilityStore(path)
        store.save(discover(CliffAdapter(), "SPY", now=NOW, pause=0))
        assert json.loads(path.read_text(encoding="utf-8"))["providers"]
        assert not list(tmp_path.glob("*.tmp"))

    def test_two_providers_coexist(self, tmp_path):
        path = tmp_path / "c.json"
        store = CapabilityStore(path)
        store.save(discover(CliffAdapter(), "SPY", now=NOW, pause=0))
        other = discover(CliffAdapter(cliff_days=100), "SPY", now=NOW, pause=0)
        other.provider = "other"
        store.save(other)
        assert CapabilityStore(path).providers() == ["cliff", "other"]


class TestRefreshIfStale:
    def test_it_measures_and_persists_when_stale(self, tmp_path):
        store = CapabilityStore(tmp_path / "c.json")
        result = refresh_if_stale(CliffAdapter(), store, refresh_days=30,
                                  now=NOW, pause=0)
        assert result is not None
        assert store.get("cliff") is not None

    def test_it_does_nothing_when_the_stored_result_is_current(self, tmp_path):
        store = CapabilityStore(tmp_path / "c.json")
        refresh_if_stale(CliffAdapter(), store, refresh_days=30, now=NOW, pause=0)
        adapter = CliffAdapter()
        assert refresh_if_stale(adapter, store, refresh_days=30,
                                now=NOW + timedelta(days=1), pause=0) is None
        assert adapter.probes == []          # no upstream requests spent

    def test_a_failing_probe_never_raises(self, tmp_path):
        """Discovery is a convenience — it must not be able to stop the app
        serving charts."""
        class Exploding(CliffAdapter):
            def _fetch_native(self, *a, **kw):
                raise RuntimeError("catastrophe")

        store = CapabilityStore(tmp_path / "c.json")
        # `measure_depth` swallows per-probe errors, so this reports "served
        # nothing" rather than propagating.
        result = refresh_if_stale(Exploding(), store, refresh_days=30,
                                  now=NOW, pause=0)
        assert result is not None
        assert result.intervals["5m"].served is False

    def test_drift_is_logged_not_applied(self, tmp_path, caplog):
        """Discovery reports; it does not rewrite the shipped table. The table
        is a deliberate floor sitting one day inside each measured cliff."""
        class Shallow(CliffAdapter):
            capabilities = YAHOO_CAPABILITIES

            def _fetch_native(self, symbol, spec, start, end, prepost):
                days = round((end - start).total_seconds() / 86400)
                self.probes.append(days)
                if days > 10:
                    raise ProviderRangeError("only 10 days")
                return frame(20, Timeframe.M5, end=NOW)

        adapter = Shallow()
        before = adapter.capabilities.max_lookback_days(Timeframe.M5)
        store = CapabilityStore(tmp_path / "c.json")
        with caplog.at_level("WARNING"):
            refresh_if_stale(adapter, store, refresh_days=30, now=NOW, pause=0)
        assert "capability drift" in caplog.text
        assert adapter.capabilities.max_lookback_days(Timeframe.M5) == before
