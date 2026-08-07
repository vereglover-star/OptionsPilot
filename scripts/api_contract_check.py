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
    "/api/v1/home", "/api/v1/openapi.json",
}

# Every region `UI_V2_WIREFRAMES.md` §2.4 puts on Home, as the payload must name
# them. Home renders from one request, so a rename here blanks a region rather
# than degrading it — and `errors` is how a region reports failure without Home
# failing as a whole (§2.10).
HOME_FIELDS = ("status", "account", "open_risk", "today_pnl", "buying_power",
               "win_rate", "positions", "working_orders", "next_actions",
               "equity", "watchlist", "errors")


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
CONTEXT_FIELDS = ("symbol", "timeframe", "expiry", "contract", "surface_level",
                  "shell_v2")


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

    # The shell flag is the migration's rollback path. A reset that silently
    # moved a device between two navigations would be the worst possible
    # moment to discover that reset owns more than it should.
    client.post("/api/v1/workspace", json={"shell_v2": True})
    if client.get("/api/v1/workspace").json()["data"]["shell_v2"] is not True:
        raise SystemExit("the shell flag did not round trip")
    if client.delete("/api/v1/workspace").json()["data"]["shell_v2"] is not True:
        raise SystemExit("workspace reset flipped the shell flag, which it does "
                         "not own")
    json.dumps(reset, allow_nan=False)


def check_home(client) -> None:
    """One real payload, for the same reason `check_workspace` exists.

    Path presence is not a contract. Home is assembled from four owners in one
    request, so the failure this catches is a region that silently stopped
    being named — which renders as a blank area rather than as an error.
    """
    body = client.get("/api/v1/home").json()["data"]
    missing = [f for f in HOME_FIELDS if f not in body]
    if missing:
        raise SystemExit(f"home payload is missing regions: {missing}")

    # The sentence is the product's single self-report; a Home that cannot say
    # anything is a Home with no first line.
    status = body["status"]
    if not status.get("text", "").strip() or not status.get("case"):
        raise SystemExit("home status line came back empty")
    if not isinstance(status.get("needs_you"), bool):
        raise SystemExit("home status line did not state whether it needs the user")

    # A fresh account has no evidence for a win rate, and must say so rather
    # than reporting 0%.
    win = body["win_rate"]
    if win["rate"] is not None or win["sufficient"]:
        raise SystemExit("a fresh account claimed a win rate it cannot evidence")

    # `Infinity`/`NaN` are not valid JSON and kill a browser parse — the whole
    # screen, not one region, because this payload is one request.
    json.dumps(body, allow_nan=False)


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
            check_home(client)
    print("API CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
