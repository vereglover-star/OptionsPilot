"""Feature extraction: turn a completed trade (or a live setup) + its captured
context into an `ExperienceRecord` and a normalized similarity feature vector.

Pure functions, no I/O — the same discipline as `analysis/`. Everything is
best-effort: a field that neither the `TradeRecord.market_conditions` (always
present) nor the richer optional `entry_context`/snapshot supplies is left None
rather than guessed. The feature vector uses the fixed ranges in
`models.FEATURE_RANGES` so a record's vector is stable for all time.

`_entry_fields` is the single shared extractor for everything derived from the
entry-side context. `build_experience` (a closed trade) and `build_query_record`
(a live setup, for similarity lookup) both use it, which is what keeps AI and
manual experiences feature-symmetric.
"""

from __future__ import annotations

from typing import Any

from optionspilot.core.models import TradeRecord, utcnow
from optionspilot.experience.models import FEATURE_RANGES, ExperienceRecord


def _num(value: Any) -> float | None:
    """Coerce to float, tolerating None / '' / non-numeric strings."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _int(value: Any) -> int | None:
    f = _num(value)
    return int(f) if f is not None else None


def _market_session(hour: int | None, minute: int | None) -> str:
    if hour is None:
        return "regular"
    m = minute or 0
    if hour < 9 or (hour == 9 and m < 30):
        return "pre"
    if hour >= 16:
        return "post"
    return "regular"


def market_regime(htf_trend: str | None, iv: float | None) -> str:
    """Coarse regime label from higher-timeframe trend × option volatility.

    Derived only from stored fields (never invented). Used as an indexed column
    for 'market regime statistics'. Both components degrade to 'unknown' rather
    than guess when their source is missing.
    """
    trend = (htf_trend or "").lower()
    if "up" in trend:
        t = "trending-up"
    elif "down" in trend:
        t = "trending-down"
    elif "range" in trend or "neutral" in trend or "chop" in trend:
        t = "ranging"
    else:
        t = "unknown-trend"
    if iv is None:
        v = "unknown-vol"
    elif iv < 0.30:
        v = "low-vol"
    elif iv < 0.60:
        v = "medium-vol"
    else:
        v = "high-vol"
    return f"{t}/{v}"


def _entry_fields(entry_context: dict | None, mc: dict | None) -> dict:
    """Extract every ExperienceRecord field derived from entry-side context.

    Searches, in priority order: the entry-timeframe view, the contract block,
    the gate block, the top-level context, then the always-present
    `market_conditions` fallback. Presence is tested explicitly (not by
    truthiness) so a legitimate 0 is never skipped.
    """
    ec = entry_context or {}
    mc = mc or {}
    entry_tf = ec.get("entry_tf") or {}
    contract = ec.get("contract") or {}
    gate = ec.get("gate") or {}

    def ctx(*keys: str) -> Any:
        for src in (entry_tf, contract, gate, ec, mc):
            for k in keys:
                if k in src and src[k] not in (None, ""):
                    return src[k]
        return None

    hour_et = _int(ctx("hour_et"))
    minute_et = _int(ctx("minute_et"))
    iv = _num(ctx("iv", "implied_volatility"))
    htf = ec.get("htf_trend") or mc.get("htf_trend") or None
    return {
        "setup_quality": gate.get("setup_quality") or mc.get("setup_quality") or None,
        "gate_mode": gate.get("mode") or mc.get("mode") or None,
        "risk_reward": _num(ctx("risk_reward")),
        "hour_et": hour_et,
        "minute_et": minute_et,
        "market_session": _market_session(hour_et, minute_et),
        "htf_trend": htf,
        "entry_trend": entry_tf.get("trend"),
        "consolidating": entry_tf.get("consolidating"),
        "rsi": _num(entry_tf.get("rsi")),
        "adx": _num(entry_tf.get("adx")),
        "rvol": _num(entry_tf.get("rvol")),
        "pressure": _num(entry_tf.get("pressure")),
        "iv": iv,
        "delta": _num(contract.get("delta")),
        "dte": _int(ctx("dte")),
        "spread_pct": _num(contract.get("spread_pct")),
        "atr": _num(entry_tf.get("atr")),
        "ema_state": _int(entry_tf.get("ema_stack")),
        "macd_hist": _num(entry_tf.get("macd_hist")),
        "above_vwap": entry_tf.get("above_vwap"),
        "supertrend_dir": _int(entry_tf.get("supertrend_dir")),
        "divergence": _int(entry_tf.get("divergence")),
        "stop": _num(ec.get("stop")),
        "target": _num(ec.get("target")),
        "market_regime": market_regime(htf, iv),
        "operating_mode": ec.get("operating_mode") or mc.get("operating_mode") or None,
        "trading_mode": ec.get("trading_mode") or gate.get("mode") or mc.get("mode") or None,
        "learning_mode": ec.get("learning_mode") or "normal",
        "reasoning": ec.get("reasoning") or "",
    }


def build_feature_vector(rec: ExperienceRecord) -> dict[str, float]:
    """Normalize the numeric context fields to [0, 1] with fixed ranges.

    Only fields that are actually present are emitted — a missing feature is
    absent from the vector, and the similarity metric treats absence as
    'no information on this axis' rather than assuming a value.
    """
    raw: dict[str, float | None] = {
        "confidence": rec.confidence_entry,
        "rsi": rec.rsi,
        "adx": rec.adx,
        "rvol": rec.rvol,
        "pressure": rec.pressure,
        "iv": rec.iv,
        "delta": rec.delta,
        "dte": None if rec.dte is None else float(rec.dte),
        "risk_reward": rec.risk_reward,
        "hour_et": None if rec.hour_et is None else float(rec.hour_et),
    }
    out: dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            continue
        lo, hi = FEATURE_RANGES[name]
        if hi == lo:
            continue
        out[name] = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return out


def build_experience(
    trade: TradeRecord,
    entry_context: dict | None = None,
    exit_context: dict | None = None,
    *,
    timeframe: str | None = None,
    exploration: bool = False,
    extra: dict | None = None,
) -> ExperienceRecord:
    """Assemble an ExperienceRecord from a journaled trade and its context.

    `entry_context` is the orchestrator's centralized decision snapshot (see
    `experience/snapshot.py`) or the coach's best-effort analysis snapshot;
    either may be None.
    """
    ec = entry_context or {}
    mc = trade.market_conditions or {}
    fields = _entry_fields(entry_context, mc)
    entry_price = trade.entry_price
    return_pct = ((trade.exit_price - entry_price) / entry_price * 100.0
                  if entry_price else 0.0)
    exploration = exploration or (fields["learning_mode"] == "exploration")

    extra = dict(extra or {})
    if ec.get("evidence") or ec.get("gate"):
        # Preserve the verbose per-component breakdown without bloating the
        # first-class columns.
        extra.setdefault("snapshot", {
            "evidence": ec.get("evidence", []),
            "gate": ec.get("gate", {}),
            "reasoning": ec.get("reasoning", ""),
        })

    rec = ExperienceRecord(
        trade_id=trade.id,
        recorded_ts=utcnow(),
        symbol=trade.symbol,
        contract_symbol=trade.contract_symbol,
        direction=trade.direction.value,
        strategy=trade.strategy,
        managed_by="manual" if trade.strategy == "manual" else "ai",
        quantity=trade.quantity,
        entry_ts=trade.entry_ts,
        entry_price=entry_price,
        exit_ts=trade.exit_ts,
        exit_price=trade.exit_price,
        pnl=trade.pnl,
        return_pct=round(return_pct, 2),
        is_win=trade.is_win,
        hold_minutes=round(trade.hold_minutes, 2),
        exit_reason=trade.exit_reason,
        timeframe=timeframe or ec.get("timeframe") or mc.get("timeframe") or None,
        confidence_entry=trade.confidence,
        confidence_exit=_num((exit_context or {}).get("confidence")),
        entry_reasons=list(trade.entry_reasons),
        evidence_names=list(trade.indicators_used),
        mistakes=list(trade.mistakes),
        lessons=list(trade.lessons),
        exploration=exploration,
        extra=extra,
        **fields,
    )
    rec.features = build_feature_vector(rec)
    return rec


def build_query_record(snapshot: dict) -> ExperienceRecord:
    """Build a query ExperienceRecord from a live decision snapshot, for
    similarity lookup. Outcome fields are neutral placeholders — the similarity
    metric reads only entry-side fields + the feature vector."""
    sn = snapshot or {}
    fields = _entry_fields(sn, None)
    now = utcnow()
    rec = ExperienceRecord(
        trade_id=sn.get("query_id", "__query__"),
        recorded_ts=now,
        symbol=sn.get("symbol") or "?",
        contract_symbol="",
        direction=sn.get("direction") or "",
        strategy=sn.get("strategy") or "confluence_v1",
        managed_by="ai",
        quantity=0,
        entry_ts=now, entry_price=0.0, exit_ts=now, exit_price=0.0,
        pnl=0.0, return_pct=0.0, is_win=False, hold_minutes=0.0, exit_reason="",
        timeframe=sn.get("timeframe"),
        confidence_entry=_num(sn.get("confidence")) or 0.0,
        evidence_names=list(sn.get("evidence_names") or []),
        **fields,
    )
    rec.features = build_feature_vector(rec)
    return rec
