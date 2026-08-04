"""The root of every failure this application raises deliberately.

One class, and it does nothing — which is the point. `OptionsPilotError` exists
so a caller can write ``except OptionsPilotError`` and mean *"a failure this
code chose to signal"*, as opposed to ``except Exception``, which also catches
the `KeyError` from a typo three frames down.

That distinction is the whole of finding H-7. The v1 transport currently reads::

    except ValueError as exc:  -> 422 validation_error
    except KeyError as exc:    -> 404 not_found

Builtin types are the wrong evidence: `ValueError` is raised by `int()`, by
pandas, by the standard library and by our own validation, and only the last of
those is the client's fault. A dict-lookup bug in a service therefore reaches
the user as *"404, not found"* while the traceback that would have identified it
is discarded. Anything deriving from this class is a deliberate signal and can be
reported to a client; anything that does not is a defect and belongs in the log
with a 500.

Deliberately in `core` rather than `services`: `broker`, `risk`, `journal` and
`data` may all eventually want to raise something classifiable, and none of them
may import `services` (`tests/test_architecture.py`). `core` is the only layer
every one of them already depends on.
"""

from __future__ import annotations


class OptionsPilotError(Exception):
    """Base class for deliberate, reportable application failures."""
