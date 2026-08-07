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


@dataclass(frozen=True, slots=True)
class WinRateView(ViewModel):
    """A win rate, or an honest statement that there is not one yet.

    `rate` is `None` below the evidence floor and `trades`/`needed` are always
    populated, so a card can render "3 of 30" instead of "0%". A zero win rate
    on three trades is a false statement about a person, and this is the third
    place in the codebase to refuse to make one — after `profit_factor` and
    `intelligence/`'s unassessable behaviours.
    """

    rate: float | None
    trades: int
    needed: int
    sufficient: bool


@dataclass(frozen=True, slots=True)
class HomeView(ViewModel):
    """The whole Home destination in one payload.

    `errors` names the regions that could not be read. Home never fails as a
    whole (`UI_V2_WIREFRAMES.md` §2.10) because its regions have independent
    sources, so a failure is a named region rather than an exception — and a
    client can render that region's error state while the rest of the screen
    stays useful.

    `next_actions` is `None`, not `[]`, when the intelligence engine could not
    be read. "No findings" and "I could not look" are different answers, and
    §2.10 requires the second to be visible: a silent empty panel is
    indistinguishable from "nothing is wrong".
    """

    status: dict
    account: dict | None
    open_risk: dict | None
    today_pnl: float | None
    buying_power: float | None
    win_rate: dict
    positions: list
    working_orders: list
    next_actions: list | None
    equity: list
    watchlist: list
    errors: list


@dataclass(frozen=True, slots=True)
class StatusLineView(ViewModel):
    """Home's first line, and the system's single self-report.

    `case` is carried so a client can style by state without re-deriving which
    of the eight situations applied — a second derivation would eventually
    disagree with the first, and the two would be rendering different opinions
    of the same moment.

    `needs_you` exists so calm text and an alarm badge cannot appear together.
    The sentence's whole value is that it never claims nothing needs you unless
    nothing does; a client that had to parse the prose to find that out would
    get it wrong. See `services/statusline.py`.
    """

    text: str
    case: str
    needs_you: bool


@dataclass(frozen=True, slots=True)
class OpenRiskView(ViewModel):
    """How much of the account is currently exposed. New in M3.

    **`dollars` is the MAXIMUM loss, and that is a deliberate choice.** Every
    position this broker can hold is long — `PaperBroker.open_position` refuses
    `quantity < 1` and `close_position` caps at the quantity held — so the most
    a position can lose is the premium currently in it, and the mark is that
    premium. The number is therefore a measurement, not a model.

    The alternative was loss-to-stop, which `RiskManager._position_size`
    estimates from delta when it sizes a trade. It was rejected for Home for two
    reasons. The stop is on the UNDERLYING, so converting it to an option loss
    needs a delta — and the only delta a `Position` persists is the one from its
    entry snapshot, which is stale by exactly as long as the position has been
    open. Reporting a live risk figure from a stale greek is stating what cannot
    be evidenced (`UI_V2_DESIGN.md` P3). The second reason is direction: a
    maximum overstates exposure, and if this number must be wrong, P6 says be
    wrong in the direction that does not surprise someone about money.

    `pct_of_account` is `None`, never `0.0`, when equity is zero or negative.
    "0% of your account is at risk" is false for an account with positions and
    no equity; there is simply no percentage to state. Same rule as
    `PerformanceView.profit_factor`.

    `marked` counts how many positions had a live mark. When it is below
    `positions`, the rest fell back to their entry price — the same fallback
    `PortfolioService.positions()` uses, so the two never disagree — and the
    figure is a floor rather than a current valuation. A client that wants to
    say "as of" has what it needs to know it must.
    """

    dollars: float
    pct_of_account: float | None
    positions: int
    marked: int


# ── trade ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QuickPickView(ViewModel):
    """What a quick-pick chip resolved to, or why it could not. New in M4.

    **`ok` and `reason` are both carried, and neither is derivable from the
    other by a client.** A resolution can fail for reasons that are about the
    market rather than about the user — no listed expirations, no spot price,
    an expiry that lists no puts — and each of those needs a different sentence
    on screen. Returning `None` would make every one of them read as "nothing
    happened", which is the state a user responds to by clicking the chip
    again.

    Same rule as `StatusLineView.needs_you` and `intelligence/`'s
    `assessable=False` with the reason quoted: the answer "I could not" is a
    first-class answer, and it is not the same answer as "there is nothing".

    `strike` is `None` rather than `0.0` when unresolved, because a strike of
    zero is a price and this is an absence. The quote fields are carried
    straight from the chain row so the ticket can populate without a second
    request against a chain that may already have moved.
    """

    ok: bool = False
    intent: str = ""
    right: str = ""
    expiration: str = ""
    dte: int | None = None
    strike: float | None = None
    mid: float | None = None
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ReviewView(ViewModel):
    """The consequence restatement of §6.5, as data. New in M4.

    **Every numeric field is paired with a note, and the note is not
    decoration.** §6.5's five elements are written for an opening buy; a
    `sell_to_close` has proceeds rather than a cost, adds no new maximum loss,
    and has no breakeven. Those fields are therefore `None` and their note says
    why. Printing "Most you can lose: $0.00" on a closing order would be the
    same class of error as scoring a metric that could not be measured — a
    confidently wrong number, which this codebase treats as worse than an
    absent one because the user acts on it.

    `premium` is the ENGINE's expected fill price, not the mid: `broker/paper.py`
    fills a market buy at `ask * (1 + slippage_pct)` and a market sell at
    `bid * (1 - slippage_pct)`. Quoting a mid here would describe a trade the
    system is not going to place.

    `fill_note` is the honesty line §6.5 requires beneath the five elements.
    It is accuracy rather than a disclaimer: it is what prevents the "why
    didn't I get that price" confusion that follows every paper fill.
    """

    sentence: str = ""
    opening: bool = True
    premium: float | None = None
    cost: float | None = None
    cost_note: str = ""
    proceeds: float | None = None
    max_loss: float | None = None
    max_loss_note: str = ""
    breakeven: float | None = None
    breakeven_note: str = ""
    spot: float | None = None
    position_pct: float | None = None
    position_note: str = ""
    if_nothing: str = ""
    fill_note: str = ""
    #: Buying-power impact (M4-C4). Distinct from `position_pct`, which is a
    #: share of ACCOUNT VALUE — equity includes the marked value of open
    #: positions, buying power is the cash actually available to spend, and on
    #: an account holding anything they are different numbers. The ticket
    #: states the second because it answers "can I afford this"; §6.5's element
    #: 4 states the first because it answers "is this too big".
    #:
    #: It lives here rather than as a division in `index.html` for the reason
    #: PRODUCT_STANDARDS.md §3.2 gives about the fill price: the ticket and the
    #: review modal state the same figure, and two implementations of one
    #: figure is the drift this codebase has paid for four times.
    buying_power: float | None = None
    buying_power_pct: float | None = None
    buying_power_after: float | None = None
    buying_power_note: str = ""


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
        # ``@dataclass(slots=True)`` can replace the class object on Python
        # versions used by CI, making zero-argument ``super()`` retain the
        # pre-transform class cell.  Call the stable serialization boundary
        # explicitly; this preserves the legacy wire rule without coupling it
        # to dataclass implementation details.
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
    #: The selected expiry ("" for none) and chain row (None for none) — UI V2
    #: M1-C2. The contract is a plain dict of primitives, not an
    #: `OptionContract`: this file's first rule is that a view model holds no
    #: domain object, and a *selection* is not a market snapshot. It names the
    #: instrument the user picked; whether that instrument still has a bid is
    #: the chain's question, asked fresh every time.
    expiry: str
    contract: dict | None
    #: How much of the interface is revealed (1 Guided - 4 Pro) — UI V2 M1-C3.
    #: It is STORED apart from the workspace document, under its own
    #: `settings.json` key with its own DEVICE_ONLY sync policy, because a user
    #: may want Guided on a phone and Full on a desktop. It is SERVED here
    #: because `UI_V2_DESIGN.md` §4.5 lists it as a context-continuity
    #: guarantee alongside the symbol and the timeframe, and a client needs all
    #: of them in the same breath to render a first frame. Transport and
    #: storage are allowed to disagree; conflating them is what would cost a
    #: round trip on every launch.
    #:
    #: No default: the value comes from the store on every read, and a default
    #: here would be a second copy of `config.runtime.DEFAULT_SURFACE_LEVEL`
    #: that could disagree with the first.
    surface_level: int
    #: Whether this device renders the UI V2 shell (M2). Served here for the
    #: same reason as `surface_level` and one stronger one: it decides which
    #: frame the client draws at all, so a second round trip for it would mean
    #: rendering the old shell and then replacing it — a visible flash on every
    #: launch. Stored device-locally, outside the workspace document.
    shell_v2: bool
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
