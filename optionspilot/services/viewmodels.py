"""View models — UI-ready data, and nothing else.

The contract, which is the whole point of the file:

  * A view model is a frozen dataclass of primitives (str, float, int, bool,
    None, and lists/dicts of those). It holds **no domain object**: no
    `Position`, no `TradeRecord`, no provider, no pydantic config section, no
    SQLite row. A client that receives one needs no OptionsPilot import to read
    it, which is the definition of being ready for a client written in another
    language.
  * `to_dict()` is the serialisation boundary and the ONLY one. If a value
    cannot survive `json.dumps(..., allow_nan=False)`, it is the view model's
    job to have already dealt with it — see `finite()` below, and the
    `intelligence/models.py::_finite` precedent it deliberately mirrors.
  * A view model computes **nothing**. Rounding and "None instead of infinity"
    are presentation; a win rate is not. If a field needs arithmetic over more
    than one input, that arithmetic belongs in the service that builds it.

Why frozen: a view model handed to two renderers that both mutate it is the
"two objects tracking one fact" failure this codebase has paid for three times
(`data/health.py` V0.5.3, the settings ranking V0.5.7, the guide catalogue
V0.6.1). Freezing makes the second mutation a crash instead of a drift.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


def finite(value: float | int | None, digits: int | None = None):
    """A JSON-safe number, or None.

    `inf` and `nan` are legitimate *computed* values here — profit factor is
    genuinely infinite for a period with no losing trades — and both are
    illegal JSON. `json.dumps` emits bare `Infinity`/`NaN` and a browser's
    `JSON.parse` dies on them, so the conversion has to happen before
    serialisation rather than being caught after it.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


@dataclass(frozen=True, slots=True)
class ViewModel:
    """Base: gives every view model the same serialisation boundary."""

    def to_dict(self) -> dict:
        return asdict(self)


# ── portfolio ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PositionView(ViewModel):
    """One open position as a screen shows it.

    `managed_by` is carried deliberately: it is the AI-vs-manual boundary the
    project's `managed_by` discipline rests on, and a client that cannot see it
    cannot render the two differently — which is how a user ends up trying to
    hand-manage a position the PositionManager owns.
    """

    contract: str
    underlying: str
    expiration: str
    strike: float
    right: str
    managed_by: str
    direction: str
    quantity: int
    avg_price: float
    mark: float
    unrealized: float
    entry_spot: float
    stop: float | None
    target: float | None
    opened_at: str


@dataclass(frozen=True, slots=True)
class AccountView(ViewModel):
    cash: float
    equity: float
    realized_pnl: float
    starting_balance: float


@dataclass(frozen=True, slots=True)
class PerformanceView(ViewModel):
    """Realised performance over the whole journal.

    `profit_factor` is `None` — not a large number, not zero — when there are no
    losing trades. A trader with three wins and no losses does not have a
    profit factor of 999; they have insufficient evidence for one, and the
    `intelligence/` layer's first-class treatment of that answer applies just as
    much to a headline card.
    """

    cash: float
    buying_power: float
    portfolio_value: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float
    total_return_pct: float
    trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float | None
    max_drawdown_pct: float
    equity_history: list = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PnLWindowsView(ViewModel):
    today: float
    week: float
    month: float


# ── watchlist ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WatchlistView(ViewModel):
    watchlist: list
    pinned: list
    favorites: list
    max: int
    meta: dict
    quotes: dict
    signals: dict


@dataclass(frozen=True, slots=True)
class WatchlistEditView(ViewModel):
    """The outcome of one add attempt.

    Four outcome buckets rather than a boolean, because "AAPL was already there"
    and "AAPLL is not a ticker" and "you are at the 30-symbol cap" are three
    different things a user must be told apart — and a single `error` string
    for a paste of twelve symbols cannot say which of them was which.
    """

    added: list
    invalid: list
    duplicates: list
    over_cap: list
    names: dict
    error: str | None = None

    def to_dict(self) -> dict:
        doc = ViewModel.to_dict(self)
        if doc.get("error") is None:
            doc.pop("error")     # absent, not null — the pre-V0.7.0 wire shape
        return doc


# ── workspace ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceView(ViewModel):
    """Where the user was, so a second device (or a reinstall) can resume.

    Deliberately holds no market data and no account data — a workspace is a
    statement about the user's attention, and mixing the two would make a
    "restore my layout" request also a portfolio read.
    """

    tab: str
    sidebar_collapsed: bool
    symbol: str
    timeframe: str
    indicators: list
    extended_hours: bool
    auto_follow: bool
    watchlist_sort: str
    ticket_chart_open: bool
    recent_symbols: list
    layouts: dict
    updated: str | None = None


# ── host / platform ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HostView(ViewModel):
    """What this build can do, for a diagnostics screen and for a client
    deciding which surfaces to offer at all."""

    host: str
    python_platform: str
    capabilities: list
    missing: list
    notes: dict
    implemented: bool
