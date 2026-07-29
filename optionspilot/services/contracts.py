"""Transport-independent platform contracts.

These types contain only primitives and standard-library values so a future
Flutter/native client can consume their serialized form without importing the
desktop shell or FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


ERROR_CODES = frozenset({
    "validation_error", "authentication_required", "forbidden",
    "not_found", "conflict", "rate_limited", "unavailable_provider",
    "internal_error",
})


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str = "local-user"
    authenticated: bool = False
    scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"subject": self.subject, "authenticated": self.authenticated,
                "scopes": list(self.scopes)}


class AuthProvider(Protocol):
    def authenticate(self, context: "RequestContext") -> Principal: ...


class AnonymousLocalAuth:
    """Current desktop policy: loopback clients need no login."""

    def authenticate(self, context: "RequestContext") -> Principal:
        return Principal(subject="local-user", authenticated=False,
                         scopes=("local",))


class DeviceTokenAuth:
    """Reserved boundary for future paired-device authentication."""

    def authenticate(self, context: "RequestContext") -> Principal:
        raise NotImplementedError("device-token authentication is not enabled")


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    api_version: str = "v1"
    transport: str = "http"
    client_id: str = "desktop"
    client_version: str = ""
    host: str = "desktop"
    capabilities: tuple[str, ...] = ()
    principal: Principal = field(default_factory=Principal)
    locale: str = ""
    timezone: str = "UTC"
    idempotency_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "api_version": self.api_version,
            "transport": self.transport,
            "client_id": self.client_id,
            "client_version": self.client_version,
            "host": self.host,
            "capabilities": list(self.capabilities),
            "principal": self.principal.to_dict(),
            "locale": self.locale,
            "timezone": self.timezone,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class ClientCapabilities:
    client_id: str
    version: str = ""
    capabilities: tuple[str, ...] = ()

    def negotiate(self, server: set[str] | frozenset[str]) -> tuple[str, ...]:
        return tuple(sorted(set(self.capabilities) & set(server)))

    def to_dict(self) -> dict:
        return {"client_id": self.client_id, "version": self.version,
                "capabilities": list(self.capabilities)}


def context_from_headers(headers, *, api_version: str = "v1",
                         transport: str = "http") -> RequestContext:
    """Build a context from transport metadata without exposing the transport
    object to application services."""
    raw_caps = headers.get("X-OptionsPilot-Client-Capabilities", "")
    try:
        import json
        caps = json.loads(raw_caps) if raw_caps else []
    except (TypeError, ValueError):
        caps = []
    if not isinstance(caps, list):
        caps = []
    return RequestContext(
        request_id=headers.get("X-Request-ID", ""),
        api_version=api_version,
        transport=transport,
        client_id=headers.get("X-OptionsPilot-Client", "desktop"),
        client_version=headers.get("X-OptionsPilot-Client-Version", ""),
        capabilities=tuple(str(c) for c in caps if isinstance(c, str)),
        principal=AnonymousLocalAuth().authenticate(None),
        locale=headers.get("Accept-Language", "").split(",", 1)[0],
        timezone=headers.get("X-OptionsPilot-Timezone", "UTC"),
        idempotency_key=headers.get("Idempotency-Key")
        or headers.get("X-Idempotency-Key"),
    )


@dataclass(frozen=True, slots=True)
class SyncRevision:
    domain: str
    revision: int
    updated_at: str

    @classmethod
    def now(cls, domain: str, revision: int = 0) -> "SyncRevision":
        return cls(domain, revision, datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {"domain": self.domain, "revision": self.revision,
                "updated_at": self.updated_at}


@dataclass(frozen=True, slots=True)
class SyncSnapshot:
    domains: dict[str, SyncRevision]
    provider: str = "local"

    def to_dict(self) -> dict:
        return {"provider": self.provider,
                "domains": {k: v.to_dict() for k, v in self.domains.items()}}


def success_envelope(data: Any, *, request_id: str, version: str = "v1",
                     meta: dict | None = None) -> dict:
    extra = {"api_version": version, "request_id": request_id}
    if meta:
        extra.update(meta)
    return {"data": data, "meta": extra}


def error_envelope(code: str, message: str, *, request_id: str,
                   details: Any = None, version: str = "v1") -> dict:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "meta": {"api_version": version, "request_id": request_id},
    }
