"""Centralized AI decision-context snapshot (Phase 3).

`build_snapshot` is THE single place an AI decision context is turned into the
structured dict the Experience Engine records. Both the AI entry path and the
manual/coach context path funnel through it, so AI and manual trades populate
equivalent feature quality (feature symmetry) — and there is exactly one place
that knows how to read the engine's `EngineDecision` / `TimeframeView` /
`GateReport`.

Coupling is deliberately loose: the decision object is duck-typed (engine types
are imported only for annotations under TYPE_CHECKING), so `experience/` never
gains a hard runtime dependency on `engine/`.

Everything is best-effort and honest: a field the engine doesn't compute
(Bollinger bands, a full volume-profile histogram) is stored as None, never
invented. NaN indicator readings (a disabled indicator) also become None.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from optionspilot.core.models import utcnow

if TYPE_CHECKING:  # annotations only — no runtime import of engine/
    from optionspilot.core.models import OptionContract, TradePlan
    from optionspilot.engine.engine import EngineDecision

ET = ZoneInfo("America/New_York")


def _f(x: Any) -> float | None:
    """Finite float, or None (drops NaN/inf and non-numeric)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _i(x: Any) -> int | None:
    v = _f(x)
    return int(v) if v is not None else None


def build_snapshot(
    decision: "EngineDecision",
    *,
    spot: float | None = None,
    plan: "TradePlan | None" = None,
    contract: "OptionContract | None" = None,
    operating_mode: str | None = None,
    trading_mode: str | None = None,
    learning_mode: str = "normal",
    now: datetime | None = None,
) -> dict:
    """Capture the complete deterministic decision context as a dict that is
    also a valid `entry_context` for `features.build_experience`."""
    now = now or utcnow()
    et = now.astimezone(ET)
    signal = getattr(decision, "signal", None)
    gate = getattr(decision, "gate", None)
    view = getattr(decision, "entry_view", None)
    views = getattr(decision, "views", {}) or {}

    # Higher-timeframe trend: the trend of the highest timeframe above the entry
    # timeframe (falls back to the entry timeframe's own trend).
    htf_trend = None
    if view is not None:
        higher = [v for tf, v in views.items() if tf.minutes > view.timeframe.minutes]
        higher.sort(key=lambda v: v.timeframe.minutes, reverse=True)
        htf_trend = (higher[0].trend.value if higher else view.trend.value)

    entry_tf: dict = {}
    if view is not None:
        entry_tf = {
            "rsi": _f(view.rsi), "adx": _f(view.adx), "rvol": _f(view.rvol),
            "pressure": _f(view.pressure), "trend": view.trend.value,
            "consolidating": bool(view.consolidating),
            "atr": _f(view.atr), "ema_stack": _i(view.ema_stack),
            "macd_hist": _f(view.macd_hist), "above_vwap": view.above_vwap,
            "supertrend_dir": _i(view.supertrend_dir),
            "divergence": _i(view.divergence),
            "patterns": list(view.patterns),
        }

    con = plan.contract if plan is not None else contract
    contract_ctx: dict = {}
    if con is not None:
        contract_ctx = {
            "dte": con.dte(now.date()),
            "delta": _f(con.delta),
            "iv": _f(con.implied_volatility),
            "spread_pct": _f(con.spread_pct) if con.mid > 0 else None,
        }

    evidence = getattr(signal, "evidence", ()) if signal is not None else ()
    evidence_list = [
        {"name": e.name, "detail": e.detail,
         "score": round(e.score, 4), "weight": round(e.weight, 4)}
        for e in evidence
    ]

    return {
        "captured_ts": now.isoformat(),
        "symbol": getattr(signal, "symbol", None),
        "timeframe": str(signal.timeframe) if signal is not None else None,
        "direction": signal.direction.value if signal is not None else "unknown",
        "confidence": _f(getattr(signal, "confidence", None)) or 0.0,
        # There is one score in this system; the deterministic score IS the
        # confidence. Recorded under both names for explicitness.
        "deterministic_score": _f(getattr(signal, "confidence", None)) or 0.0,
        "reasoning": "\n".join(signal.reasons) if signal is not None else "",
        "htf_trend": htf_trend,
        "spot": _f(spot),
        "operating_mode": operating_mode,
        "trading_mode": trading_mode or (gate.mode if gate is not None else None),
        "learning_mode": learning_mode,
        "entry_tf": entry_tf,
        "contract": contract_ctx,
        "gate": gate.to_dict() if gate is not None else {},
        "evidence": evidence_list,
        "evidence_names": [e.name for e in evidence if e.score > 0],
        "setup_quality": gate.setup_quality if gate is not None else None,
        "stop": _f(plan.stop_underlying) if plan is not None else None,
        "target": _f(plan.target_underlying) if plan is not None else None,
        "entry": _f(plan.entry_price) if plan is not None else None,
        "risk_reward": _f(plan.risk_reward) if plan is not None else None,
        "hour_et": et.hour, "minute_et": et.minute,
        "bollinger": None,        # not computed by the engine — never invented
    }
