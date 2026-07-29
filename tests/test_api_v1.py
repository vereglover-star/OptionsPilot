from __future__ import annotations

from fastapi.testclient import TestClient

from optionspilot.config.settings import AppConfig
from optionspilot.ui.server import create_app


def test_v1_success_envelope_and_capability_negotiation(tmp_path):
    app = create_app(AppConfig(), run_loop=False, data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/capabilities",
            headers={
                "X-Request-ID": "req-1",
                "X-OptionsPilot-Client": "flutter",
                "X-OptionsPilot-Client-Version": "0.1",
                "X-OptionsPilot-Client-Capabilities": '["notifications", "unknown"]',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["meta"] == {"api_version": "v1", "request_id": "req-1"}
        assert response.headers["X-Request-ID"] == "req-1"
        assert body["data"]["client"]["id"] == "flutter"
        assert body["data"]["client"]["negotiated"] == ["notifications"]


def test_v1_runtime_is_persisted_and_legacy_routes_remain_raw(tmp_path):
    app = create_app(AppConfig(), run_loop=False, data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/runtime",
            json={"hidden_profile": "monitoring_only", "notification_mode": "reduced"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["settings"]["hidden_profile"] == "monitoring_only"
        assert client.get("/api/v1/runtime").json()["data"]["settings"]["notification_mode"] == "reduced"
        legacy = client.get("/api/status")
        assert legacy.status_code == 200
        assert "data" not in legacy.json()


def test_v1_websocket_uses_versioned_envelopes(tmp_path):
    app = create_app(AppConfig(), run_loop=False, data_dir=tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.send_json({
                "version": "v1", "type": "hello",
                "data": {"client_id": "flutter", "client_version": "0.1",
                         "capabilities": ["notifications", "unknown"]},
            })
            hello = socket.receive_json()
            message = socket.receive_json()
            assert hello["version"] == "v1"
            assert hello["type"] == "hello.accepted"
            assert hello["data"]["negotiated_capabilities"] == ["notifications"]
            assert message["version"] == "v1"
            assert message["type"] in {"status.snapshot", "status.heartbeat"}


def test_v1_idempotency_replays_a_mutation_without_reapplying(tmp_path):
    app = create_app(AppConfig(), run_loop=False, data_dir=tmp_path)
    with TestClient(app) as client:
        headers = {"Idempotency-Key": "runtime-profile-1"}
        first = client.patch("/api/v1/runtime", headers=headers,
                             json={"hidden_profile": "monitoring_only"})
        second = client.patch("/api/v1/runtime", headers=headers,
                              json={"hidden_profile": "normal"})
        assert first.status_code == second.status_code == 200
        assert second.json()["data"]["settings"]["hidden_profile"] == "monitoring_only"
        assert second.json()["meta"]["idempotency_replayed"] is True
