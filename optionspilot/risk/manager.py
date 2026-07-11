"""RiskManager — the gatekeeper between the AI engine and the broker.

The engine cannot place an order; it can only *propose* a TradePlan. Every
plan passes through `approve()`, which enforces, in order:

  1. circuit breaker (halted by an earlier breach)
  2. weekday + trading-hours window (Eastern Time)
  3. daily trade limit
  4. max open positions
  5. minimum risk/reward
  6. cooldown after a loss
  7. position sizing (risk % of equity vs estimated loss per contract)

Breaches recorded via `record_closed_trade()` / `update_equity()` trip the
circuit breaker:
  - daily loss limit / max consecutive losses -> halted until the next ET day
  - weekly loss limit                          -> halted until next ET Monday
  - max drawdown from peak equity              -> halted until manual
    `reset_halt()` — a human must look at the system before it trades again.

Everything is measured against *current equity*, so limits tighten as the
account draws down. The manager fails closed: anything it cannot evaluate is
a veto, not a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from optionspilot.config.settings import RiskConfig
from optionspilot.core.logging_setup import get_logger
from optionspilot.core.models import TradePlan

log = get_logger("risk")

ET = ZoneInfo("America/New_York")
DELTA_LOSS_SAFETY = 1.25   # gamma makes delta-estimated losses optimistic; pad them


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    quantity: int = 0
    veto: str = ""                      # non-empty iff not approved
    notes: tuple[str, ...] = ()         # sizing math etc., for the journal


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self._cfg = cfg
        self._equity = cfg.starting_balance
        self._peak_equity = cfg.starting_balance
        self._closed: list[tuple[datetime, float]] = []   # (utc ts, pnl)
        self._entries: list[datetime] = []
        self._consecutive_losses = 0
        self._halt_reason = ""
        self._halt_until: datetime | None = None          # None while halted = manual reset
        self._halt_manual_reset = False

    # ── state feeds ──────────────────────────────────────────────────────────

    def record_entry(self, ts: datetime) -> None:
        self._entries.append(ts)

    def record_closed_trade(self, ts: datetime, pnl: float) -> None:
        self._closed.append((ts, pnl))
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        self._check_loss_breaches(ts)

    def update_equity(self, equity: float, ts: datetime) -> None:
        self._equity = equity
        self._peak_equity = max(self._peak_equity, equity)
        drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100
        if drawdown_pct >= self._cfg.max_drawdown_pct and not self._halt_reason:
            self._halt(
                f"max drawdown breached: {drawdown_pct:.1f}% >= "
                f"{self._cfg.max_drawdown_pct}% from peak {self._peak_equity:.2f}",
                until=None, manual=True, ts=ts,
            )

    def reset_halt(self) -> None:
        """Manual human override — the only way out of a drawdown halt."""
        log.warning("risk halt manually reset (was: %s)", self._halt_reason or "none")
        self._halt_reason = ""
        self._halt_until = None
        self._halt_manual_reset = False

    # ── the gate ─────────────────────────────────────────────────────────────

    def approve(self, plan: TradePlan, open_positions: int, now: datetime) -> RiskDecision:
        cfg = self._cfg
        self._clear_expired_halt(now)

        if self._halt_reason:
            return self._veto(f"trading halted: {self._halt_reason}")

        now_et = now.astimezone(ET)
        if now_et.weekday() >= 5:
            return self._veto("market closed (weekend)")
        if not (cfg.trading_start <= now_et.time() < cfg.trading_end):
            return self._veto(
                f"outside trading hours ({now_et.time():%H:%M} ET, "
                f"window {cfg.trading_start:%H:%M}-{cfg.trading_end:%H:%M})"
            )

        entries_today = sum(1 for t in self._entries
                            if t.astimezone(ET).date() == now_et.date())
        if entries_today >= cfg.daily_trade_limit:
            return self._veto(f"daily trade limit reached ({cfg.daily_trade_limit})")

        if open_positions >= cfg.max_open_positions:
            return self._veto(f"max open positions reached ({cfg.max_open_positions})")

        if plan.risk_reward < cfg.min_risk_reward:
            return self._veto(
                f"risk/reward {plan.risk_reward:.2f} below minimum {cfg.min_risk_reward}"
            )

        last_loss = next((t for t, pnl in reversed(self._closed) if pnl < 0), None)
        if last_loss is not None:
            elapsed = (now - last_loss).total_seconds() / 60
            if elapsed < cfg.cooldown_minutes_after_loss:
                return self._veto(
                    f"cooldown after loss: {elapsed:.0f} of "
                    f"{cfg.cooldown_minutes_after_loss} minutes elapsed"
                )

        quantity, loss_per_contract, sizing_note = self._position_size(plan)
        if quantity < 1:
            return self._veto(
                f"risk budget too small: {sizing_note}"
            )
        log.info("approved %s x%d (%s)", plan.contract.symbol, quantity, sizing_note)
        return RiskDecision(approved=True, quantity=quantity, notes=(sizing_note,))

    # ── internals ────────────────────────────────────────────────────────────

    def _position_size(self, plan: TradePlan) -> tuple[int, float, str]:
        cfg = self._cfg
        risk_budget = self._equity * cfg.risk_per_trade_pct / 100
        premium_risk = plan.entry_price * 100          # worst case: premium to zero
        delta = abs(plan.contract.delta)
        if delta > 0 and plan.spot > 0 and plan.stop_underlying > 0:
            stop_distance = abs(plan.spot - plan.stop_underlying)
            estimated = delta * stop_distance * 100 * DELTA_LOSS_SAFETY
            loss_per_contract = min(premium_risk, estimated) if estimated > 0 else premium_risk
        else:
            loss_per_contract = premium_risk
        if loss_per_contract <= 0:
            return 0, 0.0, "loss per contract is zero — cannot size"
        quantity = min(int(risk_budget // loss_per_contract), cfg.max_contracts)
        note = (f"risk budget {risk_budget:.2f} ({cfg.risk_per_trade_pct}% of "
                f"{self._equity:.2f}), est. loss/contract {loss_per_contract:.2f}, "
                f"size {quantity} (max {cfg.max_contracts})")
        return quantity, loss_per_contract, note

    def _check_loss_breaches(self, ts: datetime) -> None:
        cfg = self._cfg
        ts_et = ts.astimezone(ET)

        daily_pnl = sum(p for t, p in self._closed
                        if t.astimezone(ET).date() == ts_et.date())
        daily_limit = self._equity * cfg.max_daily_loss_pct / 100
        if daily_pnl <= -daily_limit:
            self._halt(
                f"daily loss limit: {daily_pnl:.2f} <= -{daily_limit:.2f}",
                until=_next_et_day(ts_et), ts=ts,
            )
            return

        year_week = ts_et.isocalendar()[:2]
        weekly_pnl = sum(p for t, p in self._closed
                         if t.astimezone(ET).isocalendar()[:2] == year_week)
        weekly_limit = self._equity * cfg.max_weekly_loss_pct / 100
        if weekly_pnl <= -weekly_limit:
            self._halt(
                f"weekly loss limit: {weekly_pnl:.2f} <= -{weekly_limit:.2f}",
                until=_next_et_monday(ts_et), ts=ts,
            )
            return

        if self._consecutive_losses >= cfg.max_consecutive_losses:
            self._halt(
                f"{self._consecutive_losses} consecutive losses "
                f"(limit {cfg.max_consecutive_losses})",
                until=_next_et_day(ts_et), ts=ts,
            )

    def _halt(self, reason: str, until: datetime | None, ts: datetime,
              manual: bool = False) -> None:
        self._halt_reason = reason
        self._halt_until = until
        self._halt_manual_reset = manual
        log.warning(
            "TRADING HALTED at %s: %s (resumes: %s)",
            ts.isoformat(), reason,
            until.isoformat() if until else "manual reset required",
        )

    def _clear_expired_halt(self, now: datetime) -> None:
        if self._halt_reason and not self._halt_manual_reset \
                and self._halt_until is not None and now >= self._halt_until:
            log.info("risk halt expired (%s)", self._halt_reason)
            self._halt_reason = ""
            self._halt_until = None

    @staticmethod
    def _veto(reason: str) -> RiskDecision:
        log.info("VETO: %s", reason)
        return RiskDecision(approved=False, veto=reason)

    def status(self) -> dict:
        return {
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "consecutive_losses": self._consecutive_losses,
            "halted": bool(self._halt_reason),
            "halt_reason": self._halt_reason,
            "halt_until": self._halt_until.isoformat() if self._halt_until else None,
        }


def _next_et_day(ts_et: datetime) -> datetime:
    next_day = datetime.combine(ts_et.date() + timedelta(days=1), time(0, 0), tzinfo=ET)
    return next_day


def _next_et_monday(ts_et: datetime) -> datetime:
    days_ahead = 7 - ts_et.weekday()
    next_monday = datetime.combine(
        ts_et.date() + timedelta(days=days_ahead), time(0, 0), tzinfo=ET
    )
    return next_monday
