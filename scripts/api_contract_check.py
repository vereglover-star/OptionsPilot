"""Offline v1 API contract smoke check.

Path presence, the success envelope, and a valid published OpenAPI document —
plus, since UI V2 M1-C3, one round trip through `/api/v1/workspace`. That last
one exists because path presence is not a contract: `/api/v1/workspace` was in
`REQUIRED_PATHS` from the day it shipped and nothing in this file had ever
asked it for a payload, so a field could have been renamed, dropped or made
unserialisable and this check would still have printed PASS.
"""

from __future__ import annotations

import json
import tempfile

from fastapi.testclient import TestClient

from optionspilot.config.settings import AppConfig
from optionspilot.ui.server import create_app


REQUIRED_PATHS = {
    "/api/v1/status", "/api/v1/runtime", "/api/v1/workspace",
    "/api/v1/notifications", "/api/v1/capabilities", "/api/v1/sync",
    "/api/v1/openapi.json",
}


def validate_openapi(document: dict) -> None:
    """Validate the actual published document, not just JSON serializability."""
    try:
        from openapi_spec_validator import validate
    except ImportError as exc:
        raise SystemExit("openapi-spec-validator is required for contract checks") from exc
    try:
        validate(document)
    except Exception as exc:  # validator has several concrete error classes
        raise SystemExit(f"invalid OpenAPI document: {exc}") from exc


# Every context fact `UI_V2_DESIGN.md` §4.5 guarantees, as the workspace
# payload must name them. A rename here is a breaking change for any client.
CONTEXT_FIELDS = ("symbol", "timeframe", "expiry", "contract", "surface_level")


def check_workspace(client) -> None:
    """One full round trip: read, write, read back, reset."""
    body = client.get("/api/v1/workspace").json()["data"]
    missing = [f for f in CONTEXT_FIELDS if f not in body]
    if missing:
        raise SystemExit(f"workspace payload is missing context fields: {missing}")

    contract = {"symbol": "SPY", "expiry": "2026-09-18",
                "strike": 450.0, "right": "call"}
    client.post("/api/v1/workspace", json={
        "symbol": "SPY", "timeframe": "15m", "expiry": "2026-09-18",
        "contract": contract, "surface_level": 2})
    stored = client.get("/api/v1/workspace").json()["data"]
    for field, expected in (("symbol", "SPY"), ("timeframe", "15m"),
                            ("expiry", "2026-09-18"), ("contract", contract),
                            ("surface_level", 2)):
        if stored[field] != expected:
            raise SystemExit(
                f"workspace did not round trip {field}: "
                f"sent {expected!r}, read back {stored[field]!r}")

    # A contract belongs to an underlying, so a symbol change drops it. Asserted
    # here as well as in pytest because it is the invariant a client will lean
    # on hardest: the ticket must never hold an instrument the context has left.
    after = client.post("/api/v1/workspace", json={"symbol": "QQQ"}).json()["data"]
    if after["contract"] is not None:
        raise SystemExit("a symbol change left the previous contract selected")

    # Reset clears the document but not Surface Level, which is stored beside it.
    reset = client.delete("/api/v1/workspace").json()["data"]
    if reset["symbol"] != "SPY" or reset["contract"] is not None:
        raise SystemExit("workspace reset did not return the shipped defaults")
    if reset["surface_level"] != 2:
        raise SystemExit("workspace reset cleared Surface Level, which it does "
                         "not own")
    json.dumps(reset, allow_nan=False)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="optionspilot-api-") as root:
        app = create_app(AppConfig(), run_loop=False, data_dir=root)
        with TestClient(app) as client:
            openapi = client.get("/api/v1/openapi.json").json()
            missing = REQUIRED_PATHS - set(openapi.get("paths", {}))
            if missing:
                raise SystemExit(f"missing v1 paths: {sorted(missing)}")
            response = client.get("/api/v1/status")
            body = response.json()
            if response.status_code != 200 or set(body) != {"data", "meta"}:
                raise SystemExit("invalid v1 success envelope")
            json.dumps(openapi, allow_nan=False)
            validate_openapi(openapi)
            check_workspace(client)
    print("API CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
