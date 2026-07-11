"""Backtest report: metrics, equity curve, and JSON/HTML rendering."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from optionspilot.core.models import TradeRecord


@dataclass(slots=True)
class BacktestReport:
    symbol: str
    strategy: str
    start: datetime
    end: datetime
    initial_balance: float
    final_equity: float
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ── metrics ──────────────────────────────────────────────────────────────

    @property
    def net_profit(self) -> float:
        return round(self.final_equity - self.initial_balance, 2)

    @property
    def net_profit_pct(self) -> float:
        return round(self.net_profit / self.initial_balance * 100, 2)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return round(sum(1 for t in self.trades if t.is_win) / len(self.trades), 4)

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return round(gross_win / gross_loss, 2)

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return round(sum(wins) / len(wins), 2) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        return round(sum(losses) / len(losses), 2) if losses else 0.0

    @property
    def expectancy(self) -> float:
        if not self.trades:
            return 0.0
        return round(sum(t.pnl for t in self.trades) / len(self.trades), 2)

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = np.array([e for _, e in self.equity_curve])
        peaks = np.maximum.accumulate(eq)
        dd = (peaks - eq) / peaks
        return round(float(dd.max()) * 100, 2)

    @property
    def sharpe(self) -> float:
        """Annualized Sharpe from daily equity closes (rf = 0)."""
        if len(self.equity_curve) < 3:
            return 0.0
        s = pd.Series(
            [e for _, e in self.equity_curve],
            index=pd.DatetimeIndex([t for t, _ in self.equity_curve]),
        )
        daily = s.resample("1D").last().dropna()
        rets = daily.pct_change().dropna()
        if len(rets) < 2 or rets.std() == 0:
            return 0.0
        return round(float(rets.mean() / rets.std() * np.sqrt(252)), 2)

    def _period_returns(self, freq: str, fmt: str) -> dict[str, float]:
        if len(self.equity_curve) < 2:
            return {}
        idx = pd.DatetimeIndex([t for t, _ in self.equity_curve])
        if idx.tz is not None:  # to_period drops tz noisily; curve is already UTC
            idx = idx.tz_convert("UTC").tz_localize(None)
        s = pd.Series([e for _, e in self.equity_curve], index=idx)
        out: dict[str, float] = {}
        for period, chunk in s.groupby(s.index.to_period(freq)):
            ret = (chunk.iloc[-1] / chunk.iloc[0] - 1) * 100
            out[period.strftime(fmt)] = round(float(ret), 2)
        return out

    @property
    def monthly_returns(self) -> dict[str, float]:
        return self._period_returns("M", "%Y-%m")

    @property
    def yearly_returns(self) -> dict[str, float]:
        return self._period_returns("Y", "%Y")

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "initial_balance": self.initial_balance,
            "final_equity": round(self.final_equity, 2),
            "net_profit": self.net_profit,
            "net_profit_pct": self.net_profit_pct,
            "n_trades": self.n_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "expectancy": self.expectancy,
            "sharpe": self.sharpe,
            "monthly_returns": self.monthly_returns,
            "yearly_returns": self.yearly_returns,
            "notes": self.notes,
            "equity_curve": [(t.isoformat(), round(e, 2)) for t, e in self.equity_curve],
            "trades": [
                {**{k: v for k, v in asdict(t).items()
                    if k not in ("entry_ts", "exit_ts", "direction")},
                 "direction": t.direction.value,
                 "entry_ts": t.entry_ts.isoformat(),
                 "exit_ts": t.exit_ts.isoformat(),
                 "pnl": round(t.pnl, 2)}
                for t in self.trades
            ],
        }

    def save_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def save_html(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_html(), encoding="utf-8")
        return path

    def _render_html(self) -> str:
        pf = self.profit_factor
        metrics = [
            ("Net profit", f"${self.net_profit:,.2f} ({self.net_profit_pct:+.2f}%)"),
            ("Trades", str(self.n_trades)),
            ("Win rate", f"{self.win_rate:.1%}"),
            ("Profit factor", "∞" if pf == float("inf") else f"{pf:.2f}"),
            ("Max drawdown", f"{self.max_drawdown_pct:.2f}%"),
            ("Avg win / loss", f"${self.avg_win:,.2f} / ${self.avg_loss:,.2f}"),
            ("Expectancy / trade", f"${self.expectancy:,.2f}"),
            ("Sharpe (daily, ann.)", f"{self.sharpe:.2f}"),
        ]
        rows = "".join(
            f"<tr><td>{k}</td><td class='v'>{v}</td></tr>" for k, v in metrics
        )
        monthly = "".join(
            f"<tr><td>{m}</td><td class='v' style='color:{'#2e9e5b' if r >= 0 else '#d0454c'}'>"
            f"{r:+.2f}%</td></tr>"
            for m, r in self.monthly_returns.items()
        )
        trade_rows = "".join(
            f"<tr><td>{t.entry_ts:%Y-%m-%d %H:%M}</td><td>{t.direction.value}</td>"
            f"<td>{t.contract_symbol}</td><td>{t.quantity}</td>"
            f"<td>{t.entry_price:.2f}</td><td>{t.exit_price:.2f}</td>"
            f"<td class='v' style='color:{'#2e9e5b' if t.is_win else '#d0454c'}'>"
            f"{t.pnl:+.2f}</td><td>{t.confidence:.0f}%</td>"
            f"<td>{t.exit_reason}</td></tr>"
            for t in self.trades
        )
        notes = "".join(f"<li>{n}</li>" for n in self.notes)
        return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Backtest {self.symbol} — {self.strategy}</title>
<style>
 body {{ font-family: Segoe UI, system-ui, sans-serif; background:#14161a; color:#e6e8eb;
        margin:2rem auto; max-width:1000px; padding:0 1rem; }}
 h1 {{ font-size:1.4rem; }} h2 {{ font-size:1.05rem; margin-top:2rem; color:#9aa3ad; }}
 table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
 td, th {{ padding:.35rem .6rem; border-bottom:1px solid #2a2e35; text-align:left; }}
 td.v {{ text-align:right; font-variant-numeric:tabular-nums; }}
 svg {{ width:100%; height:220px; background:#1a1d22; border-radius:6px; }}
 .muted {{ color:#7d8590; font-size:.8rem; }}
</style></head><body>
<h1>Backtest — {self.symbol} <span class="muted">{self.strategy} ·
{self.start:%Y-%m-%d} → {self.end:%Y-%m-%d}</span></h1>
<h2>Equity curve</h2>{self._equity_svg()}
<h2>Performance</h2><table>{rows}</table>
<h2>Monthly returns</h2><table>{monthly or '<tr><td>n/a</td></tr>'}</table>
<h2>Trades</h2>
<table><tr><th>Entry</th><th>Dir</th><th>Contract</th><th>Qty</th><th>In</th>
<th>Out</th><th>P&amp;L</th><th>Conf</th><th>Exit reason</th></tr>{trade_rows}</table>
<h2>Notes</h2><ul class="muted">{notes}</ul>
</body></html>"""

    def _equity_svg(self, width: int = 960, height: int = 220) -> str:
        if len(self.equity_curve) < 2:
            return "<svg></svg>"
        eq = [e for _, e in self.equity_curve]
        lo, hi = min(eq), max(eq)
        span = (hi - lo) or 1.0
        pad = 10
        pts = " ".join(
            f"{pad + i * (width - 2 * pad) / (len(eq) - 1):.1f},"
            f"{height - pad - (e - lo) / span * (height - 2 * pad):.1f}"
            for i, e in enumerate(eq)
        )
        base_y = height - pad - (self.initial_balance - lo) / span * (height - 2 * pad)
        return (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
                f'<line x1="{pad}" y1="{base_y:.1f}" x2="{width - pad}" y2="{base_y:.1f}" '
                f'stroke="#3a3f47" stroke-dasharray="4 4"/>'
                f'<polyline points="{pts}" fill="none" stroke="#4f9cf7" stroke-width="1.6"/>'
                f"</svg>")
