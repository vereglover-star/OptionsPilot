"""The service error hierarchy — one class per declared error code.

V0.9.2-C1, finding H-7. This module is *vocabulary only*: nothing raises these
yet (V0.9.2-C7) and nothing maps them to an HTTP status yet (V0.9.2-C8). Landing
the types alone is deliberate — they are the contract those two commits agree
on, and separating them keeps both reviewable.

**Why builtin exceptions could not carry this.** `ui/api_v1.py` infers a status
from the exception type: `ValueError` becomes 422 and `KeyError` becomes 404. But
`ValueError` is what `int("x")`, pandas and half the standard library raise, and
`KeyError` is what a dict-lookup typo raises. So an internal defect is reported
to the user as a client mistake, and the traceback that would have found it is
thrown away because the handler thought it understood the failure. A service
saying `raise NotFound(...)` is making a claim; a service leaking `KeyError` is
not.

**Two rules, both enforced here rather than by review.**

*One class per code, in both directions.* `__init_subclass__` refuses a class
whose code is not in `ERROR_CODES`, so a typo fails at import rather than on the
failing path — the one moment nobody wants a second error.
`tests/test_service_errors.py` asserts the reverse, that every declared code has
a class, because a one-directional catalogue check is a drift this repository has
already paid for (`ui/guide.py`).

*No HTTP.* No status, no `HTTPException`, no transport import. `NotFound` is a
statement about the domain; that it becomes 404 over HTTP is the transport's
decision, and a CLI or a future mobile backend must be able to raise and catch
these without knowing what a status code is. Asserted by
`TestNoHttpConceptsLeakIn`.
"""

from __future__ import annotations

from typing import Any

from optionspilot.core.errors import OptionsPilotError
from optionspilot.services.contracts import ERROR_CODES


class ServiceError(OptionsPilotError):
    """Base for every classified service failure. Never raised directly.

    `code` is `None` here on purpose: the base is an ``except`` target, not an
    error. Allowing it to be raised would permit an unclassified failure, which
    is the ``except Exception`` this hierarchy exists to replace.
    """

    #: Set by each subclass; must be a member of `ERROR_CODES`.
    code: str | None = None

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.code is None:
            raise ValueError(
                f"{cls.__name__} must declare a code from ERROR_CODES")
        if cls.code not in ERROR_CODES:
            raise ValueError(
                f"{cls.__name__}.code={cls.code!r} is not a declared error "
                f"code; add it to ERROR_CODES first or fix the typo")

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        if type(self) is ServiceError:
            raise TypeError(
                "ServiceError is abstract — raise a subclass so the failure "
                "carries a code")
        super().__init__(message)
        self.message = message
        # `{}` rather than None, matching `contracts.error_envelope`'s
        # `details or {}`. Two places normalising one field is how they end up
        # disagreeing.
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict:
        """The envelope's `error` object, built without a transport."""
        return {"code": self.code, "message": self.message,
                "details": dict(self.details)}


class ValidationError(ServiceError):
    """The request was understood and is not acceptable."""

    code = "validation_error"


class AuthenticationRequired(ServiceError):
    """No principal, and this operation needs one."""

    code = "authentication_required"


class Forbidden(ServiceError):
    """A principal exists and is not permitted to do this."""

    code = "forbidden"


class NotFound(ServiceError):
    """The named thing does not exist. A claim about the domain, not a lookup
    that happened to miss — that would be a `KeyError`, and a defect."""

    code = "not_found"


class Conflict(ServiceError):
    """The operation contradicts current state — a second backtest while one
    runs, or an idempotency key replayed with a different request."""

    code = "conflict"


class RateLimited(ServiceError):
    """A budget is spent. Distinct from `UnavailableProvider`: this one comes
    back on its own, and the caller can be told roughly when."""

    code = "rate_limited"


class UnavailableProvider(ServiceError):
    """An upstream this service depends on cannot answer right now."""

    code = "unavailable_provider"


class InternalError(ServiceError):
    """A defect, reported deliberately.

    Raised only where a service has already decided the failure is its own
    fault. It exists so that path is explicit rather than arriving as an
    unclassified exception — the message shown to a client should still say
    nothing about the internals.
    """

    code = "internal_error"
