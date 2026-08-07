"""Versioned transport adapter for the platform/application contracts.

This module deliberately contains routing and serialization only.  All useful
work is delegated to the injected ``UIServer`` and its service registry.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from optionspilot.services.contracts import (
    ClientCapabilities, context_from_headers, error_envelope, success_envelope,
)
from optionspilot.services import intelligence as intel_view
from optionspilot.services import quickpick
from optionspilot.services import LocalSyncProvider
from optionspilot.services import sync as sync_boundaries
from optionspilot.services.errors import ServiceError
from optionspilot.services.idempotency import fingerprint
from optionspilot.ui.errors import (
    UNCLASSIFIED_MESSAGE, UNCLASSIFIED_STATUS, status_for,
)


SERVER_CAPABILITIES = frozenset({
    "workspace_profiles", "multi_chart_layouts", "notifications",
    "notification_actions",
    "background_refresh", "offline_cache", "synchronization", "health_diagnostics",
})


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "optionspilot_request_id", None)
    if request_id:
        return request_id
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.optionspilot_request_id = request_id
    return request_id


def _ok(request: Request, data: Any, **meta) -> dict:
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get(
        "X-Idempotency-Key")
    if idempotency_key:
        meta.setdefault("idempotency_key", idempotency_key)
    return success_envelope(data, request_id=_request_id(request), meta=meta)


def _fail(request: Request, code: str, message: str, status: int = 400,
          details: Any = None) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        error_envelope(code, message, request_id=request_id, details=details),
        status_code=status,
        headers={"X-Request-ID": request_id},
    )


def _service_fail(request: Request, exc) -> JSONResponse:
    """A classified failure: the service said what was wrong and why."""
    return _fail(request, exc.code, exc.message, status_for(exc.code),
                 exc.details)


def _unclassified_fail(request: Request, exc: Exception) -> JSONResponse:
    """Anything else, which is by definition a defect in this application.

    V0.9.2-C8 removed the two clauses above this one — `except ValueError`
    became 422 and `except KeyError` became 404 — because `ValueError` is what
    `int("x")` raises and `KeyError` is what a dict typo raises, so an internal
    bug was reported to the user as their mistake and the traceback was thrown
    away. The type is kept in `details` for a bug report; the message says
    nothing about internals.
    """
    return _fail(request, "internal_error", UNCLASSIFIED_MESSAGE,
                 UNCLASSIFIED_STATUS, {"type": type(exc).__name__})


def _safe(request: Request, fn, *args, **kwargs):
    try:
        return _ok(request, fn(*args, **kwargs))
    except ServiceError as exc:
        return _service_fail(request, exc)
    except Exception as exc:  # noqa: BLE001 - transport boundary
        return _unclassified_fail(request, exc)


def _mutation(request: Request, server, operation: str, fn, payload=None):
    """Execute a retry-sensitive application mutation once per client key.

    `payload` is fingerprinted (V0.9.2-C9, finding N-2) so that reusing one key
    for a *different* request is refused with a 409 rather than answered with
    the first request's result. A route that passes nothing opts out, and is no
    worse off than it was before.
    """
    try:
        key = request.headers.get("Idempotency-Key") or request.headers.get(
            "X-Idempotency-Key")
        data, replayed = server.idempotency.execute(
            operation, key, fn, fingerprint=fingerprint(payload))
        return _ok(request, data, idempotency_replayed=replayed)
    except ServiceError as exc:
        return _service_fail(request, exc)
    except Exception as exc:  # noqa: BLE001 - transport boundary
        return _unclassified_fail(request, exc)


def register_v1_routes(app: FastAPI, server) -> None:
    """Register v1 routes against one existing application service owner."""

    @app.middleware("http")
    async def v1_request_id_header(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers.setdefault("X-Request-ID", _request_id(request))
        return response

    @app.get("/api/v1/status", tags=["status"])
    def status(request: Request):
        return _safe(request, server.status_payload)

    @app.get("/api/v1/workspace", tags=["workspace"])
    def workspace(request: Request):
        return _safe(request, server.workspace_payload)

    @app.post("/api/v1/workspace", tags=["workspace"])
    def workspace_update(request: Request, payload: dict | None = None):
        return _mutation(request, server, "workspace.update",
                         lambda: server.update_workspace(payload), payload)

    @app.delete("/api/v1/workspace", tags=["workspace"])
    def workspace_reset(request: Request):
        return _mutation(request, server, "workspace.reset", server.reset_workspace)

    # Home, in one request (M3-C3). Six regions from four owners: six round
    # trips would mean six chances to arrive late and six independently
    # shifting regions, which is the layout jump the milestone exists to
    # remove. Regions still fail independently — see `HomeView.errors`.
    @app.get("/api/v1/home", tags=["home"])
    def home(request: Request):
        return _safe(request, lambda: server.services.home.view().to_dict())

    # Quick picks (M4-C1). Two routes, because a client needs to know what the
    # chips ARE before it can press one, and the catalogue must not be a second
    # copy of `quickpick.INTENTS` living in `index.html` — the two-catalogue
    # drift `guide.py` has a bidirectional test for.
    @app.get("/api/v1/quickpicks", tags=["trade"])
    def quickpicks(request: Request):
        return _safe(request, lambda: {"intents": quickpick.catalogue()})

    @app.get("/api/v1/quickpick", tags=["trade"])
    def quickpick_resolve(request: Request, symbol: str, intent: str,
                          expiration: str = "", right: str = "call"):
        return _safe(request, lambda: server.services.trading.quick_pick(
            symbol=symbol, intent=intent, expiration=expiration, right=right))

    # Review (M4-C2). A POST because the body is an order draft, not an
    # address — but it places nothing and mutates nothing, so it does not go
    # through `_mutation`'s idempotency path.
    @app.post("/api/v1/review", tags=["trade"])
    async def review_order(request: Request):
        body = await request.json()
        return _safe(request,
                     lambda: server.services.trading.review_order(body))

    @app.get("/api/v1/notifications", tags=["notifications"])
    def notifications(request: Request, limit: int = 15):
        return _safe(request, lambda: {
            "items": [n.to_dict() for n in server.services.notifications.recent(limit)]
        })

    # Read state (M2-C6). Here rather than in `ui/server.py` because that file
    # has a line ceiling whose whole point is that new surface goes elsewhere.
    @app.post("/api/v1/notifications/read", tags=["notifications"])
    def notifications_read(request: Request, payload: dict | None = None):
        ids = (payload or {}).get("ids") or []
        return _mutation(request, server, "notifications.read",
                         lambda: {"read": server.services.notifications.mark_read(ids)},
                         payload)

    @app.post("/api/v1/notifications/{event_id}/dismiss", tags=["notifications"])
    def notification_dismiss(request: Request, event_id: str):
        return _mutation(request, server, "notification.dismiss:" + event_id, lambda: {
            "dismissed": server.services.notifications.dismiss(event_id)
        })

    @app.get("/api/v1/runtime", tags=["runtime"])
    def runtime(request: Request):
        return _safe(request, server.runtime_payload)

    @app.patch("/api/v1/runtime", tags=["runtime"])
    def runtime_update(request: Request, payload: dict | None = None):
        return _mutation(request, server, "runtime.update",
                         lambda: server.update_runtime(payload), payload)

    @app.post("/api/v1/runtime/pause", tags=["runtime"])
    def runtime_pause(request: Request):
        return _mutation(request, server, "runtime.pause", server.pause_background)

    @app.post("/api/v1/runtime/resume", tags=["runtime"])
    def runtime_resume(request: Request):
        return _mutation(request, server, "runtime.resume", server.resume_background)

    @app.get("/api/v1/health", tags=["health"])
    def health(request: Request):
        return _safe(request, lambda: {
            "runtime": server.runtime_payload(),
            "host": server.services.host_view().to_dict(),
        })

    @app.get("/api/v1/capabilities", tags=["platform"])
    def capabilities(request: Request):
        host = server.services.host_view().to_dict()
        client = context_from_headers(request.headers)
        return _ok(request, {
            "protocol": "v1",
            "server": sorted(SERVER_CAPABILITIES),
            "host": host,
            "client": {
                "id": client.client_id,
                "version": client.client_version,
                "requested": list(client.capabilities),
                "negotiated": sorted(set(client.capabilities) & set(SERVER_CAPABILITIES)),
            },
        })

    @app.get("/api/v1/host", tags=["platform"])
    def host(request: Request):
        return _safe(request, lambda: server.services.host_view().to_dict())

    @app.get("/api/v1/sync", tags=["sync"])
    def sync(request: Request):
        report = sync_boundaries.report()
        report["provider"] = "local"
        report["protocol"] = "v1"
        report["snapshot"] = LocalSyncProvider().snapshot().to_dict()
        return _ok(request, report)

    @app.get("/api/v1/diagnostics/sync", tags=["sync"])
    def sync_diagnostics(request: Request):
        return _ok(request, sync_boundaries.report())

    @app.get("/api/v1/updates/status", tags=["updates"])
    def update_status(request: Request):
        return _safe(request, server.updater.snapshot)

    @app.post("/api/v1/updates/check", tags=["updates"])
    def update_check(request: Request):
        def check():
            server.updater.check_now()
            return server.updater.snapshot()
        return _mutation(request, server, "updates.check", check)

    @app.get("/api/v1/guide", tags=["guide"])
    def guide(request: Request):
        return _safe(request, server.guide_payload)

    @app.post("/api/v1/guide/state", tags=["guide"])
    def guide_update(request: Request, payload: dict | None = None):
        return _mutation(request, server, "guide.update",
                         lambda: server.update_guide(payload), payload)

    @app.get("/api/v1/marketdata", tags=["marketdata"])
    def marketdata(request: Request):
        return _safe(request, server.marketdata_dashboard)

    @app.get("/api/v1/intelligence/summary", tags=["intelligence"])
    def intelligence_summary(request: Request):
        def read():
            with server.lock:
                snapshot = server.orch.intelligence_snapshot()
            return intel_view.summary(snapshot)
        return _safe(request, read)

    @app.get("/api/v1/openapi.json", tags=["platform"])
    def versioned_openapi():
        return app.openapi()

    @app.websocket("/api/v1/ws")
    async def websocket_v1(socket: WebSocket):
        request_id = socket.headers.get("X-Request-ID") or uuid.uuid4().hex
        await socket.accept()
        try:
            hello = await asyncio.wait_for(socket.receive_json(), timeout=10)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            await socket.close(code=1002)
            return
        if (not isinstance(hello, dict) or hello.get("version") != "v1"
                or hello.get("type") != "hello"):
            await socket.send_json({
                "version": "v1", "type": "error", "message_id": uuid.uuid4().hex,
                "timestamp": utc_timestamp(),
                "data": {"code": "protocol_error", "message": "v1 hello is required"},
                "meta": {"request_id": request_id},
            })
            await socket.close(code=1002)
            return
        hello_data = hello.get("data") or {}
        caps = hello_data.get("capabilities", [])
        if not isinstance(caps, list):
            caps = []
        client = ClientCapabilities(
            client_id=str(hello_data.get("client_id") or "desktop"),
            version=str(hello_data.get("client_version") or ""),
            capabilities=tuple(str(cap) for cap in caps if isinstance(cap, str)),
        )
        negotiated = client.negotiate(SERVER_CAPABILITIES)
        await socket.send_json({
            "version": "v1",
            "type": "hello.accepted",
            "message_id": uuid.uuid4().hex,
            # Protocol time, not the last health check — which is null until the
            # first six-hourly sweep runs, so every hello in a fresh session
            # carried `"timestamp": null` while every other frame carried a real
            # one.
            "timestamp": utc_timestamp(),
            "data": {
                "server_capabilities": sorted(SERVER_CAPABILITIES),
                "negotiated_capabilities": list(negotiated),
                "client": client.to_dict(),
            },
            "meta": {"request_id": request_id},
        })
        last_digest = ""

        def read_status() -> tuple[dict, str]:
            payload = server.status_payload()
            return payload, json.dumps(payload, sort_keys=True, default=str)

        try:
            while True:
                # OFF THE EVENT LOOP. `status_payload` takes `UIServer.lock`,
                # which a background scan can hold for seconds, and serialising
                # it is real CPU. The synchronous REST handlers get a threadpool
                # from FastAPI for free; an `async def` websocket does not, so
                # calling it inline stalled every other request in the process
                # for as long as the lock was held.
                payload, digest = await asyncio.to_thread(read_status)
                if digest != last_digest:
                    last_digest = digest
                    kind = "status.snapshot"
                    data = payload
                else:
                    kind = "status.heartbeat"
                    data = {"ts": payload.get("ts")}
                await socket.send_json({
                    "version": "v1", "type": kind, "message_id": uuid.uuid4().hex,
                    "timestamp": payload.get("ts"), "data": data,
                    "meta": {"request_id": request_id},
                })
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    # Every existing REST surface remains implemented by its established
    # application/legacy handler while becoming reachable through v1.  This is
    # deliberately a bridge, not a second catalogue of business logic: it
    # invokes the already-registered legacy route and applies the v1 envelope
    # exactly once at the transport boundary.  New routes should replace these
    # bridges with typed application adapters without changing their URL.
    canonical = {
        route.path for route in app.router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    }

    async def forward_legacy(request: Request):
        legacy_path = request.scope["path"].replace("/api/v1/", "/api/", 1)
        scope = dict(request.scope)
        scope["path"] = legacy_path
        scope["raw_path"] = legacy_path.encode("ascii", "ignore")
        messages = []

        async def send(message):
            messages.append(message)

        await app.router(scope, request.receive, send)
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        payload = b"".join(m.get("body", b"") for m in messages
                           if m["type"] == "http.response.body")
        status = start["status"] if start else 500
        try:
            data = json.loads(payload or b"null")
        except ValueError:
            data = {"content": payload.decode("utf-8", "replace")}
        if status >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            return _fail(request, "validation_error" if status < 500 else "internal_error",
                         "The request could not be completed.", status, detail)
        return _ok(request, data, compatibility_alias=True)

    for route in list(app.router.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        path = "/api/v1/" + route.path.removeprefix("/api/")
        if path in canonical:
            continue
        methods = [method for method in route.methods if method not in {"HEAD", "OPTIONS"}]
        if methods:
            parameters = [
                {"name": name, "in": "path", "required": True,
                 "schema": {"type": "string"}}
                for name in re.findall(r"\{([^}:]+)(?::[^}]+)?\}", path)
            ]
            app.add_api_route(path, forward_legacy, methods=methods,
                              tags=["compatibility"], include_in_schema=True,
                              openapi_extra={"parameters": parameters} if parameters else None)


def utc_timestamp() -> str:
    """Protocol timestamps are UTC and independent of the desktop host clock."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
