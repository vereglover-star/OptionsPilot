"""Offline v1 API contract smoke check."""

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
    print("API CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
