"""host — everything OptionsPilot needs from the machine it is running on.

The name is deliberate: this package is not "the desktop UI", it is *the host
platform*. Windows is one host. A Linux server running `serve` mode is another.
An iPhone running a future Flutter/SwiftUI client against a paired desktop is a
third, and the thing that makes it possible is that no business-logic module
ever asks the operating system a question directly — it asks a `HostAdapter`.

Two halves, and they are different kinds of thing:

  `capabilities.py`  A *declarative* answer to "what can a client on this kind
                     of device do at all?" — data, not code, and it includes
                     targets that do not exist yet (ios, android, web). This is
                     the design register: when someone asks "if Flutter needed
                     this tomorrow, what would it want?", the answer is a
                     `HostProfile` it can read.

  `adapter.py`       The *runtime* interface for the process we are actually
                     inside. `DesktopHost` is the only implementation today and
                     is the exact behaviour V0.6.1 shipped; a mobile host would
                     be a sibling, never an edit.

Nothing here imports anything above `core`. That is what lets a future mobile
backend host import `host` + `services` and get the whole application layer
without dragging in FastAPI, pywebview, or a chart library.
"""

from optionspilot.host.adapter import (
    DesktopHost, HeadlessHost, HostAdapter, current_host, set_host,
)
from optionspilot.host.capabilities import (
    HOST_PROFILES, Capability, HostProfile, profile_for,
)

__all__ = [
    "Capability", "HostProfile", "HOST_PROFILES", "profile_for",
    "HostAdapter", "DesktopHost", "HeadlessHost", "current_host", "set_host",
]
