"""Layered, validated application configuration.

Precedence (lowest to highest):
    1. Defaults defined on the pydantic models below.
    2. A user YAML file (default: <project>/config.yaml).
    3. Environment variables: OPTIONSPILOT__<SECTION>__<KEY>=value
       e.g. OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT=0.5

Invalid values (unknown keys, out-of-range numbers, malformed times) fail fast
at startup with a readable error — the system never runs on a half-understood
configuration.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ENV_PREFIX = "OPTIONSPILOT__"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typo'd keys are errors, not silence


class DataConfig(_Section):
    provider: str = "yfinance"
    cache_dir: str = "data/cache"
    watchlist: list[str] = ["SPY", "QQQ"]
    timeframes: list[str] = ["1d", "4h", "1h", "15m", "5m"]

    @field_validator("timeframes")
    @classmethod
    def _known_timeframes(cls, v: list[str]) -> list[str]:
        from optionspilot.core.models import Timeframe

        for tf in v:
            Timeframe.from_string(tf)  # raises with a helpful message
        return v

    @field_validator("watchlist")
    @classmethod
    def _non_empty_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("watchlist must contain at least one symbol")
        return [s.strip().upper() for s in v]


class IndicatorsConfig(_Section):
    """Per-indicator enable flags plus tunable parameters.

    The learning system may *recommend* disabling an indicator, but only config
    actually turns it off — the human stays in the loop.
    """

    ema: bool = True
    ema_periods: list[int] = [9, 21, 50, 200]
    sma: bool = True
    sma_periods: list[int] = [50, 200]
    vwap: bool = True
    macd: bool = True
    rsi: bool = True
    rsi_period: int = Field(14, ge=2, le=100)
    stoch_rsi: bool = True
    atr: bool = True
    atr_period: int = Field(14, ge=2, le=100)
    bollinger: bool = True
    supertrend: bool = True
    adx: bool = True
    obv: bool = True
    volume_profile: bool = False  # heavier computation; opt-in


class EngineConfig(_Section):
    min_confidence: float = Field(80.0, ge=0, le=100)
    scan_interval_seconds: int = Field(60, ge=5)
    htf_trend_timeframes: list[str] = ["1d", "4h"]
    entry_timeframes: list[str] = ["15m", "5m"]
    # Contract selection
    target_delta: float = Field(0.45, gt=0, lt=1)
    min_dte: int = Field(7, ge=0)
    max_dte: int = Field(45, ge=1)
    max_spread_pct: float = Field(0.10, gt=0, le=1)   # reject spreads wider than 10% of mid
    min_open_interest: int = Field(200, ge=0)
    min_option_volume: int = Field(50, ge=0)
    min_liquidity_score: float = Field(60.0, ge=0, le=100)
    # Trade planning
    stop_atr_buffer: float = Field(0.25, ge=0)        # extra ATRs beyond the swing for the stop
    fallback_target_rr: float = Field(2.0, gt=0)      # target R multiple when no structure target
    # Confluence weight overrides, e.g. {"htf_trend": 3.0}. Keys are validated
    # against the scorer's known evidence names at engine startup. The learning
    # system (Phase 5) proposes changes here; config remains the authority.
    evidence_weights: dict[str, float] = {}

    @field_validator("evidence_weights")
    @classmethod
    def _non_negative_weights(cls, v: dict[str, float]) -> dict[str, float]:
        for name, w in v.items():
            if w < 0:
                raise ValueError(f"evidence weight {name!r} must be >= 0, got {w}")
        return v

    @model_validator(mode="after")
    def _dte_window(self) -> "EngineConfig":
        if self.max_dte < self.min_dte:
            raise ValueError(f"max_dte ({self.max_dte}) must be >= min_dte ({self.min_dte})")
        return self


class RiskConfig(_Section):
    starting_balance: float = Field(25_000.0, gt=0)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    max_daily_loss_pct: float = Field(3.0, gt=0, le=50)
    max_weekly_loss_pct: float = Field(6.0, gt=0, le=50)
    max_drawdown_pct: float = Field(15.0, gt=0, le=90)
    max_consecutive_losses: int = Field(3, ge=1)
    max_contracts: int = Field(10, ge=1)
    max_open_positions: int = Field(3, ge=1)
    daily_trade_limit: int = Field(5, ge=1)
    min_risk_reward: float = Field(1.5, gt=0)
    cooldown_minutes_after_loss: int = Field(15, ge=0)
    trading_start: time = time(9, 45)   # ET; skip the open chop by default
    trading_end: time = time(15, 30)    # ET; stop entering before the close

    @field_validator("trading_start", "trading_end", mode="before")
    @classmethod
    def _parse_time(cls, v: Any) -> Any:
        if isinstance(v, str):
            return time.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _hours_ordered(self) -> "RiskConfig":
        if self.trading_end <= self.trading_start:
            raise ValueError("trading_end must be after trading_start")
        return self


class BrokerConfig(_Section):
    name: str = "paper"
    # --- The live-trading gate. v1 ships no live adapter; even when one exists,
    # both flags must be true or it refuses to initialize. ---
    live_trading_enabled: bool = False
    i_understand_the_risks: bool = False
    # Paper simulator realism
    commission_per_contract: float = Field(0.65, ge=0)
    slippage_pct: float = Field(0.01, ge=0, le=0.2)  # of premium, applied against you

    @model_validator(mode="after")
    def _paper_only_in_v1(self) -> "BrokerConfig":
        if self.name != "paper" and not (self.live_trading_enabled and self.i_understand_the_risks):
            raise ValueError(
                f"Broker {self.name!r} requires live_trading_enabled and "
                "i_understand_the_risks both set to true. v1 supports only 'paper'."
            )
        return self


class NotifyConfig(_Section):
    desktop: bool = True
    email: bool = False
    email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)
    daily_summary: bool = True
    weekly_summary: bool = True

    @model_validator(mode="after")
    def _email_needs_settings(self) -> "NotifyConfig":
        if self.email and not (self.email_to and self.smtp_host):
            raise ValueError("email notifications require email_to and smtp_host")
        return self


class LoggingConfig(_Section):
    dir: str = "logs"
    level: str = "INFO"
    max_bytes: int = Field(5_000_000, ge=100_000)
    backup_count: int = Field(10, ge=1)

    @field_validator("level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        import logging

        if v.upper() not in logging.getLevelNamesMapping():
            raise ValueError(f"Unknown log level {v!r}")
        return v.upper()


class AppConfig(_Section):
    data: DataConfig = DataConfig()
    indicators: IndicatorsConfig = IndicatorsConfig()
    engine: EngineConfig = EngineConfig()
    risk: RiskConfig = RiskConfig()
    broker: BrokerConfig = BrokerConfig()
    notify: NotifyConfig = NotifyConfig()
    logging: LoggingConfig = LoggingConfig()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides(environ: dict[str, str] | None = None) -> dict:
    """Collect OPTIONSPILOT__SECTION__KEY=value into a nested dict.

    Values are parsed as YAML scalars so numbers, booleans, and lists
    ("[a, b]") work naturally.
    """
    environ = environ if environ is not None else dict(os.environ)
    result: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        if len(path) != 2:
            raise ValueError(
                f"Malformed env override {key!r}: expected {ENV_PREFIX}SECTION__KEY"
            )
        section, field_name = path
        result.setdefault(section, {})[field_name] = yaml.safe_load(raw)
    return result


def load_config(
    yaml_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    """Build the effective configuration: defaults <- yaml <- env."""
    merged: dict = {}
    if yaml_path is not None:
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file {path} must contain a YAML mapping")
        merged = _deep_merge(merged, loaded)
    merged = _deep_merge(merged, _env_overrides(environ))
    return AppConfig.model_validate(merged)
