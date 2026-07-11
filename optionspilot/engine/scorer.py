"""ConfluenceScorer: weighted evidence -> direction + confidence + reasons.

Every evidence builder scores from the LONG perspective in [-1, +1]
(+1 = strongly bullish). The weighted mean of all emitted evidence gives a net
directional score m in [-1, +1]; the trade direction is the sign of m and the
confidence is |m| * 100, damped when the entry timeframe is consolidating.

Confidence is therefore *agreement*, not certainty: 90+ means nearly every
enabled lens points the same way. With the default threshold of 80, mixed
markets simply produce no trade — which is the intended behaviour.

Weights come from DEFAULT_WEIGHTS, overridden by config
(`engine.evidence_weights`) and later tuned by the learning system. Unknown
override keys fail fast at startup.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from optionspilot.config.settings import EngineConfig, IndicatorsConfig
from optionspilot.core.models import Direction, Evidence, Timeframe
from optionspilot.engine.views import TimeframeView

DEFAULT_WEIGHTS: dict[str, float] = {
    "htf_trend": 2.0,          # higher-timeframe structure trend
    "htf_supertrend": 1.0,
    "structure_break": 1.5,    # recent BOS / CHoCH on entry timeframe
    "ema_alignment": 1.0,
    "momentum_rsi": 1.0,
    "momentum_macd": 1.0,
    "trend_strength": 0.8,     # ADX + DI direction
    "vwap": 0.8,
    "volume_pressure": 1.0,
    "relative_volume": 0.6,
    "divergence": 1.0,
    "candlestick": 0.8,
    "range_position": 0.8,     # premium/discount location
    "liquidity_grab": 0.9,
    "zone_confluence": 0.7,    # price at unmitigated FVG / order block
}

CONSOLIDATION_DAMPING = 0.75
RECENT_EVENT_BARS = 12       # structure breaks older than this are stale

BULL_PATTERNS = {"hammer", "bullish_engulfing", "morning_star",
                 "three_white_soldiers", "bullish_marubozu"}
BEAR_PATTERNS = {"shooting_star", "bearish_engulfing", "evening_star",
                 "three_black_crows", "bearish_marubozu"}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    direction: Direction
    confidence: float               # 0..100 for `direction`
    net: float                      # signed LONG-perspective mean, [-1, +1]
    evidence: tuple[Evidence, ...]  # scores flipped so + always supports the trade

    @property
    def reasons(self) -> list[str]:
        ranked = sorted(self.evidence, key=lambda e: abs(e.score * e.weight), reverse=True)
        return [f"{'+' if e.score >= 0 else '-'} {e.detail}" for e in ranked]


class ConfluenceScorer:
    def __init__(self, engine_cfg: EngineConfig, indicators_cfg: IndicatorsConfig,
                 learned_weights: dict[str, float] | None = None):
        unknown = set(engine_cfg.evidence_weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError(
                f"Unknown evidence_weights keys: {sorted(unknown)}. "
                f"Known: {sorted(DEFAULT_WEIGHTS)}"
            )
        # Precedence: defaults < learned (bounded, audited) < explicit config.
        learned = {k: v for k, v in (learned_weights or {}).items()
                   if k in DEFAULT_WEIGHTS}
        self.weights = {**DEFAULT_WEIGHTS, **learned, **engine_cfg.evidence_weights}
        self._icfg = indicators_cfg
        self._htf = [Timeframe.from_string(s) for s in engine_cfg.htf_trend_timeframes]
        self._entry = [Timeframe.from_string(s) for s in engine_cfg.entry_timeframes]

    def score(self, views: dict[Timeframe, TimeframeView]) -> ScoreResult | None:
        htf_views = [views[tf] for tf in self._htf if tf in views]
        entry_views = [views[tf] for tf in self._entry if tf in views]
        if not entry_views:
            return None

        evidence: list[Evidence] = []
        for name, result in self._build_evidence(htf_views, entry_views):
            if result is None:
                continue
            score, detail = result
            evidence.append(Evidence(name=name, detail=detail,
                                     score=score, weight=self.weights[name]))
        if not evidence:
            return None

        total_weight = sum(e.weight for e in evidence)
        net = sum(e.score * e.weight for e in evidence) / total_weight
        direction = Direction.LONG if net >= 0 else Direction.SHORT
        confidence = min(abs(net), 1.0) * 100.0

        if any(v.consolidating for v in entry_views):
            confidence *= CONSOLIDATION_DAMPING
            evidence.append(Evidence(
                name="consolidation",
                detail="entry timeframe is consolidating — confidence damped 25%",
                score=-0.5 if net >= 0 else 0.5,  # opposes the trade in the reason list
                weight=0.0,
            ))

        # Present evidence relative to the chosen trade: + supports, - opposes.
        sign = 1.0 if direction is Direction.LONG else -1.0
        presented = tuple(
            dataclasses.replace(e, score=e.score * sign) for e in evidence
        )
        return ScoreResult(direction=direction, confidence=round(confidence, 1),
                           net=net, evidence=presented)

    # ── evidence builders (all LONG-perspective) ─────────────────────────────

    def _build_evidence(self, htf: list[TimeframeView], entry: list[TimeframeView]):
        e = entry[0]  # primary entry view
        yield "htf_trend", self._htf_trend(htf)
        yield "htf_supertrend", self._htf_supertrend(htf)
        yield "structure_break", self._structure_break(entry)
        yield "ema_alignment", self._ema_alignment(htf + entry)
        yield "momentum_rsi", self._rsi(e)
        yield "momentum_macd", self._macd(e)
        yield "trend_strength", self._adx(e)
        yield "vwap", self._vwap(e)
        yield "volume_pressure", self._pressure(e)
        yield "relative_volume", self._rvol(e)
        yield "divergence", self._divergence(e)
        yield "candlestick", self._patterns(entry)
        yield "range_position", self._range_position(htf + entry)
        yield "liquidity_grab", self._grabs(entry)
        yield "zone_confluence", self._zones(e)

    @staticmethod
    def _htf_trend(htf: list[TimeframeView]):
        if not htf:
            return None
        vals = {"up": 1.0, "down": -1.0, "range": 0.0}
        score = sum(vals[v.trend.value] for v in htf) / len(htf)
        states = ", ".join(f"{v.timeframe}={v.trend.value}" for v in htf)
        return score, f"higher-timeframe trend: {states}"

    def _htf_supertrend(self, htf: list[TimeframeView]):
        if not htf or not self._icfg.supertrend:
            return None
        score = sum(v.supertrend_dir for v in htf) / len(htf)
        states = ", ".join(
            f"{v.timeframe}={'bull' if v.supertrend_dir > 0 else 'bear' if v.supertrend_dir < 0 else 'n/a'}"
            for v in htf
        )
        return score, f"supertrend: {states}"

    @staticmethod
    def _structure_break(entry: list[TimeframeView]):
        best = None
        for v in entry:
            if v.last_event is None or v.bars_since_event is None:
                continue
            if v.bars_since_event > RECENT_EVENT_BARS:
                continue
            if best is None or v.bars_since_event < best[1]:
                best = (v, v.bars_since_event)
        if best is None:
            return None
        v = best[0]
        ev = v.last_event
        magnitude = 1.0 if ev.kind == "BOS" else 0.7
        score = magnitude if ev.direction is Direction.LONG else -magnitude
        return score, (f"{ev.kind} {'up' if ev.direction is Direction.LONG else 'down'} "
                       f"through {ev.level:.2f} on {v.timeframe} "
                       f"({v.bars_since_event} bars ago)")

    def _ema_alignment(self, views: list[TimeframeView]):
        if not self._icfg.ema or not views:
            return None
        score = sum(v.ema_stack for v in views) / len(views)
        aligned = sum(1 for v in views if v.ema_stack == 1)
        inverse = sum(1 for v in views if v.ema_stack == -1)
        return score, (f"EMA stack: {aligned}/{len(views)} timeframes bullish, "
                       f"{inverse} bearish")

    def _rsi(self, v: TimeframeView):
        if not self._icfg.rsi or math.isnan(v.rsi):
            return None
        r = v.rsi
        if r > 75:
            score = 0.3   # still bullish, but stretched
        elif r > 55:
            score = min((r - 55) / 15, 1.0)
        elif r < 25:
            score = -0.3
        elif r < 45:
            score = -min((45 - r) / 15, 1.0)
        else:
            score = 0.0
        return score, f"RSI({self._icfg.rsi_period}) = {r:.0f} on {v.timeframe}"

    def _macd(self, v: TimeframeView):
        if not self._icfg.macd or math.isnan(v.macd_hist):
            return None
        rising = v.macd_hist > v.macd_hist_prev
        if v.macd_hist > 0:
            score = 1.0 if rising else 0.4
        elif v.macd_hist < 0:
            score = -1.0 if not rising else -0.4
        else:
            score = 0.0
        state = "rising" if rising else "falling"
        return score, f"MACD histogram {v.macd_hist:+.3f} and {state} on {v.timeframe}"

    def _adx(self, v: TimeframeView):
        if not self._icfg.adx or math.isnan(v.adx):
            return None
        if v.adx < 20:
            return 0.0, f"ADX {v.adx:.0f}: no meaningful trend on {v.timeframe}"
        strength = min(v.adx / 50, 1.0)
        score = strength if v.plus_di >= v.minus_di else -strength
        side = "+DI" if v.plus_di >= v.minus_di else "-DI"
        return score, f"ADX {v.adx:.0f} with {side} leading on {v.timeframe}"

    def _vwap(self, v: TimeframeView):
        if not self._icfg.vwap or v.above_vwap is None:
            return None
        score = 0.6 if v.above_vwap else -0.6
        return score, f"price {'above' if v.above_vwap else 'below'} VWAP on {v.timeframe}"

    @staticmethod
    def _pressure(v: TimeframeView):
        if math.isnan(v.pressure):
            return None
        score = max(-1.0, min(1.0, v.pressure))
        side = "buying" if score > 0 else "selling" if score < 0 else "balanced"
        return score, f"net {side} pressure {v.pressure:+.2f} on {v.timeframe}"

    @staticmethod
    def _rvol(v: TimeframeView):
        if math.isnan(v.rvol) or math.isnan(v.pressure) or v.rvol < 1.2:
            return None
        direction = 1.0 if v.pressure > 0 else -1.0 if v.pressure < 0 else 0.0
        score = direction * min(v.rvol / 2.5, 1.0)
        return score, f"elevated volume ({v.rvol:.1f}x average) confirming flow on {v.timeframe}"

    def _divergence(self, v: TimeframeView):
        if not self._icfg.obv or v.divergence == 0:
            return None
        kind = "bullish" if v.divergence > 0 else "bearish"
        return float(v.divergence), f"{kind} price/OBV divergence on {v.timeframe}"

    @staticmethod
    def _patterns(entry: list[TimeframeView]):
        bulls, bears = [], []
        for v in entry:
            bulls += [f"{p} ({v.timeframe})" for p in v.patterns if p in BULL_PATTERNS]
            bears += [f"{p} ({v.timeframe})" for p in v.patterns if p in BEAR_PATTERNS]
        if not bulls and not bears:
            return None
        score = max(-1.0, min(1.0, 0.6 * (len(bulls) - len(bears))))
        return score, f"candlesticks: {', '.join(bulls + bears)}"

    @staticmethod
    def _range_position(views: list[TimeframeView]):
        ctx = next((v.range_ctx for v in views if v.range_ctx is not None), None)
        if ctx is None:
            return None
        score = max(-1.0, min(1.0, (0.5 - ctx.position) * 2))
        return score, (f"price in {ctx.zone} of swing range "
                       f"{ctx.low:.2f}–{ctx.high:.2f} (position {ctx.position:.2f})")

    @staticmethod
    def _grabs(entry: list[TimeframeView]):
        grab = None
        for v in entry:
            if v.recent_grabs:
                grab = (v.recent_grabs[-1], v.timeframe)
        if grab is None:
            return None
        g, tf = grab
        score = 1.0 if g.direction is Direction.LONG else -1.0
        side = "below lows" if g.direction is Direction.LONG else "above highs"
        return score, f"liquidity grab {side} at {g.level:.2f} on {tf}"

    @staticmethod
    def _zones(v: TimeframeView):
        if math.isnan(v.atr) or v.atr <= 0 or not v.open_zones:
            return None
        near_bull = near_bear = 0
        for z in v.open_zones:
            dist = 0.0 if z.contains(v.close) else min(
                abs(v.close - z.top), abs(v.close - z.bottom)
            )
            if dist <= 0.5 * v.atr:
                if z.kind in ("fvg_bull", "ob_bull"):
                    near_bull += 1
                elif z.kind in ("fvg_bear", "ob_bear"):
                    near_bear += 1
        if near_bull == near_bear:
            return None
        score = 0.8 if near_bull > near_bear else -0.8
        kind = "support (bullish FVG/OB)" if near_bull > near_bear else "resistance (bearish FVG/OB)"
        return score, f"price at unmitigated {kind} on {v.timeframe}"
