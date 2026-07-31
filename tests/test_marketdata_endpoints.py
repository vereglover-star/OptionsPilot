"""The `/api/marketdata/*` HTTP surface.

`test_marketdata_control.py` proves the control plane does the right thing;
this proves the routes reach it, return the right status codes, and cannot be
made to do something the control plane would refuse. The split matters because
a route is the one layer that can undo every guarantee below it — by forgetting
a gate, by echoing a payload it should have masked, or by exposing a mutation
as a GET that a browser will prefetch.

  TestDashboardRoute   the read path, and that it is free
  TestKeyRoutes        a key round-trips and is never echoed back
  TestProviderRoutes   enable/disable, move, test
  TestOrderRoutes      order, reset, ordering mode
  TestMaintenanceRoute start + poll, and bad input
  TestQaGate           404 without QA mode, reachable with it
  TestMethodDiscipline nothing that costs a request is a GET
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from optionspilot.config.settings import AppConfig
from optionspilot.ui.server import create_app

SECRET = "sk_endpoint_1234567890ab"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(AppConfig(), run_loop=False, data_dir=tmp_path))


@pytest.fixture
def qa_client(tmp_path):
    config = AppConfig()
    config.market_data.qa_mode = True
    return TestClient(create_app(config, run_loop=False, data_dir=tmp_path))


def _provider(client, name: str) -> dict:
    rows = client.get("/api/marketdata").json()["providers"]
    return next(r for r in rows if r["name"] == name)


class TestDashboardRoute:
    def test_it_returns_every_provider_with_a_state(self, client):
        payload = client.get("/api/marketdata").json()
        assert {p["name"] for p in payload["providers"]} >= {"yahoo", "finnhub"}
        for p in payload["providers"]:
            assert p["health_state"] and p["health_detail"]

    def test_it_carries_everything_the_page_needs_in_one_request(self, client):
        """The page auto-refreshes; N requests per refresh would make the
        settings screen the busiest client of the system it reports on."""
        payload = client.get("/api/marketdata").json()
        for key in ("providers", "ranking", "order", "ordering_mode",
                    "ordering_modes", "failover", "recommendations",
                    "maintenance", "cache", "requests", "qa_mode"):
            assert key in payload, f"missing {key}"

    def test_it_spends_no_upstream_request(self, client):
        before = client.get("/api/marketdata").json()["requests"]["total_requests"]
        for _ in range(3):
            client.get("/api/marketdata")
        after = client.get("/api/marketdata").json()["requests"]["total_requests"]
        assert after == before


class TestKeyRoutes:
    def test_a_key_is_saved_and_the_response_carries_only_a_mask(self, client):
        res = client.post("/api/marketdata/providers/finnhub/key",
                          json={"api_key": SECRET})
        assert res.status_code == 200
        assert SECRET not in res.text
        assert res.json()["masked_key"].endswith(SECRET[-4:])

    def test_the_saved_key_never_appears_in_any_later_payload(self, client):
        client.post("/api/marketdata/providers/finnhub/key",
                    json={"api_key": SECRET})
        assert SECRET not in client.get("/api/marketdata").text
        assert SECRET not in client.get("/api/diagnostics/marketdata").text
        assert SECRET not in client.get(
            "/api/diagnostics/marketdata/export?format=json").text
        assert SECRET not in client.get(
            "/api/diagnostics/marketdata/export?format=text").text
        assert SECRET not in client.get("/api/config").text

    def test_saving_a_key_makes_the_provider_usable_immediately(self, client):
        assert _provider(client, "finnhub")["health_state"] == "missing_key"
        client.post("/api/marketdata/providers/finnhub/key",
                    json={"api_key": SECRET})
        row = _provider(client, "finnhub")
        assert row["health_state"] != "missing_key"
        assert row["credential"]["source"] == "stored"

    def test_removing_a_key_reverts_it(self, client):
        client.post("/api/marketdata/providers/finnhub/key",
                    json={"api_key": SECRET})
        assert client.delete(
            "/api/marketdata/providers/finnhub/key").json()["removed"] is True
        assert _provider(client, "finnhub")["health_state"] == "missing_key"

    def test_a_key_survives_a_restart_of_the_app(self, tmp_path):
        first = TestClient(create_app(AppConfig(), run_loop=False,
                                      data_dir=tmp_path))
        first.post("/api/marketdata/providers/finnhub/key",
                   json={"api_key": SECRET})
        second = TestClient(create_app(AppConfig(), run_loop=False,
                                       data_dir=tmp_path))
        assert _provider(second, "finnhub")["credential"]["masked_key"] \
            .endswith(SECRET[-4:])
        assert _provider(second, "finnhub")["health_state"] != "missing_key"

    def test_a_key_for_a_keyless_provider_is_a_400(self, client):
        res = client.post("/api/marketdata/providers/yahoo/key",
                          json={"api_key": SECRET})
        assert res.status_code == 400

    def test_an_unknown_provider_is_a_400_not_a_500(self, client):
        assert client.post("/api/marketdata/providers/ghost/key",
                           json={"api_key": SECRET}).status_code == 400
        assert client.delete(
            "/api/marketdata/providers/ghost/key").status_code == 400

    def test_an_empty_body_is_refused_rather_than_storing_a_blank(self, client):
        assert client.post(
            "/api/marketdata/providers/finnhub/key").status_code == 400
        assert client.post("/api/marketdata/providers/finnhub/key",
                           json={"api_key": "   "}).status_code == 400


class TestProviderRoutes:
    def test_disabling_and_re_enabling_round_trips(self, client):
        client.post("/api/marketdata/providers/stooq/enabled",
                    json={"enabled": False})
        assert _provider(client, "stooq")["enabled"] is False
        assert _provider(client, "stooq")["health_state"] == "disabled"
        client.post("/api/marketdata/providers/stooq/enabled",
                    json={"enabled": True})
        assert _provider(client, "stooq")["enabled"] is True

    def test_a_disable_survives_a_restart(self, tmp_path):
        first = TestClient(create_app(AppConfig(), run_loop=False,
                                      data_dir=tmp_path))
        first.post("/api/marketdata/providers/stooq/enabled",
                   json={"enabled": False})
        second = TestClient(create_app(AppConfig(), run_loop=False,
                                       data_dir=tmp_path))
        assert _provider(second, "stooq")["enabled"] is False

    def test_move_changes_the_order(self, client):
        before = client.get("/api/marketdata").json()["order"]
        res = client.post("/api/marketdata/providers/" + before[1] + "/move",
                          json={"direction": "up"})
        assert res.json()["order"][0] == before[1]

    def test_an_invalid_direction_is_a_400(self, client):
        assert client.post("/api/marketdata/providers/yahoo/move",
                           json={"direction": "sideways"}).status_code == 400

    def test_testing_a_keyless_configured_provider_needs_no_network_to_fail(
            self, client):
        """Finnhub has no key here, so the route must answer WITHOUT making a
        request — a test endpoint that fired a doomed request at every
        unconfigured provider would collect 401s and poison their health."""
        res = client.post("/api/marketdata/providers/finnhub/test")
        assert res.status_code == 200
        assert res.json()["code"] == "missing_key"
        assert res.json()["ok"] is False
        assert res.json()["action"]


class TestOrderRoutes:
    def test_setting_an_explicit_order_takes_effect(self, client):
        order = client.get("/api/marketdata").json()["order"]
        reversed_order = list(reversed(order))
        res = client.post("/api/marketdata/order", json={"order": reversed_order})
        assert res.json()["order"] == reversed_order
        assert client.get("/api/marketdata").json()["order"] == reversed_order

    def test_reset_restores_the_shipped_order(self, client):
        default = client.get("/api/marketdata").json()["default_order"]
        client.post("/api/marketdata/order", json={"order": list(reversed(default))})
        client.post("/api/marketdata/order/reset")
        assert client.get("/api/marketdata").json()["order"] == default

    def test_a_non_list_order_is_a_400(self, client):
        assert client.post("/api/marketdata/order",
                           json={"order": "yahoo"}).status_code == 400

    def test_each_ordering_mode_is_accepted_and_explained(self, client):
        for mode in ("static", "hybrid", "dynamic"):
            res = client.post("/api/marketdata/ordering_mode", json={"mode": mode})
            assert res.status_code == 200
            assert res.json()["explanation"]
            assert client.get("/api/marketdata").json()["ordering_mode"] == mode

    def test_an_unknown_mode_is_a_400(self, client):
        assert client.post("/api/marketdata/ordering_mode",
                           json={"mode": "telepathic"}).status_code == 400

    def test_the_mode_survives_a_restart(self, tmp_path):
        first = TestClient(create_app(AppConfig(), run_loop=False,
                                      data_dir=tmp_path))
        first.post("/api/marketdata/ordering_mode", json={"mode": "hybrid"})
        second = TestClient(create_app(AppConfig(), run_loop=False,
                                       data_dir=tmp_path))
        assert second.get("/api/marketdata").json()["ordering_mode"] == "hybrid"


class TestMaintenanceRoute:
    def _run(self, client, action: str, timeout: float = 20.0) -> dict:
        assert client.post("/api/marketdata/maintenance",
                           json={"action": action}).status_code == 200
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = client.get("/api/marketdata/maintenance").json()
            if job["state"] != "running":
                return job
            time.sleep(0.02)
        pytest.fail(f"{action} never finished")

    def test_a_cache_action_runs_and_summarises(self, client):
        job = self._run(client, "verify_cache")
        assert job["state"] == "done"
        assert job["summary"]["message"]
        assert job["progress"] == 1.0

    def test_clear_cache_reports_what_it_removed(self, client):
        job = self._run(client, "clear_cache")
        assert job["state"] == "done"
        assert "bars_removed" in job["summary"]

    def test_diagnostics_action_returns_the_health_snapshot(self, client):
        job = self._run(client, "diagnostics")
        assert job["summary"]["health"]["providers"]

    def test_an_unknown_action_is_a_400(self, client):
        assert client.post("/api/marketdata/maintenance",
                           json={"action": "rm -rf"}).status_code == 400

    def test_the_idle_job_is_reported_not_an_error(self, client):
        assert client.get("/api/marketdata/maintenance").json()["state"] == "idle"

    def test_cancelling_nothing_is_not_an_error(self, client):
        res = client.delete("/api/marketdata/maintenance")
        assert res.status_code == 200
        assert res.json()["cancelled"] is False

    def test_a_running_action_can_be_cancelled(self, client):
        """The long actions hold the single job slot for minutes. Without this
        a user who started one by mistake has no way out but a restart."""
        from optionspilot.data.control import ACTION_VALIDATE
        # The slot is pinned directly rather than by racing a real action:
        # every action fast enough to start reliably in a test is also fast
        # enough to finish before the cancel arrives, which would make this
        # test pass without ever exercising cancellation.
        server_job = client.app.state.server.marketdata.job
        server_job.begin(ACTION_VALIDATE, 10)
        try:
            assert client.get(
                "/api/marketdata/maintenance").json()["cancellable"] is True
            assert client.delete(
                "/api/marketdata/maintenance").json()["cancelled"] is True
            assert client.get(
                "/api/marketdata/maintenance").json()["cancelled"] is True
        finally:
            server_job.stopped({})


class TestQaGate:
    """Without `market_data.qa_mode` these routes must not exist.

    404 rather than 403 on purpose: a 403 confirms the endpoint is there, which
    is a small thing to hand an unattended local HTTP server. 404 says only
    that this build has no such route, which is functionally true.
    """

    QA_ROUTES = [
        ("get", "/api/marketdata/qa", None),
        ("post", "/api/marketdata/qa/fault", {"provider": "yahoo",
                                              "kind": "outage"}),
        ("delete", "/api/marketdata/qa/fault", None),
        ("post", "/api/marketdata/qa/breaker", {"provider": "yahoo"}),
        ("post", "/api/marketdata/qa/reset", None),
        ("post", "/api/marketdata/qa/corrupt_cache", None),
    ]

    @pytest.mark.parametrize("method,path,body", QA_ROUTES)
    def test_every_qa_route_is_404_by_default(self, client, method, path, body):
        res = getattr(client, method)(path, **({"json": body} if body else {}))
        assert res.status_code == 404

    def test_the_dashboard_advertises_no_qa_mode(self, client):
        payload = client.get("/api/marketdata").json()
        assert payload["qa_mode"] is False
        assert payload["qa"] is None

    def test_with_qa_enabled_a_fault_can_be_armed_and_cleared(self, qa_client):
        try:
            res = qa_client.post("/api/marketdata/qa/fault",
                                 json={"provider": "yahoo", "kind": "outage"})
            assert res.status_code == 200
            assert "yahoo" in qa_client.get("/api/marketdata/qa").json()["faults"]
            assert qa_client.delete(
                "/api/marketdata/qa/fault").json()["cleared"] == 1
        finally:
            from optionspilot.data.faults import FAULTS
            FAULTS.clear_all()

    def test_with_qa_enabled_the_breaker_can_be_tripped(self, qa_client):
        res = qa_client.post("/api/marketdata/qa/breaker",
                             json={"provider": "yahoo", "seconds": 30})
        assert res.status_code == 200
        assert _provider(qa_client, "yahoo")["health_state"] == "circuit_open"
        qa_client.post("/api/marketdata/qa/reset")
        assert _provider(qa_client, "yahoo")["health_state"] != "circuit_open"

    def test_an_unknown_fault_is_a_400_even_in_qa_mode(self, qa_client):
        assert qa_client.post("/api/marketdata/qa/fault",
                              json={"provider": "yahoo",
                                    "kind": "nuclear"}).status_code == 400


class TestMethodDiscipline:
    """Anything that changes state or spends a request must not be a GET.

    Browsers prefetch, scanners crawl, and a link preview follows GETs. A
    mutation behind one is a mutation anybody's software can trigger by
    accident — and on a 25-request-per-day key, so is a read that costs a
    request.
    """

    COSTLY = [
        "/api/marketdata/providers/yahoo/test",
        "/api/marketdata/providers/yahoo/enabled",
        "/api/marketdata/providers/yahoo/move",
        "/api/marketdata/providers/yahoo/key",
        "/api/marketdata/order",
        "/api/marketdata/order/reset",
        "/api/marketdata/ordering_mode",
    ]

    @pytest.mark.parametrize("path", COSTLY)
    def test_no_mutating_route_answers_a_get(self, client, path):
        assert client.get(path).status_code == 405

    def test_the_two_gets_that_exist_are_both_free(self, client):
        assert client.get("/api/marketdata").status_code == 200
        assert client.get("/api/marketdata/maintenance").status_code == 200
