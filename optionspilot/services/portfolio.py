"""PortfolioService — positions, account, and realised performance.

Everything in this file was computed inside `ui/server.py`. Maximum drawdown,
profit factor, win rate, average win and average loss are not transport
concerns; they are the numbers a trader judges themselves by, and they were
being derived in the same function that decides an HTTP response shape. A
second client asking "what is my max drawdown" had no way to get the same
answer except by re-implementing the peak-tracking loop — and a second
implementation of a drawdown loop that disagrees with the first is a bug nobody
can see, because both look plausible.

Two properties are preserved exactly, because both are load-bearing:

  * **What is computed under the lock and what is not.** The orchestrator is not
    thread-safe, so the broker read, the journal read and the day-boundary sum
    happen inside the caller's lock and the arithmetic happens outside it. That
    split is reproduced verbatim; widening the lock to cover the arithmetic
    would let a slow statistics pass block a scan.
  * **`profit_factor` is `None`, not a big number, when there are no losses.**
    Division by a zero gross loss is infinite, `Infinity` is not valid JSON, and
    the honest answer to "what is your profit factor with no losing trades" is
    that there is not enough evidence for one. `intelligence/` treats
    insufficient evidence as a first-class answer; a headline card does not get
    to be sloppier than the analysis engine.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from optionspilot.services.viewmodels import (
    AccountView, PerformanceView, PnLWindowsView, PositionView,
)

ET = ZoneInfo("America/New_York")


def position_view(position, mark: float) -> PositionView:
    """One broker position projected for a client.

    Rounding happens here rather than in the client because two clients that
    round differently show two different unrealized P&Ls for one position, and
    the user has no way to tell which is the real one.
    """
    return PositionView(
        contract=position.contract.symbol,
        underlying=position.contract.underlying,
        expiration=position.contract.expiration.isoformat(),
        strike=position.contract.strike,
        right=position.contract.right.value,
        managed_by=position.managed_by,
        direction=position.direction.value,
        quantity=position.quantity,
        avg_price=round(position.avg_price, 2),
        mark=round(mark, 2),
        unrealized=round(position.unrealized_pnl(mark), 2),
        entry_spot=round(position.entry_spot, 2),
        stop=position.stop_current,
        target=position.target,
        opened_at=position.opened_at.isoformat(),
    )


def max_drawdown_pct(equity_history, starting_balance: float) -> float:
    """Peak-to-trough decline, in percent, over an equity curve.

    Seeded at `starting_balance` rather than the first recorded point: an
    account that has only ever lost money has no recorded peak above its
    opening balance, and starting the peak at the first *sample* would report
    a 0% drawdown for it.
    """
    peak = starting_balance
    worst = 0.0
    for _, equity in equity_history:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100)
    return worst


def pnl_windows(trades, now_et: datetime) -> PnLWindowsView:
    """Realised P&L for today, this week and this month.

    Windowed on ENTRY time, matching the behaviour shipped since V0.3 — a trade
    entered Friday and closed Monday counts to the week it was taken. That is
    arguably the wrong convention for a "this week's P&L" card and it is
    deliberately not changed here: V0.7.0 moves code, it does not silently
    restate a number the user has been reading for six months.
    """
    day_start = datetime.combine(now_et.date(), time(0), tzinfo=ET)
    week_start = day_start - timedelta(days=now_et.weekday())
    month_start = day_start.replace(day=1)

    def since(start):
        return round(sum(t.pnl for t in trades if t.entry_ts >= start), 2)

    return PnLWindowsView(today=since(day_start), week=since(week_start),
                          month=since(month_start))


def setup_history(trades) -> dict:
    """Measured win rate per setup quality — the honest 'estimated probability
    of success'. Absent, not zero, for a quality with no closed trades."""
    buckets: dict[str, list[bool]] = {}
    for trade in trades:
        quality = trade.market_conditions.get("setup_quality")
        if quality:
            buckets.setdefault(quality, []).append(trade.is_win)
    return {q: {"trades": len(v), "win_rate": round(sum(v) / len(v), 3)}
            for q, v in buckets.items()}


class PortfolioService:
    """Account, positions and performance over an injected broker + journal.

    `broker` is duck-typed to the `Broker` interface, `trades` to a zero-arg
    callable returning `TradeRecord`s, and `lock` to a context manager. None of
    the three is imported: this service must be constructible against a replay,
    a backtest, or a future host that composes the stack differently — which is
    the concrete meaning of "platform-independent" for this layer.
    """

    def __init__(self, broker, trades, starting_balance: float, lock):
        self._broker = broker
        self._trades = trades
        self._starting_balance = starting_balance
        self._lock = lock

    # ── projections ──────────────────────────────────────────────────────────

    def positions(self) -> list[PositionView]:
        """Call under the caller's lock (the status payload already holds it)."""
        marks = (self._broker.current_marks()
                 if hasattr(self._broker, "current_marks") else {})
        return [position_view(p, marks.get(p.contract.symbol, p.avg_price))
                for p in self._broker.get_positions()]

    def account(self) -> AccountView:
        acct = self._broker.get_account()
        return AccountView(cash=acct.cash, equity=acct.equity,
                           realized_pnl=acct.realized_pnl,
                           starting_balance=self._starting_balance)

    def pnl_windows(self, now_et: datetime) -> PnLWindowsView:
        with self._lock:
            trades = self._trades()
        return pnl_windows(trades, now_et)

    def setup_history(self) -> dict:
        return setup_history(self._trades())

    # ── performance ──────────────────────────────────────────────────────────

    def performance(self, now_et: datetime) -> PerformanceView:
        """The full realised-performance card.

        The lock covers only the reads of mutable orchestrator-owned state; the
        statistics below run outside it. See the class docstring.
        """
        with self._lock:
            acct = self._broker.get_account()
            trades = self._trades()
            marks = (self._broker.current_marks()
                     if hasattr(self._broker, "current_marks") else {})
            unrealized = sum(
                p.unrealized_pnl(marks.get(p.contract.symbol, p.avg_price))
                for p in self._broker.get_positions()
            )
            history = (self._broker.equity_history()
                       if hasattr(self._broker, "equity_history") else [])
            day_start = datetime.combine(now_et.date(), time(0), tzinfo=ET)
            daily = sum(t.pnl for t in trades
                        if t.exit_ts.astimezone(ET) >= day_start)

        start = self._starting_balance
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        return PerformanceView(
            cash=acct.cash,
            # Options buying power is cash: this account has no margin, and
            # reporting anything else would invite position sizes it cannot fill.
            buying_power=acct.cash,
            portfolio_value=acct.equity,
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=acct.realized_pnl,
            daily_pnl=round(daily, 2),
            total_return_pct=round((acct.equity / start - 1) * 100, 2),
            trades=len(trades),
            win_rate=round(len(wins) / len(pnls), 4) if pnls else 0.0,
            avg_win=round(gross_win / len(wins), 2) if wins else 0.0,
            avg_loss=round(-gross_loss / len(losses), 2) if losses else 0.0,
            profit_factor=(round(gross_win / gross_loss, 2)
                           if gross_loss else None),
            max_drawdown_pct=round(max_drawdown_pct(history, start), 2),
            equity_history=history[-500:],
        )
