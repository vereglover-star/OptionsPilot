"""V0.9.2-C1: the service error hierarchy, before anything raises it.

Finding H-7 — *internal bugs surfaced to clients as client errors*. The v1
transport infers a status from the builtin exception type::

    except ValueError as exc:
        return _fail(request, "validation_error", str(exc), 422)
    except KeyError as exc:
        return _fail(request, "not_found", str(exc), 404)

So a dict-lookup typo anywhere beneath a route — an internal defect — reaches
the user as **404 not found**, and a `ValueError` raised by pandas deep inside
an indicator becomes **422, your request was invalid**. Both blame the client
for a bug in the application, and both hide the defect from the logs that would
have shown a 500.

This commit adds the *types only*. Nothing raises them yet (C7) and nothing maps
them to a status yet (C8), which is deliberate: the hierarchy is the shared
vocabulary those two commits agree on, and landing it alone keeps each of them
reviewable.

**The two rules the specification sets for this commit, and both are asserted
below:**

1. **1:1 with `ERROR_CODES`.** In both directions. A class whose code is not a
   declared code produces an envelope no client can interpret; a declared code
   with no class is a code nothing can ever raise. This repository has paid for
   one-directional catalogue checks before — `services/guide.py`'s tutorial ids are
   asserted both ways for exactly this reason.
2. **No HTTP concepts leak in.** No status codes, no `HTTPException`, no
   `fastapi`/`starlette` import. A service must be usable by a CLI, a test or a
   future mobile backend that has never heard of HTTP, and the moment an
   exception carries a 404 the mapping decision has moved out of the transport
   and into the domain.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from optionspilot.core.errors import OptionsPilotError
from optionspilot.services import errors as service_errors
from optionspilot.services.contracts import ERROR_CODES
from optionspilot.services.errors import (
    AuthenticationRequired,
    Conflict,
    Forbidden,
    InternalError,
    NotFound,
    RateLimited,
    ServiceError,
    UnavailableProvider,
    ValidationError,
)

ROOT = Path(__file__).resolve().parents[1]


def _concrete() -> list[type[ServiceError]]:
    """Every leaf error class the services layer offers."""
    return [obj for obj in vars(service_errors).values()
            if isinstance(obj, type) and issubclass(obj, ServiceError)
            and obj is not ServiceError]


class TestTheCodeMappingIsExact:
    def test_every_class_declares_a_known_code(self):
        for cls in _concrete():
            assert cls.code in ERROR_CODES, (
                f"{cls.__name__}.code={cls.code!r} is not a declared error code; "
                "a client would receive a code its envelope cannot interpret")

    def test_every_declared_code_has_a_class(self):
        """The other direction. A code nothing can raise is dead vocabulary."""
        covered = {cls.code for cls in _concrete()}
        assert covered == set(ERROR_CODES), (
            f"codes with no class: {sorted(set(ERROR_CODES) - covered)}; "
            f"classes with no code: {sorted(covered - set(ERROR_CODES))}")

    def test_no_two_classes_share_a_code(self):
        codes = [cls.code for cls in _concrete()]
        assert len(codes) == len(set(codes)), (
            f"duplicate codes make the mapping ambiguous: {sorted(codes)}")

    @pytest.mark.parametrize("cls,code", [
        (ValidationError, "validation_error"),
        (AuthenticationRequired, "authentication_required"),
        (Forbidden, "forbidden"),
        (NotFound, "not_found"),
        (Conflict, "conflict"),
        (RateLimited, "rate_limited"),
        (UnavailableProvider, "unavailable_provider"),
        (InternalError, "internal_error"),
    ])
    def test_each_subclass_maps_to_its_code(self, cls, code):
        assert cls.code == code
        assert cls("boom").code == code


class TestTheBaseIsNotItselfRaisable:
    """`ServiceError` is the `except` target, not an error anyone raises.

    If the base carried a code it would be raisable without choosing one, and
    the choice is the entire value of the hierarchy — an unclassified failure
    is exactly the `except Exception` this commit exists to replace.
    """

    def test_the_base_declares_no_code(self):
        assert ServiceError.code is None

    def test_raising_the_base_directly_is_refused(self):
        with pytest.raises(TypeError, match="subclass"):
            ServiceError("no code chosen")

    def test_a_subclass_with_an_unknown_code_is_refused_at_definition(self):
        """Enforced when the class is created, not when it is raised.

        A validation that only fires on `raise` would let a mistyped code sit in
        the tree until the failing path executed — which, for an error class, is
        precisely the moment nobody wants a second failure.
        """
        with pytest.raises(ValueError, match="teapot"):
            class Teapot(ServiceError):
                code = "teapot"


class TestTheErrorCarriesWhatTheEnvelopeNeeds:
    def test_message_and_details_survive(self):
        exc = NotFound("no such symbol", details={"symbol": "ZZZZ"})
        assert str(exc) == "no such symbol"
        assert exc.message == "no such symbol"
        assert exc.details == {"symbol": "ZZZZ"}

    def test_details_defaults_to_an_empty_dict_not_none(self):
        """`error_envelope` writes `details or {}`; matching that here means the
        transport never has to normalise, and two places never disagree."""
        assert NotFound("gone").details == {}

    def test_it_projects_to_the_envelope_shape_without_a_transport(self):
        exc = Conflict("already running", details={"job": "backtest"})
        assert exc.to_dict() == {"code": "conflict",
                                 "message": "already running",
                                 "details": {"job": "backtest"}}

    def test_every_service_error_is_an_application_error(self):
        """`core.errors.OptionsPilotError` is the root that lets a caller say
        'a deliberate failure' rather than enumerating types."""
        for cls in _concrete():
            assert issubclass(cls, OptionsPilotError)

    def test_it_is_an_exception_first(self):
        with pytest.raises(ServiceError):
            raise ValidationError("bad input")


class TestNoHttpConceptsLeakIn:
    """The review focus, asserted rather than eyeballed."""

    @pytest.mark.parametrize("module", [
        "optionspilot/core/errors.py",
        "optionspilot/services/errors.py",
    ])
    def test_no_transport_import(self, module):
        imported = set()
        for node in ast.walk(ast.parse((ROOT / module).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        banned = {n for n in imported
                  if n.split(".")[0] in {"fastapi", "starlette", "http",
                                         "urllib", "optionspilot.ui"}}
        assert not banned, f"{module} imports transport machinery: {sorted(banned)}"

    def test_no_status_code_attribute_anywhere_in_the_hierarchy(self):
        """A status on the exception moves the mapping decision out of the
        transport and into the domain, which is what C8 exists to prevent."""
        for cls in [ServiceError, *_concrete()]:
            for attr in ("status", "status_code", "http_status"):
                assert not hasattr(cls, attr), (
                    f"{cls.__name__} carries {attr}; HTTP status is the "
                    "transport's decision (V0.9.2-C8)")

    def test_no_status_number_appears_in_the_code(self):
        """Matched on the AST, not the source text.

        The first version searched the raw source for "404" and "422" and
        failed on this module's own docstring, which explains the transport
        behaviour it exists to replace. A test a docstring can break is
        measuring the wrong thing — the same correction `test_architecture.py`
        and V0.9.1-C5 both record. Integer literals in the HTTP range are the
        actual hazard; prose about them is the documentation.
        """
        offenders = [
            node.value
            for node in ast.walk(ast.parse(inspect.getsource(service_errors)))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and 100 <= node.value <= 599
        ]
        assert not offenders, (
            f"HTTP-status-shaped literals in services/errors.py: {offenders}; "
            "status is the transport's decision (V0.9.2-C8)")
