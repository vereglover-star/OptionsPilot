"""The transport's mapping from a service failure to an HTTP status.

V0.9.2-C8, and the other half of finding H-7. C1 gave failures a vocabulary and
C7 made services speak it; this is the one place that vocabulary becomes a
status code.

**Why it lives in `ui/` and not beside the errors it maps.** `ServiceError`
carries no status by design — `NotFound` is a statement about the domain, and
that it becomes 404 over HTTP is a transport decision. A CLI would map the same
codes to exit statuses and a message; a future mobile backend might not map them
at all. Putting the table here is what keeps `services/` free of HTTP, which
`tests/test_service_errors.py::TestNoHttpConceptsLeakIn` asserts.

**What changed for clients.** Two things, both deliberate:

*An unclassified exception is now a 500.* `ui/api_v1.py` used to infer a status
from the builtin type — `except ValueError` became 422, `except KeyError` became
404 — so an internal defect reached the user as *their* mistake, and the
traceback that would have found it was discarded because the handler believed it
understood the failure. Those two clauses are gone. A service that means "your
request is wrong" now says so with a code; anything else is a defect and is
reported as one.

*A client's unparseable timeframe is a 422, not a 502.* `/api/candles?tf=7m`
answered "candles unavailable" with a 502 — H-7 pointing the other way, a client
error dressed as an upstream failure, which sends a user to check their internet
connection over a typo.

**Two response shapes, on purpose.** `/api/v1/*` returns the full
`error_envelope` (code, message, details, request id). The legacy routes keep
`{"error": "<message>"}`, because that is the shape `index.html` reads and this
commit is about statuses, not a frontend rewrite. Both get the same status from
the same table.
"""

from __future__ import annotations

from optionspilot.services.contracts import ERROR_CODES

#: One status per declared error code. Total over `ERROR_CODES` in both
#: directions, asserted by `tests/test_transport_errors.py` — a code with no
#: status would fall through to 500 and report a client's mistake as a server
#: fault, which is the defect this table exists to end.
STATUS_FOR_CODE: dict[str, int] = {
    # The request was understood and is not acceptable. 422 rather than 400
    # because the body parsed fine; it is the *content* that is wrong.
    "validation_error": 422,
    "authentication_required": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    # 429 carries `Retry-After` semantics clients already understand, which is
    # the difference between this and `unavailable_provider`: a rate limit ends
    # on its own and the caller can be told roughly when.
    "rate_limited": 429,
    # 503, not 502: the app is reachable and working, an upstream it depends on
    # is not. 502 would claim this server produced an invalid response.
    "unavailable_provider": 503,
    "internal_error": 500,
}

#: The status for a failure that carried no code at all — an unclassified
#: exception, which is by definition a defect in this application rather than a
#: statement about the request.
UNCLASSIFIED_STATUS = 500

#: What an unclassified failure tells the client. Deliberately says nothing
#: about internals; the type goes in `details` for a bug report, and the
#: traceback goes to the log.
UNCLASSIFIED_MESSAGE = "The request could not be completed."


def status_for(code: str | None) -> int:
    """The HTTP status for a service error code.

    Falls back to 500 for an unknown code rather than raising: the caller is
    already handling a failure, and a second one raised inside the error path
    is how a clean 4xx becomes an empty 500 with no body.
    """
    if code is None:
        return UNCLASSIFIED_STATUS
    return STATUS_FOR_CODE.get(code, UNCLASSIFIED_STATUS)


def missing_codes() -> set[str]:
    """Declared codes with no status. Used by the test, and by nothing else."""
    return set(ERROR_CODES) - set(STATUS_FOR_CODE)
