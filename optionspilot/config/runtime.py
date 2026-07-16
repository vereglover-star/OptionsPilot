"""RuntimeSettings — the in-app-editable subset of configuration.

config.yaml stays the authority for everything structural (broker, data
provider, indicators, logging). This store owns what the user changes from
the dashboard, persisted to data/settings.json after every change:

  - watchlist (order preserved), pinned symbols, saved favorites
  - trading_mode (conservative | high_risk | custom)
  - custom-mode overrides (min_confidence, risk_per_trade_pct,
    daily_trade_limit, max_contracts, min_risk_reward, max_daily_loss_pct)

Changes mutate the *live* AppConfig objects that every component reads at
call time (the orchestrator re-reads the watchlist each cycle, the gate and
risk manager read their thresholds per decision), so they take effect on the
next cycle without a restart. Every mutation is validated through the same
pydantic models as config.yaml — bad values are rejected, never applied.

`baseline` is a deep copy of the config as loaded from yaml, taken BEFORE
this overlay: switching from custom back to conservative/high_risk restores
the yaml-configured values exactly.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from optionspilot.config.settings import AppConfig, EngineConfig, RiskConfig
from optionspilot.core.logging_setup import get_logger

log = get_logger("ui")

MAX_WATCHLIST = 30   # scan cycles are ~seconds per symbol on the free feed

# Custom-mode knobs: (section, field)
CUSTOM_FIELDS = {
    "min_confidence": ("engine", "min_confidence"),
    "risk_per_trade_pct": ("risk", "risk_per_trade_pct"),
    "daily_trade_limit": ("risk", "daily_trade_limit"),
    "max_contracts": ("risk", "max_contracts"),
    "min_risk_reward": ("risk", "min_risk_reward"),
    "max_daily_loss_pct": ("risk", "max_daily_loss_pct"),
}


class RuntimeSettings:
    def __init__(self, path: str | Path, baseline: AppConfig):
        self._path = Path(path)
        self._baseline = baseline.model_copy(deep=True)
        self._lock = threading.Lock()
        self._doc: dict = {"pinned": [], "favorites": [], "custom": {}}
        if self._path.exists():
            try:
                self._doc.update(json.loads(self._path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as exc:
                log.error("settings.json unreadable (%s) — starting fresh", exc)

    # ── startup overlay ──────────────────────────────────────────────────────

    def apply(self, cfg: AppConfig) -> None:
        """Overlay persisted choices onto a freshly loaded config."""
        with self._lock:
            if self._doc.get("watchlist"):
                cfg.data.watchlist = list(self._doc["watchlist"])
            mode = self._doc.get("trading_mode")
        if mode:
            self._apply_mode(cfg, mode, self._doc.get("custom") or {})
            log.info("runtime settings applied: mode=%s watchlist=%s",
                     cfg.engine.trading_mode, cfg.data.watchlist)

    # ── watchlist ────────────────────────────────────────────────────────────

    def set_watchlist(self, cfg: AppConfig, symbols: list[str],
                      pinned: list[str] | None = None) -> None:
        symbols = [s.upper() for s in symbols]
        if not symbols:
            raise ValueError("watchlist cannot be empty")
        if len(symbols) > MAX_WATCHLIST:
            raise ValueError(
                f"watchlist capped at {MAX_WATCHLIST} symbols (free data feed "
                f"scans take seconds per symbol) — got {len(symbols)}"
            )
        if len(set(symbols)) != len(symbols):
            raise ValueError("watchlist contains duplicates")
        with self._lock:
            keep_pinned = pinned if pinned is not None else self._doc["pinned"]
            self._doc["watchlist"] = symbols
            self._doc["pinned"] = [s for s in keep_pinned if s in symbols]
            cfg.data.watchlist = list(symbols)
            self._save()

    def pinned(self) -> list[str]:
        with self._lock:
            return list(self._doc["pinned"])

    def set_pinned(self, symbol: str, pinned: bool) -> list[str]:
        symbol = symbol.upper()
        with self._lock:
            current = [s for s in self._doc["pinned"] if s != symbol]
            if pinned:
                current.append(symbol)
            self._doc["pinned"] = current
            self._save()
            return list(current)

    def favorites(self) -> list[str]:
        with self._lock:
            return list(self._doc["favorites"])

    def save_favorites(self, symbols: list[str]) -> None:
        with self._lock:
            self._doc["favorites"] = [s.upper() for s in symbols]
            self._save()

    # ── trading mode ─────────────────────────────────────────────────────────

    def set_mode(self, cfg: AppConfig, mode: str,
                 custom: dict | None = None) -> None:
        """Validate then switch modes live. Raises ValueError on bad input;
        the live config is only touched after validation passes."""
        self._apply_mode(cfg, mode, custom if custom is not None
                         else self._doc.get("custom") or {})
        with self._lock:
            self._doc["trading_mode"] = mode
            if custom is not None:
                self._doc["custom"] = dict(custom)
            self._save()
        log.info("trading mode switched to %s%s", mode,
                 f" (custom: {custom})" if mode == "custom" and custom else "")

    def _apply_mode(self, cfg: AppConfig, mode: str, custom: dict) -> None:
        base_engine = self._baseline.engine.model_dump()
        base_risk = self._baseline.risk.model_dump()
        engine_updates = {**base_engine, "trading_mode": mode}
        risk_updates = dict(base_risk)

        if mode == "custom":
            unknown = set(custom) - set(CUSTOM_FIELDS)
            if unknown:
                raise ValueError(
                    f"unknown custom settings: {sorted(unknown)} "
                    f"(allowed: {sorted(CUSTOM_FIELDS)})"
                )
            for key, value in custom.items():
                section, field = CUSTOM_FIELDS[key]
                (engine_updates if section == "engine" else risk_updates)[field] = value

        # Validate through the same models as config.yaml — reject, don't apply.
        valid_engine = EngineConfig.model_validate(engine_updates)
        valid_risk = RiskConfig.model_validate(risk_updates)

        for field in EngineConfig.model_fields:
            setattr(cfg.engine, field, getattr(valid_engine, field))
        for field in RiskConfig.model_fields:
            setattr(cfg.risk, field, getattr(valid_risk, field))

    def custom_settings(self) -> dict:
        with self._lock:
            return dict(self._doc.get("custom") or {})

    # ── persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._doc, indent=2), encoding="utf-8")
        tmp.replace(self._path)
