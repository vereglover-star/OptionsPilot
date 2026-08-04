"""services — the platform-independent application layer.

Where this sits, and why it exists:

    analysis · engine · risk · broker · journal · learning · experience
    coach · intelligence · data · notify            (the domain)
                          |
                      orchestrator                  (composition of a cycle)
                          |
                      SERVICES                      <- this package
                          |
              ui/server.py  ·  future mobile host   (transport)

Before V0.7.0 the middle box did not exist. `ui/server.py` was 1,700 lines
holding both halves: FastAPI routing *and* the decisions about what a client
should be shown — which twelve of thirty-eight metrics are the headline, how a
drawdown is computed, what four buckets a pasted list of tickers falls into, how
many periods of a five-year series to ship. All of it correct, all of it
reachable only by importing a web framework.

That is fine for one client and it is the whole obstacle to a second. A phone
asking "what is my max drawdown" had exactly two options: import FastAPI, or
recompute it — and the second is how two screens end up disagreeing about one
number, which is the failure this codebase has already paid for three times
(`data/health.py` V0.5.3, the settings ranking V0.5.7, the guide catalogue
V0.6.1).

**The rule, and it is enforced by `tests/test_architecture.py`:**

    Nothing under `services/` may import `ui/`, FastAPI, Starlette, pywebview,
    uvicorn, or any other transport or presentation dependency.

A service takes injected, duck-typed collaborators and returns frozen view
models of primitives. That is the concrete answer to "if Flutter needed this
tomorrow, what interface would it want?" — it would want exactly this, plus a
serializer, and it would need no Python object from any other package.

**What this layer does NOT do**, deliberately:

  * It does not own the cycle. `orchestrator.run_cycle()` remains the only place
    engine + risk + broker + coach + notify are composed, and no service
    duplicates any part of it.
  * It does not gate trades. `RiskManager` is still the only entry gate and
    `OrderManager` still the only execution path; a service that could open a
    position would be a second gatekeeper, which is the same class of defect as
    a second owner of a counter.
  * It does not persist anything itself. Every store arrives injected.
"""

from optionspilot.services.charts import ChartService
from optionspilot.services.intelligence import IntelligenceService
from optionspilot.services.notifications import (
    CATALOGUE, NotificationService, NotificationView,
)
from optionspilot.services.portfolio import PortfolioService
from optionspilot.services.registry import ServiceRegistry
from optionspilot.services.sync import (
    CLIENT_TRAPPED, INVENTORY, LocalSyncProvider, SyncDomain, SyncPolicy,
    SyncProvider,
)
from optionspilot.services.runtime import (
    BackgroundRuntime, BackgroundTask, RuntimeSnapshot, TaskSpec,
)
from optionspilot.services.idempotency import IdempotencyStore
from optionspilot.services.contracts import (
    AnonymousLocalAuth, AuthProvider, ClientCapabilities, DeviceTokenAuth,
    Principal, RequestContext, SyncRevision, SyncSnapshot,
    context_from_headers,
    error_envelope, success_envelope,
)
from optionspilot.services.viewmodels import (
    AccountView, HostView, PerformanceView, PnLWindowsView, PositionView,
    ViewModel, WatchlistEditView, WatchlistView, WorkspaceView,
)
from optionspilot.services.watchlist import WatchlistService
from optionspilot.services.workspace import WorkspaceService

__all__ = [
    "ServiceRegistry",
    "ChartService",
    "IntelligenceService", "NotificationService", "PortfolioService",
    "WatchlistService", "WorkspaceService",
    "CATALOGUE", "NotificationView",
    "INVENTORY", "CLIENT_TRAPPED", "LocalSyncProvider", "SyncDomain",
    "SyncPolicy", "SyncProvider",
    "BackgroundRuntime", "BackgroundTask", "RuntimeSnapshot", "TaskSpec",
    "IdempotencyStore",
    "AnonymousLocalAuth", "AuthProvider", "ClientCapabilities", "DeviceTokenAuth",
    "Principal", "RequestContext", "SyncRevision", "context_from_headers",
    "SyncSnapshot", "success_envelope", "error_envelope",
    "ViewModel", "AccountView", "HostView", "PerformanceView",
    "PnLWindowsView", "PositionView", "WatchlistEditView", "WatchlistView",
    "WorkspaceView",
]
