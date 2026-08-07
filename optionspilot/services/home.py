"""HomeService — the whole Home destination as one payload.

Home reads six regions (`UI_V2_WIREFRAMES.md` §2.4) from four different owners:
the portfolio, the intelligence engine, the scan state, and facts only the host
knows (whether trading is halted, whether an order was refused, whether the data
provider is degraded). This service is the one place that assembles them.

**One request, not six.** Home's whole claim is that it answers three questions
in the first second (§2.1). Six round trips means six chances to arrive late and
six independently-shifting regions, which is the layout jump the milestone
exists to eliminate. The regions still fail independently — `errors` names the
ones that could not be read — but they arrive together.

**It computes nothing that anyone else owns.** The status line comes from
`statusline`, open risk and the account from `PortfolioService`, the ranking in
`next_actions` from `intelligence/` verbatim. That is P10, and it is the reason
this file is assembly rather than arithmetic: a second place deriving win rate
or exposure would eventually disagree with the first, and neither screen would
be able to prove it was the correct one.

**The host supplies what only the host knows.** `facts` is a zero-argument
callable returning a `StatusInputs`-shaped record of halt, rejection, degraded
provider and market clock. It is injected rather than reached for because
`services/` may not import `risk/` (`tests/test_architecture.py`), and because a
backtest or a replay host has entirely different answers to those questions.
"""

from __future__ import annotations

from optionspilot.intelligence import stats
from optionspilot.services.statusline import StatusInputs, status_line
from optionspilot.services.viewmodels import HomeView, WinRateView

#: Closed trades needed before a win rate is stated as a number.
#:
#: `UI_V2_DESIGN.md` §5.3 gives the example "not enough trades yet (12 of 30)",
#: which is `stats.MIN_SAMPLE_HIGH` — the same ladder `intelligence/` already
#: judges its own claims against. Reused rather than restated so a headline card
#: cannot be more confident than the analysis engine about the same history.
#:
#: NOTE — the wireframe's empty state (§2.9) draws this card as "0 of 5", which
#: is `MIN_SAMPLE_LOW` and is the threshold for the *intelligence* region (H4),
#: not for the win-rate metric. The two documents disagree; the prose rule and
#: the existing ladder win over an ASCII sketch. Recorded in the M3 report.
MIN_TRADES_FOR_WIN_RATE = stats.MIN_SAMPLE_HIGH

#: How many ranked items H4 shows. The engine ranks with a false-discovery
#: correction already applied; the UI shows the top of that ranking and does not
#: re-rank, filter or extend it (§2.4, "region H4 is the specification-critical
#: one"). Three because a recommendation list longer than three is a list nobody
#: reads (`UI_V2_DESIGN.md` §5.4).
MAX_NEXT_ACTIONS = 3


def win_rate_view(rate: float | None, trades: int) -> WinRateView:
    """Whether the win rate may be stated, given how many trades produced it.

    Takes the rate rather than the wins and losses on purpose: `PortfolioService`
    already computes it and is its one owner (P10). This function's only job is
    the evidence question — a win rate over four trades is a number the
    arithmetic supports and the evidence does not.

    Below the floor `rate` comes back `None` with the counts, so the card can
    say "3 of 30" — an expectation a user can act on — rather than "0%", which
    is a claim about them the data cannot carry.
    """
    trades = int(trades or 0)
    if trades < MIN_TRADES_FOR_WIN_RATE or rate is None:
        return WinRateView(rate=None, trades=max(0, trades),
                           needed=MIN_TRADES_FOR_WIN_RATE, sufficient=False)
    return WinRateView(rate=round(float(rate), 4), trades=trades,
                       needed=MIN_TRADES_FOR_WIN_RATE, sufficient=True)


class HomeService:
    """Assembles Home. Every collaborator is injected and duck-typed."""

    def __init__(self, *, portfolio, facts, performance, intelligence=None,
                 watchlist=None, equity=None, working_orders=None):
        self._portfolio = portfolio
        self._facts = facts
        # Zero-arg: the host binds the clock. A service that held its own would
        # answer from a different "now" than the app around it — the reason
        # `ChartService` takes an injected clock too.
        self._performance = performance
        self._intelligence = intelligence
        self._watchlist = watchlist
        self._equity = equity
        self._working_orders = working_orders

    # ── regions ──────────────────────────────────────────────────────────────

    def _next_actions(self) -> list:
        """`intelligence/`'s ranking, truncated. Never re-ordered.

        Returns `None` — not `[]` — when the engine could not be read, because
        "no findings" and "I could not look" are different answers and §2.10
        requires the second to be visible. A silent empty panel is
        indistinguishable from "nothing is wrong", which is the one thing this
        region may not imply when it does not know.
        """
        if self._intelligence is None:
            return []
        actions = self._intelligence()
        if actions is None:
            return None
        return list(actions)[:MAX_NEXT_ACTIONS]

    def view(self) -> HomeView:
        """The whole destination. Region failures are named, never swallowed."""
        errors: list[str] = []

        def region(name, fn, fallback):
            try:
                return fn()
            except Exception:  # noqa: BLE001 - a region fails, Home does not
                errors.append(name)
                return fallback

        facts = region("status", self._facts, StatusInputs())
        account = region("metrics", self._portfolio.account, None)
        risk = region("risk", self._portfolio.open_risk, None)
        performance = region("metrics", self._performance, None)
        positions = region("positions", self._portfolio.positions, [])
        working = (region("positions", self._working_orders, [])
                   if self._working_orders else [])
        actions = region("next_actions", self._next_actions, None)
        equity = region("equity", self._equity, []) if self._equity else []
        watchlist = (region("watchlist", self._watchlist, [])
                     if self._watchlist else [])

        return HomeView(
            status=status_line(facts).to_dict(),
            account=account.to_dict() if account is not None else None,
            open_risk=risk.to_dict() if risk is not None else None,
            today_pnl=getattr(performance, "daily_pnl", None),
            buying_power=getattr(performance, "buying_power", None),
            win_rate=win_rate_view(getattr(performance, "win_rate", None),
                                   getattr(performance, "trades", 0)).to_dict(),
            positions=[p.to_dict() for p in positions],
            working_orders=list(working),
            next_actions=actions,
            equity=list(equity),
            watchlist=list(watchlist),
            errors=sorted(set(errors)),
        )
