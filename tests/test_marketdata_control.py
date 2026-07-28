"""The Market Data Control Centre — administration over a live provider stack.

Every test here asks the same underlying question in a different shape: **did
the change actually take effect on the running system, or only in a payload?**
That distinction is the one this repo has been caught by before (see
`CLAUDE.md`, "Adding a risk/validation gate function is not the same as it
being active"), so a setter is never asserted on its return value alone — it is
asserted on `registry.candidates`, on `adapter.api_key`, on the order requests
are actually tried in.

Groups:

  TestDashboard        the payload explains the system, and leaks nothing
  TestKeys             a pasted key reaches the adapter and survives a restart
  TestEnableDisable    switching a provider off removes it from selection
  TestOrdering         Move Up/Down, Reset, and the three ordering modes
  TestTestConnection   every outcome code, driven offline
  TestMaintenance      each action runs, reports, and cannot run twice at once
  TestRecommendations  advice fires on the conditions it claims to
  TestPersistence      choices survive a restart, and a corrupt file does not
                       cost more than the choices
  TestQaGate           QA methods refuse unless QA mode is on
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import (
    ProviderAuthError, ProviderEntitlementError, ProviderQuotaExceeded,
    ProviderTimeout, ProviderUnavailable,
)
from optionspilot.data.config import (
    ORDER_DYNAMIC, ORDER_HYBRID, ORDER_STATIC, MarketDataConfig,
)
from optionspilot.data.control import (
    ACTION_BENCHMARK, ACTION_CLEAR_CACHE, ACTION_DIAGNOSTICS,
    ACTION_REBUILD_CACHE, ACTION_VALIDATE, ACTION_VERIFY_CACHE,
    TEST_AUTH_FAILED, TEST_CONNECTED, TEST_DISABLED, TEST_MISSING_KEY,
    TEST_NETWORK, TEST_PREMIUM_REQUIRED, TEST_RATE_LIMITED, TEST_UNEXPECTED,
    TEST_UNKNOWN, TEST_UNREACHABLE, MarketDataControl, apply_control_state,
    load_control_state,
)
from optionspilot.data.credentials import CredentialStore
from optionspilot.data.ratelimit import RateLimitPolicy
from optionspilot.data.registry import ProviderRegistry, default_registry
from optionspilot.data.service import MarketDataService
from tests.marketdata_helpers import ScriptedAdapter, frame

SECRET = "sk_test_1234567890abcdef"


def wait_for_job(control, timeout: float = 20.0) -> dict:
    """Block until the single maintenance slot is free. Real threads, real
    completion — polling a job that never finishes should fail the test by
    timing out, not by hanging the suite."""
    deadline = time.monotonic() + timeout
    while control.job.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not control.job.running, "the maintenance job never finished"
    return control.job.as_dict()


@pytest.fixture
def scripted(tmp_path):
    """A control plane over three scripted providers and a real cache file.

    Scripted rather than real so nothing touches a socket, and a real cache so
    the maintenance actions operate on something with rows in it.
    """
    def build(scripts=None, config=None):
        scripts = scripts or {}
        adapters = [
            ScriptedAdapter("alpha", scripts.get("alpha"), priority=10),
            ScriptedAdapter("beta", scripts.get("beta"), priority=20),
            ScriptedAdapter("gamma", scripts.get("gamma"), priority=30),
        ]
        config = config or MarketDataConfig(
            credentials_path=str(tmp_path / "credentials.json"),
            control_state_path=str(tmp_path / "marketdata.json"))
        registry = ProviderRegistry(adapters, config=config,
                                    credentials=CredentialStore(
                                        config.credentials_path))
        service = MarketDataService(registry, cache_db=tmp_path / "cache.db",
                                    config=config)
        return MarketDataControl(service, config=config,
                                 credentials=registry.credentials,
                                 state_path=config.control_state_path,
                                 environ={})
    return build


@pytest.fixture
def shipped(tmp_path):
    """A control plane over the REAL shipped chain, with no network calls made.

    Constructing the adapters costs nothing (no I/O in `__init__`), so this is
    the right fixture for anything about credentials, ordering or the keyed
    providers specifically.
    """
    config = MarketDataConfig(
        credentials_path=str(tmp_path / "credentials.json"),
        control_state_path=str(tmp_path / "marketdata.json"))
    registry = default_registry(environ={}, config=config)
    service = MarketDataService(registry, config=config)
    return MarketDataControl(service, config=config,
                             credentials=registry.credentials,
                             state_path=config.control_state_path, environ={})


class TestDashboard:
    def test_every_provider_appears_with_a_state_and_an_explanation(self, shipped):
        rows = shipped.dashboard()["providers"]
        assert {r["name"] for r in rows} >= {"yahoo", "yfinance", "finnhub"}
        for row in rows:
            # The pairing is the whole contract: a badge without a sentence
            # tells a user they have a problem without telling them what it is.
            assert row["health_state"]
            assert row["health_detail"]

    def test_a_keyless_provider_is_not_asked_for_a_key(self, shipped):
        yahoo = _row(shipped, "yahoo")
        assert yahoo["credential"]["required"] is False
        assert yahoo["feed"]["key"] == "No key needed"

    def test_a_keyed_provider_without_one_says_where_to_get_it(self, shipped):
        finnhub = _row(shipped, "finnhub")
        assert finnhub["health_state"] == "missing_key"
        assert finnhub["credential"]["signup_url"].startswith("https://")

    def test_the_failover_summary_does_not_count_yahoo_twice(self, shipped):
        """Yahoo and yfinance are two code paths to ONE upstream. Counting them
        as two sources overstates a keyless install's redundancy by exactly
        one, which is the single most misleading number this page could show."""
        failover = shipped.dashboard()["failover"]
        assert "yahoo" in failover["usable"]
        assert "yfinance" in failover["usable"]
        assert failover["independent_sources"].count("yahoo") == 1
        assert failover["independent_count"] < failover["usable_count"]

    def test_the_dashboard_never_carries_a_plaintext_key(self, shipped):
        shipped.set_api_key("finnhub", SECRET)
        assert SECRET not in json.dumps(shipped.dashboard())

    def test_the_ranking_shown_is_the_registrys_own(self, scripted):
        """The page must not compute an order — the chart and the settings
        screen disagreeing about which provider goes first would be a bug
        neither could be debugged from."""
        control = scripted()
        assert control.dashboard()["ranking"] == control.registry.ranking()

    def test_the_dashboard_costs_no_upstream_request(self, scripted):
        control = scripted()
        control.dashboard()
        control.dashboard()
        assert all(not a.calls for a in control.registry.adapters)


class TestKeys:
    def test_a_pasted_key_reaches_the_live_adapter(self, shipped):
        result = shipped.set_api_key("finnhub", SECRET)
        assert result["ok"] is True
        adapter = shipped.registry.get("finnhub")
        assert adapter.api_key == SECRET
        # Not merely stored — the provider is now selectable, without a restart.
        assert adapter.monitor.available() is True
        assert adapter in shipped.registry.candidates("SPY", Timeframe.D1)

    def test_the_response_returns_a_mask_never_the_key(self, shipped):
        result = shipped.set_api_key("finnhub", SECRET)
        assert SECRET not in json.dumps(result)
        assert result["masked_key"].endswith(SECRET[-4:])

    def test_removing_a_key_takes_the_provider_out_of_use(self, shipped):
        shipped.set_api_key("finnhub", SECRET)
        result = shipped.remove_api_key("finnhub")
        assert result["removed"] is True
        adapter = shipped.registry.get("finnhub")
        assert adapter.api_key is None
        assert adapter.monitor.health_state()[0] == "missing_key"
        assert adapter not in shipped.registry.candidates("SPY", Timeframe.D1)

    def test_a_new_key_clears_a_sticky_auth_failure(self, shipped):
        """The auth failure is sticky so a rejected key is not retried on every
        chart load — but the thing that made it sticky has just been replaced,
        and not clearing it would make a corrected key impossible to use
        without a restart."""
        adapter = shipped.registry.get("finnhub")
        shipped.set_api_key("finnhub", "wrong_key_aaaaaaaaaa")
        adapter.monitor.note_auth_failure()
        assert adapter.monitor.available() is False
        shipped.set_api_key("finnhub", SECRET)
        assert adapter.monitor.auth_failed is False
        assert adapter.monitor.available() is True

    def test_an_environment_variable_shadowing_a_stored_key_is_reported(self, tmp_path):
        """The worst possible bug report is "I typed my key in and it still
        says no key". The response says so explicitly instead."""
        config = MarketDataConfig(credentials_path=str(tmp_path / "c.json"))
        environ = {"FINNHUB_API_KEY": "env_key_zzzzzzzzzz"}
        registry = default_registry(environ=environ, config=config)
        control = MarketDataControl(MarketDataService(registry, config=config),
                                    config=config,
                                    credentials=registry.credentials,
                                    environ=environ)
        result = control.set_api_key("finnhub", SECRET)
        assert result["env_overrides"] is True
        assert "environment variable" in result["message"]
        assert control.registry.get("finnhub").api_key == "env_key_zzzzzzzzzz"

    def test_a_keyless_provider_refuses_a_key(self, shipped):
        assert "error" in shipped.set_api_key("yahoo", SECRET)

    def test_an_unknown_provider_is_an_error_not_a_crash(self, shipped):
        assert "error" in shipped.set_api_key("nope", SECRET)
        assert "error" in shipped.remove_api_key("nope")

    def test_an_empty_key_is_refused_rather_than_stored(self, shipped):
        assert "error" in shipped.set_api_key("finnhub", "   ")
        assert shipped.credentials.has_key("finnhub") is False


class TestEnableDisable:
    def test_disabling_removes_a_provider_from_selection(self, scripted):
        control = scripted()
        assert control.set_enabled("beta", False)["ok"] is True
        beta = control.registry.get("beta")
        assert beta.monitor.health_state()[0] == "disabled"
        assert beta not in control.registry.candidates("SPY", Timeframe.M5)

    def test_a_disabled_provider_is_still_listed_and_explains_itself(self, scripted):
        """The whole reason it stays constructed: an absent provider cannot be
        switched back on from a settings page that cannot show it."""
        control = scripted()
        control.set_enabled("beta", False)
        row = _row(control, "beta")
        assert row["enabled"] is False
        assert "turn it back on" in row["health_detail"]

    def test_re_enabling_restores_it_without_a_restart(self, scripted):
        control = scripted()
        control.set_enabled("beta", False)
        control.set_enabled("beta", True)
        assert control.registry.get("beta") in \
            control.registry.candidates("SPY", Timeframe.M5)

    def test_disabling_the_head_of_the_chain_promotes_the_next(self, scripted):
        control = scripted()
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "alpha"
        control.set_enabled("alpha", False)
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "beta"

    def test_an_unknown_provider_is_an_error(self, scripted):
        assert "error" in scripted().set_enabled("nope", False)


class TestOrdering:
    def test_move_up_changes_the_order_requests_are_tried_in(self, scripted):
        control = scripted()
        control.move("gamma", "up")
        assert control.registry.order() == ["alpha", "gamma", "beta"]
        assert [a.provider_name for a in
                control.registry.candidates("SPY", Timeframe.M5)] == \
            ["alpha", "gamma", "beta"]

    def test_move_down_is_the_mirror_image(self, scripted):
        control = scripted()
        control.move("alpha", "down")
        assert control.registry.order() == ["beta", "alpha", "gamma"]

    def test_moving_past_the_end_is_a_no_op_not_an_error(self, scripted):
        """A user pressing Up on the first row has not done anything wrong, and
        an error toast for it would be noise."""
        control = scripted()
        result = control.move("alpha", "up")
        assert result["ok"] is True and result["moved"] is False
        assert control.registry.order() == ["alpha", "beta", "gamma"]

    def test_priorities_stay_spaced_ten_apart(self, scripted):
        """The rank formula is calibrated so 10 points == 1 second of latency.
        Consecutive priorities would make a provider 100 ms slower outrank its
        neighbour — dynamic ordering would silently become almost-static the
        first time a user pressed Move Up."""
        control = scripted()
        control.move("gamma", "up")
        assert [a.provider_priority for a in
                sorted(control.registry.adapters,
                       key=lambda a: a.provider_priority)] == [10, 20, 30]

    def test_reset_restores_the_shipped_order_not_the_startup_one(self, scripted):
        control = scripted()
        control.set_order(["gamma", "beta", "alpha"])
        assert control.registry.order() == ["gamma", "beta", "alpha"]
        control.reset_order()
        assert control.registry.order() == ["alpha", "beta", "gamma"]

    def test_an_order_naming_an_unknown_provider_reorders_what_it_can(self, scripted):
        """A stored order from a build with more providers must not break this
        one — a config has to survive a downgrade."""
        control = scripted()
        control.set_order(["ghost", "gamma", "alpha"])
        assert control.registry.order() == ["gamma", "alpha", "beta"]

    def test_a_repeated_name_does_not_duplicate_a_provider(self, scripted):
        """Found by the V0.5.7 self-audit. A list naming one provider three
        times used to assign it three priorities (the last winning) and make
        `order()` report a chain LONGER than the registry, with the same
        provider drawn three times in the settings page. There is no way to
        type that in the UI, but this parses a file a user can hand-edit."""
        control = scripted()
        result = control.set_order(["alpha", "alpha", "alpha", "beta"])
        assert result["order"] == ["alpha", "beta", "gamma"]
        assert control.registry.order() == ["alpha", "beta", "gamma"]
        priorities = [a.provider_priority for a in control.registry.adapters]
        assert len(set(priorities)) == len(priorities)

    def test_a_non_string_entry_cannot_corrupt_the_chain(self, scripted):
        control = scripted()
        control.set_order([None, 5, {"a": 1}, "gamma"])
        assert control.registry.order() == ["gamma", "alpha", "beta"]

    def test_static_mode_ignores_measured_health(self, scripted):
        control = scripted()
        control.set_ordering_mode(ORDER_STATIC)
        alpha = control.registry.get("alpha")
        alpha.monitor.record_success(latency_ms=9000.0)      # ruinously slow
        alpha.monitor.record_failure("unavailable")
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "alpha"

    def test_dynamic_mode_demotes_a_slow_provider(self, scripted):
        control = scripted()
        control.set_ordering_mode(ORDER_DYNAMIC)
        control.registry.get("alpha").monitor.record_success(latency_ms=4000.0)
        control.registry.get("beta").monitor.record_success(latency_ms=50.0)
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "beta"

    def test_hybrid_mode_keeps_a_slow_but_working_provider_first(self, scripted):
        """The whole point of hybrid: your order stands unless something is
        actually broken. Latency alone must not reorder it."""
        control = scripted()
        control.set_ordering_mode(ORDER_HYBRID)
        control.registry.get("alpha").monitor.record_success(latency_ms=4000.0)
        control.registry.get("beta").monitor.record_success(latency_ms=50.0)
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "alpha"

    def test_hybrid_mode_still_demotes_a_failing_provider(self, scripted):
        control = scripted()
        control.set_ordering_mode(ORDER_HYBRID)
        alpha = control.registry.get("alpha")
        for _ in range(4):
            alpha.monitor.record_failure("unavailable")
        control.registry.get("beta").monitor.record_success(latency_ms=50.0)
        assert control.registry.candidates("SPY", Timeframe.M5)[0].provider_name \
            == "beta"

    def test_an_unknown_mode_is_refused(self, scripted):
        control = scripted()
        assert "error" in control.set_ordering_mode("fastest")
        assert control.config.ordering() == ORDER_DYNAMIC

    def test_choosing_a_mode_clears_the_legacy_dynamic_ranking_pin(self, scripted):
        """`dynamic_ranking: false` is the older spelling of "static" and wins
        over `ordering_mode`. Leaving it False would mean choosing Dynamic in
        the UI silently did nothing."""
        control = scripted(config=MarketDataConfig(dynamic_ranking=False))
        assert control.config.ordering() == ORDER_STATIC
        control.set_ordering_mode(ORDER_DYNAMIC)
        assert control.config.ordering() == ORDER_DYNAMIC
        assert control.registry.config.ordering() == ORDER_DYNAMIC


class TestTestConnection:
    def test_a_working_provider_reports_connected_with_real_numbers(self, scripted):
        control = scripted({"alpha": [frame(30, Timeframe.D1)]})
        result = control.test_connection("alpha")
        assert result["code"] == TEST_CONNECTED
        assert result["ok"] is True
        assert result["bars"] == 30
        assert result["quality"] is not None

    def test_the_test_records_a_real_request_on_the_monitor(self, scripted):
        """A green test beside a red provider would be nonsense — a successful
        test genuinely IS evidence the provider works."""
        control = scripted({"alpha": [frame(30, Timeframe.D1)]})
        control.test_connection("alpha")
        assert control.registry.get("alpha").monitor.successes == 1

    def test_the_test_writes_nothing_to_the_cache(self, scripted):
        """"Never store bad responses" — the test path does not go through the
        service, so it cannot store a good one either."""
        control = scripted({"alpha": [frame(30, Timeframe.D1)]})
        before = control.service.cache.stats()["bars"]
        control.test_connection("alpha")
        assert control.service.cache.stats()["bars"] == before

    @pytest.mark.parametrize("error,code", [
        (ProviderAuthError("rejected"), TEST_AUTH_FAILED),
        (ProviderQuotaExceeded("spent"), TEST_RATE_LIMITED),
        (ProviderTimeout("too slow"), TEST_UNREACHABLE),
        (ProviderUnavailable("no route"), TEST_NETWORK),
    ])
    def test_each_failure_maps_to_its_own_code(self, scripted, error, code):
        control = scripted({"alpha": [error]})
        result = control.test_connection("alpha")
        assert result["code"] == code
        assert result["ok"] is False
        # Every failure names a remedy — a test that says only "failed" has
        # told the user nothing they did not already know.
        assert result["action"]

    def test_an_empty_answer_for_a_liquid_symbol_is_a_failure(self, scripted):
        """SPY has never had three weeks without bars. An empty answer here is
        the provider being broken, not the market being closed."""
        control = scripted({"alpha": [None]})
        result = control.test_connection("alpha")
        assert result["code"] == TEST_UNEXPECTED
        assert "no bars" in result["detail"]

    def test_bars_that_fail_validation_are_a_failure_not_a_pass(self, scripted):
        """The failure most worth catching: a provider whose response format
        changed still opens a socket and still parses.

        Here it answers the daily probe with WEEKLY bars — which is a real
        thing free feeds do when an interval parameter stops being honoured.
        A test that stopped at "it answered" would pass this, and the chart
        would then silently mislabel its own axis.
        """
        control = scripted({"alpha": [frame(30, Timeframe.W1)]})
        result = control.test_connection("alpha")
        assert result["code"] == TEST_UNEXPECTED
        assert "wrong interval served" in result["detail"]

    def test_a_disabled_provider_says_so_rather_than_being_tested(self, scripted):
        control = scripted()
        control.set_enabled("alpha", False)
        assert control.test_connection("alpha")["code"] == TEST_DISABLED
        assert not control.registry.get("alpha").calls

    def test_a_provider_with_no_key_is_not_sent_a_doomed_request(self, shipped):
        result = shipped.test_connection("finnhub")
        assert result["code"] == TEST_MISSING_KEY
        assert result["signup_url"]

    def test_a_provider_out_of_budget_is_not_sent_a_request_it_cannot_afford(
            self, tmp_path):
        adapter = ScriptedAdapter("metered", [frame(30, Timeframe.D1)])
        adapter.quota.policy = RateLimitPolicy(per_day=2)
        adapter.quota.record(5)                    # already over
        registry = ProviderRegistry([adapter])
        control = MarketDataControl(MarketDataService(registry), environ={})
        assert control.test_connection("metered")["code"] == TEST_RATE_LIMITED
        assert not adapter.calls

    def test_an_unknown_provider_is_reported_not_raised(self, scripted):
        assert scripted().test_connection("nope")["code"] == TEST_UNKNOWN

    def test_a_403_is_reported_as_a_plan_problem_not_a_key_problem(self, scripted):
        """The Finnhub incident, at the level the user actually sees it.

        A provider that authenticates and then refuses the data must not
        produce "the provider rejected your API key" — that sentence is what
        sent a user to regenerate a key that was never wrong.
        """
        control = scripted({"alpha": [ProviderEntitlementError("no access")]})
        result = control.test_connection("alpha")
        assert result["code"] == TEST_PREMIUM_REQUIRED
        assert result["ok"] is False
        assert "valid" in result["message"]
        assert "do not regenerate it" in result["action"]

    def test_a_403_does_not_mark_the_provider_auth_failed(self, scripted):
        control = scripted({"alpha": [ProviderEntitlementError("no access")]})
        control.test_connection("alpha")
        monitor = control.registry.get("alpha").monitor
        assert monitor.auth_failed is False
        assert monitor.entitlement_failed is True

    def test_the_dashboard_row_says_a_paid_plan_is_needed(self, scripted):
        control = scripted({"alpha": [ProviderEntitlementError("no access")]})
        control.test_connection("alpha")
        row = _row(control, "alpha")
        assert row["health_state"] == "premium_required"
        assert "nothing to fix" in row["health_detail"]

    def test_a_premium_gated_provider_is_recommended_against_fixing(self, scripted):
        control = scripted({"alpha": [ProviderEntitlementError("no access")]})
        control.test_connection("alpha")
        rec = _rec(control, "needs a paid plan")
        assert rec["action"] == "premium"
        assert "no reason to regenerate the key" in rec["detail"]
        # Never phrased as an error — nothing is broken.
        assert rec["severity"] == "info"

    def test_the_last_result_is_remembered_for_the_dashboard(self, scripted):
        control = scripted({"alpha": [frame(30, Timeframe.D1)]})
        control.test_connection("alpha")
        assert _row(control, "alpha")["last_test"]["code"] == TEST_CONNECTED


class TestMaintenance:
    def test_clear_cache_empties_it_and_reports_how_much(self, scripted):
        control = scripted()
        control.service.cache.store("SPY", Timeframe.D1, frame(40, Timeframe.D1),
                                    provider="alpha")
        assert control.service.cache.stats()["bars"] == 40
        control.start_maintenance(ACTION_CLEAR_CACHE)
        job = wait_for_job(control)
        assert job["state"] == "done"
        assert job["summary"]["bars_removed"] == 40
        assert control.service.cache.stats()["bars"] == 0

    def test_rebuild_keeps_the_old_file_rather_than_deleting_it(self, scripted, tmp_path):
        control = scripted()
        control.service.cache.store("SPY", Timeframe.D1, frame(10, Timeframe.D1))
        control.start_maintenance(ACTION_REBUILD_CACHE)
        job = wait_for_job(control)
        assert job["state"] == "done"
        assert control.service.cache.stats()["bars"] == 0
        assert list(tmp_path.glob("cache.db.corrupt-*"))

    def test_verify_reports_a_sound_cache_as_sound(self, scripted):
        control = scripted()
        control.service.cache.store("SPY", Timeframe.D1, frame(10, Timeframe.D1))
        control.start_maintenance(ACTION_VERIFY_CACHE)
        job = wait_for_job(control)
        assert job["summary"]["ok"] is True
        assert job["summary"]["suspect_bars"] == 0

    def test_validate_walks_what_is_cached_and_scores_it(self, scripted):
        control = scripted()
        control.service.cache.store("SPY", Timeframe.D1, frame(60, Timeframe.D1))
        control.start_maintenance(ACTION_VALIDATE)
        job = wait_for_job(control)
        assert job["summary"]["frames_checked"] == 1
        assert job["summary"]["frames_failed"] == 0

    def test_benchmark_times_every_usable_provider(self, scripted):
        control = scripted({"alpha": [frame(30, Timeframe.D1)],
                            "beta": [frame(30, Timeframe.D1)],
                            "gamma": [ProviderUnavailable("down")]})
        control.start_maintenance(ACTION_BENCHMARK)
        job = wait_for_job(control)
        results = job["summary"]["results"]
        assert {r["provider"] for r in results} == {"alpha", "beta", "gamma"}
        # A failing provider must not abort the run — a benchmark that stops at
        # the first error measures nothing.
        assert [r["provider"] for r in results if not r["ok"]] == ["gamma"]
        assert job["summary"]["fastest"] in ("alpha", "beta")

    def test_diagnostics_summarises_without_touching_the_network(self, scripted):
        control = scripted()
        control.start_maintenance(ACTION_DIAGNOSTICS)
        job = wait_for_job(control)
        assert "provider(s) are currently usable" in job["summary"]["message"]
        assert all(not a.calls for a in control.registry.adapters)

    def test_an_unknown_action_is_refused(self, scripted):
        assert "error" in scripted().start_maintenance("delete_everything")

    def test_a_second_concurrent_action_is_refused_by_name(self, scripted):
        """The refusal has to say WHAT is in the way. "Re-measure capabilities"
        takes minutes, and a user who started it and then pressed "Clear chart
        cache" needs to know what they are waiting for."""
        control = scripted()
        control.job.begin(ACTION_VALIDATE, 10)      # pin the slot
        try:
            result = control.start_maintenance(ACTION_CLEAR_CACHE)
            assert "error" in result
            assert "Run validation" in result["error"]
            assert result["job"]["action"] == ACTION_VALIDATE
        finally:
            control.job.finish({})

    def test_progress_is_reported_and_ends_at_one(self, scripted):
        control = scripted()
        control.start_maintenance(ACTION_VERIFY_CACHE)
        job = wait_for_job(control)
        assert job["progress"] == 1.0
        assert job["elapsed_seconds"] >= 0

    def test_a_failing_action_reports_an_error_rather_than_crashing(self, scripted):
        """A maintenance failure is a RESULT to show the user, not a traceback
        on a background thread nobody sees."""
        control = scripted()
        control.service.cache = _ExplodingCache()
        control.start_maintenance(ACTION_CLEAR_CACHE)
        job = wait_for_job(control)
        assert job["state"] == "error"
        assert "Boom" in job["error"]

    def test_every_declared_action_is_runnable(self, scripted):
        """`ALL_ACTIONS` is what the UI renders buttons from, so an action
        listed but not dispatchable would be a button that 500s."""
        from optionspilot.data.control import ALL_ACTIONS
        control = scripted()
        for action in ALL_ACTIONS:
            if action in ("replay", "capabilities"):
                continue          # these spend upstream requests; covered above
            assert control.start_maintenance(action).get("ok") is True
            wait_for_job(control)

    def test_a_long_action_can_be_stopped(self, scripted):
        """Found by the V0.5.7 self-audit: "Re-measure capabilities" probes
        every provider at every depth and runs for MINUTES, holding the single
        job slot the whole time. Without a stop, a user who started it by
        mistake had no way out but a restart."""
        control = scripted({"alpha": [frame(30, Timeframe.D1)],
                            "beta": [frame(30, Timeframe.D1)],
                            "gamma": [frame(30, Timeframe.D1)]})
        control.job.begin(ACTION_BENCHMARK, 3)
        assert control.job.as_dict()["cancellable"] is True
        assert control.cancel_maintenance()["cancelled"] is True
        assert control.job.cancelled is True
        control.job.stopped({"message": "Stopped."})
        assert control.job.as_dict()["state"] == "cancelled"
        # And the slot is free again for the next action.
        assert control.start_maintenance(ACTION_VERIFY_CACHE).get("ok") is True
        wait_for_job(control)

    def test_cancelling_with_nothing_running_is_not_an_error(self, scripted):
        result = scripted().cancel_maintenance()
        assert result["ok"] is True and result["cancelled"] is False

    def test_a_cancellation_does_not_leak_into_the_next_run(self, scripted):
        """The flag is cleared on `begin`, not on `cancel` — a cancellation
        arriving between two runs must not silently stop the next one."""
        control = scripted()
        control.job.begin(ACTION_VALIDATE, 5)
        control.job.cancel()
        control.job.stopped({})
        control.start_maintenance(ACTION_VERIFY_CACHE)
        job = wait_for_job(control)
        assert job["state"] == "done"
        assert job["cancelled"] is False

    def test_a_cancelled_benchmark_keeps_what_it_measured(self, scripted):
        """Stopping is not discarding: a benchmark stopped after two providers
        has still measured two providers, and throwing that away would make the
        stop button a punishment for using it."""
        control = scripted({"alpha": [frame(30, Timeframe.D1)],
                            "beta": [frame(30, Timeframe.D1)],
                            "gamma": [frame(30, Timeframe.D1)]})
        control.job.begin(ACTION_BENCHMARK, 3)
        control.job.cancel()
        control.job.stopped({"results": [{"provider": "alpha", "ok": True}],
                             "message": "Stopped after 1 provider(s)."})
        summary = control.job.as_dict()["summary"]
        assert summary["results"] == [{"provider": "alpha", "ok": True}]

    def test_actions_declare_whether_they_spend_requests(self, scripted):
        """On a 25-per-day key, a user is entitled to know which button costs
        them requests BEFORE pressing it."""
        actions = {a["action"]: a for a in
                   scripted().dashboard()["maintenance"]["actions"]}
        assert actions["benchmark"]["spends_requests"] is True
        assert actions["clear_cache"]["spends_requests"] is False


class TestRecommendations:
    def test_a_single_source_install_is_told_to_add_one(self, tmp_path):
        config = MarketDataConfig(credentials_path=str(tmp_path / "c.json"))
        registry = default_registry(environ={}, config=config)
        # Bench the only non-Yahoo keyless source, leaving one family.
        registry.set_enabled("stooq", False)
        control = MarketDataControl(MarketDataService(registry, config=config),
                                    config=config, environ={})
        rec = _rec(control, "only one independent data source")
        assert rec["severity"] == "warning"
        assert rec["signup_url"].startswith("https://")

    def test_the_suggested_provider_can_actually_serve_history(self, tmp_path):
        """It must not be Finnhub.

        Finnhub is the first keyed provider by priority, so the obvious
        implementation recommends it — and its free tier answers 403 to every
        chart request (`docs/MARKET_DATA.md` §41). Sending someone to register
        for a key that cannot do the job is worse than saying nothing.
        """
        config = MarketDataConfig(credentials_path=str(tmp_path / "c.json"))
        registry = default_registry(environ={}, config=config)
        registry.set_enabled("stooq", False)
        control = MarketDataControl(MarketDataService(registry, config=config),
                                    config=config, environ={})
        rec = _rec(control, "only one independent data source")
        assert rec["provider"] != "finnhub"
        assert registry.get(rec["provider"]).free_tier_serves_history is True

    def test_finnhub_is_marked_as_needing_a_paid_plan_for_history(self, shipped):
        """Stated on the card BEFORE a user spends ten minutes registering."""
        row = _row(shipped, "finnhub")
        assert row["feed"]["free_tier_serves_history"] is False
        assert "Paid plan" in row["feed"]["cost"]

    def test_the_other_keyed_providers_are_not_marked_that_way(self, shipped):
        for name in ("twelvedata", "alphavantage"):
            assert _row(shipped, name)["feed"]["free_tier_serves_history"] is True

    def test_no_usable_provider_is_critical_and_gives_recovery_steps(self, scripted):
        control = scripted()
        for name in ("alpha", "beta", "gamma"):
            control.set_enabled(name, False)
        rec = control.recommendations()[0]
        assert rec["severity"] == "critical"
        assert "internet connection" in rec["detail"]

    def test_a_provider_near_its_quota_is_flagged_before_it_is_spent(self, tmp_path):
        adapter = ScriptedAdapter("metered")
        adapter.quota.policy = RateLimitPolicy(per_day=10)
        adapter.quota.record(9)
        control = MarketDataControl(
            MarketDataService(ProviderRegistry([adapter])), environ={})
        rec = _rec(control, "close to its daily limit")
        assert "9 of 10" in rec["detail"]

    def test_an_exhausted_provider_says_it_will_come_back(self, tmp_path):
        adapter = ScriptedAdapter("metered")
        adapter.quota.policy = RateLimitPolicy(per_day=10)
        adapter.quota.record(10)
        control = MarketDataControl(
            MarketDataService(ProviderRegistry([adapter])), environ={})
        rec = _rec(control, "entire daily allowance")
        assert "when the quota resets" in rec["detail"]

    def test_a_repeatedly_failing_provider_suggests_switching_it_off(self, scripted):
        control = scripted()
        monitor = control.registry.get("beta").monitor
        for _ in range(9):
            monitor.record_failure("unavailable", "connection refused")
            monitor.evaluate_breaker()
        rec = _rec(control, "keeps failing")
        assert rec["action"] == "disable"
        assert rec["provider"] == "beta"

    def test_a_rejected_key_tells_the_user_to_replace_it(self, shipped):
        shipped.set_api_key("finnhub", SECRET)
        shipped.registry.get("finnhub").monitor.note_auth_failure()
        rec = _rec(shipped, "rejected its API key")
        assert rec["action"] == "fix_key"

    def test_a_healthy_multi_source_install_is_told_nothing(self, scripted):
        """Advice that fires when nothing is wrong is advice nobody reads."""
        control = scripted()
        assert control.recommendations() == []

    def test_every_recommendation_names_an_action(self, scripted):
        control = scripted()
        for name in ("alpha", "beta"):
            control.set_enabled(name, False)
        for rec in control.recommendations():
            assert rec["action"] and rec["detail"] and rec["title"]


class TestPersistence:
    def test_choices_survive_a_restart(self, tmp_path):
        path = tmp_path / "marketdata.json"
        config = MarketDataConfig(control_state_path=str(path),
                                  credentials_path=str(tmp_path / "c.json"))
        registry = default_registry(environ={}, config=config)
        control = MarketDataControl(MarketDataService(registry, config=config),
                                    config=config, state_path=path, environ={})
        control.set_enabled("stooq", False)
        control.set_ordering_mode(ORDER_HYBRID)
        control.move("yfinance", "up")

        restored = apply_control_state(config, load_control_state(path))
        assert restored.ordering() == ORDER_HYBRID
        assert restored.for_provider("stooq").enabled is False
        rebuilt = default_registry(environ={}, config=restored)
        assert rebuilt.order()[0] == "yfinance"
        assert rebuilt.get("stooq").monitor.available() is False

    def test_a_corrupt_state_file_costs_the_choices_and_nothing_else(self, tmp_path):
        path = tmp_path / "marketdata.json"
        path.write_text("}{ not json", encoding="utf-8")
        assert load_control_state(path) == {}
        config = apply_control_state(MarketDataConfig(), load_control_state(path))
        assert config.ordering() == ORDER_DYNAMIC
        assert default_registry(environ={}, config=config).order()[0] == "yahoo"

    @pytest.mark.parametrize("state", [
        {"providers": [1, 2, 3]},                  # a list where a dict belongs
        {"providers": "everything"},
        {"providers": {"stooq": "off"}},           # a string where a dict belongs
        {"order": "yahoo,yfinance"},               # a string where a list belongs
        {"ordering_mode": 99},
        {"ordering_mode": None, "order": None, "providers": None},
        {"version": "banana"},
    ])
    def test_no_shape_of_hand_edited_state_can_stop_the_app_starting(
            self, state, tmp_path):
        """Found by the V0.5.7 self-audit: `providers` as a LIST raised an
        AttributeError out of the composition root — the app refusing to start
        because a preferences file had been edited badly. Every field is now
        type-checked on the way in, and this parametrisation is the guard."""
        config = apply_control_state(MarketDataConfig(), state)
        assert config.ordering() in (ORDER_STATIC, ORDER_HYBRID, ORDER_DYNAMIC)
        registry = default_registry(environ={}, config=config)
        assert len(registry.order()) == len(registry.adapters)

    def test_a_hand_edited_mode_degrades_to_the_default(self, tmp_path):
        """This file lives in a directory the user can open. A nonsense value
        must not reach `ordering()` and be ignored somewhere less obvious."""
        config = apply_control_state(
            MarketDataConfig(), {"ordering_mode": "fastest-possible"})
        assert config.ordering() == ORDER_DYNAMIC

    def test_a_stored_mode_overrides_the_legacy_dynamic_ranking_pin(self, tmp_path):
        config = apply_control_state(MarketDataConfig(dynamic_ranking=False),
                                     {"ordering_mode": ORDER_HYBRID})
        assert config.ordering() == ORDER_HYBRID

    def test_no_state_path_means_choices_apply_but_do_not_persist(self, scripted):
        control = scripted(config=MarketDataConfig())
        assert control.set_enabled("beta", False)["ok"] is True
        assert control.registry.get("beta").monitor.available() is False

    def test_an_unwritable_state_path_does_not_fail_the_change(self, tmp_path):
        """The adapters were already updated when the write is attempted, so
        failing loudly would report an error for a change the user can see took
        effect."""
        blocked = tmp_path / "afile"
        blocked.write_text("x", encoding="utf-8")
        config = MarketDataConfig(control_state_path=str(blocked / "nested.json"))
        registry = default_registry(environ={}, config=config)
        control = MarketDataControl(MarketDataService(registry, config=config),
                                    config=config,
                                    state_path=blocked / "nested.json", environ={})
        assert control.set_enabled("stooq", False)["ok"] is True
        assert registry.get("stooq").monitor.available() is False


class TestQaGate:
    def test_every_qa_method_refuses_while_qa_mode_is_off(self, scripted):
        control = scripted()
        assert control.qa_state() == {"enabled": False}
        assert "error" in control.qa_arm("alpha", "outage")
        assert "error" in control.qa_clear()
        assert "error" in control.qa_trip_breaker("alpha")
        assert "error" in control.qa_reset_health()
        assert "error" in control.qa_corrupt_cache()

    def test_the_dashboard_hides_qa_entirely_when_it_is_off(self, scripted):
        payload = scripted().dashboard()
        assert payload["qa_mode"] is False
        assert payload["qa"] is None

    def test_with_qa_on_a_fault_can_be_armed_and_cleared(self, scripted):
        control = scripted(config=MarketDataConfig(qa_mode=True))
        try:
            assert control.qa_arm("alpha", "outage")["ok"] is True
            assert "alpha" in control.qa_state()["faults"]
            assert control.qa_clear()["cleared"] == 1
            assert control.qa_state()["faults"] == {}
        finally:
            from optionspilot.data.faults import FAULTS
            FAULTS.clear_all()

    def test_an_unknown_fault_or_provider_is_refused(self, scripted):
        control = scripted(config=MarketDataConfig(qa_mode=True))
        assert "error" in control.qa_arm("alpha", "explode")
        assert "error" in control.qa_arm("ghost", "outage")

    def test_tripping_a_breaker_takes_the_provider_out_of_rotation(self, scripted):
        control = scripted(config=MarketDataConfig(qa_mode=True))
        assert control.qa_trip_breaker("alpha", 30)["ok"] is True
        assert control.registry.get("alpha").monitor.available() is False
        assert control.qa_reset_health()["ok"] is True
        assert control.registry.get("alpha").monitor.available() is True


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(control, name: str) -> dict:
    return next(r for r in control.dashboard()["providers"] if r["name"] == name)


def _rec(control, fragment: str) -> dict:
    matches = [r for r in control.recommendations() if fragment in r["title"]]
    assert matches, (f"no recommendation matching {fragment!r}; got "
                     f"{[r['title'] for r in control.recommendations()]}")
    return matches[0]


class _ExplodingCache:
    """A cache whose every operation fails — for asserting that a maintenance
    failure is reported rather than lost on a background thread."""

    def purge(self, *a, **k):
        raise RuntimeError("Boom")

    def stats(self):
        raise RuntimeError("Boom")
