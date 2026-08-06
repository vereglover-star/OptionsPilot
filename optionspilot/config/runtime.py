"""RuntimeSettings — the in-app-editable subset of configuration.

config.yaml stays the authority for everything structural (broker, data
provider, indicators, logging). This store owns what the user changes from
the dashboard, persisted to data/settings.json after every change:

  - watchlist (order preserved), pinned symbols, saved favorites
  - trading_mode (conservative | high_risk | custom)
  - custom-mode overrides (min_confidence, risk_per_trade_pct,
    daily_trade_limit, max_contracts, min_risk_reward, max_daily_loss_pct)
  - guided-onboarding state (tutorials finished/skipped, features used,
    motion and hint preferences) — see services/guide.py
  - workspace state (selected tab, symbol, timeframe, indicators, sidebar,
    recent symbols, saved layouts) — see services/workspace.py
  - surface_level (1–4) — how much of the interface is revealed

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

# Auto-updater preferences. Live-editable from Settings (hence here, not in the
# structural config.yaml), persisted under the "updates" key of settings.json.
# The UpdateService reads/writes these through update_prefs()/set_update_prefs().
DEFAULT_UPDATE_PREFS: dict = {
    "auto_check": True,          # check GitHub Releases in the background on launch
    "frequency": "daily",        # "launch" | "daily" | "weekly"
    "channel": "stable",         # "stable" | "beta" (beta accepts prereleases)
    "skip_version": None,        # a version the user chose to dismiss
    "last_checked": None,        # ISO-8601 timestamp of the last check
    "last_seen_version": None,   # the latest version observed at last check
}

# Desktop/runtime preferences are persisted here rather than in the desktop
# shell so a future host can read the same policy.  The shell is responsible
# for applying platform-specific effects (tray, Windows startup), while this
# store owns validation and persistence.
DEFAULT_RUNTIME_PREFS: dict = {
    "close_behavior": "tray",
    "close_prompt_dismissed": False,
    "hidden_profile": "essential_reduced",
    "start_with_windows": False,
    "start_minimized_to_tray": False,
    "restore_previous_workspace": False,
    "auto_resume_monitoring": True,
    "notification_mode": "normal",
}

_RUNTIME_BOOL_KEYS = {
    "close_prompt_dismissed", "start_with_windows",
    "start_minimized_to_tray", "restore_previous_workspace",
    "auto_resume_monitoring",
}

# Surface Level (UI V2). How much of the interface is revealed:
# 1 Guided, 2 Focused, 3 Full, 4 Pro. `docs/UI_V2_DESIGN.md` §8 states the
# four invariants that bind it, and the first two are the reason it lives
# here rather than anywhere near the engine: it NEVER changes behaviour and
# it NEVER hides money, risk or a warning. It is a third mode-like axis
# alongside operating_mode and trading_mode, and the same orthogonality rule
# applies — no code path may let one of the three change another.
SURFACE_LEVELS = (1, 2, 3, 4)

# 3 (Full) for an installation that has no stored value, which means every
# installation that predates this field. That is not a preference about what
# a new user should see — onboarding asks them (§7.2) and writes the answer.
# It is the only default that does not silently REMOVE something: an existing
# user is already looking at every column this app has, and §8.3 is explicit
# that "the system never lowers a user's level. Only the user does."
DEFAULT_SURFACE_LEVEL = 3

# The UI V2 shell (M2). Live-editable and device-local, exactly like
# surface_level above, and for the same two reasons: it decides what the client
# renders rather than what the app does, and a phone and a desktop may
# legitimately want different answers.
#
# It is the ROLLBACK PATH for the whole shell migration, which is why it lives
# here and not in `config/settings.py`: a rollback that needs a restart is not a
# rollback. `docs/ROADMAP-UI-V2.md` §2.1 gives its lifecycle — introduced off in
# M2-C1, defaulted on in M2-C11, and removed with its branches in M9-C7.
DEFAULT_SHELL_V2 = False

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
        # Runtime preference reads are also used while applying a validated
        # patch; reentrancy prevents a self-deadlock in that atomic path.
        self._lock = threading.RLock()
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
            operating = self._doc.get("operating_mode")
        if operating in ("ai", "human"):
            cfg.engine.operating_mode = operating
        if mode:
            self._apply_mode(cfg, mode, self._doc.get("custom") or {})
        if mode or operating:
            log.info("runtime settings applied: mode=%s operating=%s watchlist=%s",
                     cfg.engine.trading_mode, cfg.engine.operating_mode,
                     cfg.data.watchlist)

    def set_operating_mode(self, cfg: AppConfig, mode: str) -> None:
        if mode not in ("ai", "human"):
            raise ValueError(f"operating_mode must be 'ai' or 'human', got {mode!r}")
        cfg.engine.operating_mode = mode
        with self._lock:
            self._doc["operating_mode"] = mode
            self._save()
        log.info("operating mode switched to %s", mode)

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
        engine_updates = {**base_engine, "trading_mode": mode,
                          # operating_mode is orthogonal to the risk mode —
                          # switching risk modes must never flip AI <-> Human
                          "operating_mode": cfg.engine.operating_mode}
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

    # ── auto-updater preferences (PreferencesStore for UpdateService) ─────────

    def update_prefs(self) -> dict:
        """Current updater preferences, defaults filled in for any missing key."""
        with self._lock:
            stored = self._doc.get("updates") or {}
            return {**DEFAULT_UPDATE_PREFS, **stored}

    def set_update_prefs(self, **patch) -> dict:
        """Merge a partial preferences update and persist. Only known keys are
        stored; unknown keys are ignored so a future field can't be smuggled in.
        Returns the full effective preferences."""
        with self._lock:
            current = {**DEFAULT_UPDATE_PREFS, **(self._doc.get("updates") or {})}
            for key, value in patch.items():
                if key in DEFAULT_UPDATE_PREFS:
                    current[key] = value
            self._doc["updates"] = current
            self._save()
            return dict(current)

    # ── background/desktop runtime preferences ─────────────────────────────

    def runtime_prefs(self) -> dict:
        """Return validated runtime preferences with defaults filled in."""
        with self._lock:
            stored = self._doc.get("runtime") or {}
            return {**DEFAULT_RUNTIME_PREFS, **self._valid_runtime_patch(stored)}

    def set_runtime_prefs(self, **patch) -> dict:
        """Merge and validate runtime preferences atomically."""
        with self._lock:
            current = self.runtime_prefs()
            current.update(self._valid_runtime_patch(patch, reject=True))
            self._doc["runtime"] = current
            self._save()
            return dict(current)

    @staticmethod
    def _valid_runtime_patch(patch: dict, *, reject: bool = False) -> dict:
        if not isinstance(patch, dict):
            if reject:
                raise ValueError("runtime preferences must be an object")
            return {}
        allowed = set(DEFAULT_RUNTIME_PREFS)
        unknown = set(patch) - allowed
        if unknown and reject:
            raise ValueError(f"unknown runtime preferences: {sorted(unknown)}")
        out: dict = {}
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key in _RUNTIME_BOOL_KEYS:
                if not isinstance(value, bool):
                    if reject:
                        raise ValueError(f"{key} must be boolean")
                    continue
                out[key] = value
            elif key == "close_behavior":
                if value not in ("tray", "exit"):
                    if reject:
                        raise ValueError("close_behavior must be 'tray' or 'exit'")
                    continue
                out[key] = value
            elif key == "hidden_profile":
                if value not in ("essential_reduced", "normal", "monitoring_only"):
                    if reject:
                        raise ValueError("unknown hidden_profile")
                    continue
                out[key] = value
            elif key == "notification_mode":
                if value not in ("normal", "reduced", "critical"):
                    if reject:
                        raise ValueError("unknown notification_mode")
                    continue
                out[key] = value
        return out

    # ── Surface Level (UI V2 M1) ─────────────────────────────────────────────
    #
    # Deliberately NOT part of the workspace document, and the distinction is
    # the product decision recorded in `ROADMAP-UI-V2.md` §11-5: the workspace
    # is "where you were looking" and is meant to follow you to a second
    # client, whereas Surface Level is per-device — someone may reasonably want
    # Guided on a phone and Full on a desktop. Folding it into the workspace
    # would have made that impossible to express later without a migration.
    # `services/sync.py` carries the matching inventory row.
    #
    # The read/write asymmetry is the house pattern (see _valid_runtime_patch):
    # a READ of a hand-edited file must never fail — it falls back and the user
    # loses a preference — while a WRITE from a client is rejected loudly,
    # because a client sending 7 has a bug that silently defaulting would hide.

    def surface_level(self) -> int:
        """The stored Surface Level, or the default. Never raises."""
        with self._lock:
            return self._coerce_surface_level(self._doc.get("surface_level"))

    def set_surface_level(self, level) -> int:
        """Validate, persist and return the new Surface Level."""
        valid = self._coerce_surface_level(level, reject=True)
        with self._lock:
            self._doc["surface_level"] = valid
            self._save()
        log.info("surface level set to %d", valid)
        return valid

    @staticmethod
    def _coerce_surface_level(value, *, reject: bool = False) -> int:
        # bool BEFORE int: `isinstance(True, int)` is True in Python, so a
        # hand-edited `"surface_level": true` would otherwise be accepted and
        # silently become Guided — the most restrictive level, arrived at by
        # nobody's decision.
        if isinstance(value, bool):
            value = None
        elif isinstance(value, float) and value.is_integer():
            # JSON has one number type; a client that computed the level
            # arithmetically sends 3.0 and means 3.
            value = int(value)
        if value in SURFACE_LEVELS:
            return int(value)
        if reject:
            raise ValueError(
                f"surface_level must be one of {list(SURFACE_LEVELS)}, "
                f"got {value!r}")
        return DEFAULT_SURFACE_LEVEL

    def shell_v2(self) -> bool:
        """Whether this device renders the UI V2 shell. Never raises."""
        with self._lock:
            value = self._doc.get("shell_v2")
            return value if isinstance(value, bool) else DEFAULT_SHELL_V2

    def set_shell_v2(self, enabled) -> bool:
        """Validate, persist and return the new shell setting.

        Strict about the type for the same reason `set_surface_level` is: a
        client sending `"true"` has a bug, and coercing it would hide the bug
        while appearing to work. Note the asymmetry with the reader above,
        which is the house pattern — a hand-edited `settings.json` costs a
        preference, never a startup.
        """
        if not isinstance(enabled, bool):
            raise ValueError(f"shell_v2 must be true or false, got {enabled!r}")
        with self._lock:
            self._doc["shell_v2"] = enabled
            self._save()
        log.info("UI V2 shell %s", "enabled" if enabled else "disabled")
        return enabled

    # ── guided-onboarding state (V0.6.1) ─────────────────────────────────────
    #
    # Deliberately dumb storage: this store validates nothing about the shape of
    # the document, because the vocabulary (tutorial ids, feature keys, merge
    # semantics) belongs to `services/guide.py` and `config/` must not import upward
    # to reach it. The caller normalizes on read and on write — see
    # `UIServer.guide_payload` / `UIServer.update_guide`.
    #
    # It lives here rather than in the webview's localStorage because a user who
    # reinstalls, restores a backup, or clears the WebView2 profile should not
    # be shown the first-launch tour again as though they were new.

    def guide_state(self) -> dict:
        """The persisted guide document, exactly as stored (possibly empty)."""
        with self._lock:
            stored = self._doc.get("guide")
            return dict(stored) if isinstance(stored, dict) else {}

    def set_guide_state(self, state: dict) -> dict:
        """Replace the guide document wholesale and persist.

        Replacement, not merge: merging is `guide.merge_state`'s job and doing
        it in two places would give the two of them a chance to disagree.
        """
        with self._lock:
            self._doc["guide"] = dict(state)
            self._save()
            return dict(self._doc["guide"])

    # ── workspace state (V0.7.0) ─────────────────────────────────────────────
    #
    # Same deliberately-dumb storage as the guide document above, for the same
    # reason: the vocabulary (tab ids, indicator names, layout bodies) belongs
    # to `services/workspace.py`, and `config/` must not import upward to reach
    # it. The caller normalizes on read and on write.
    #
    # It lives here rather than in the WebView's localStorage because that is
    # not durable storage — a cleared profile, a restored backup or a reinstall
    # discards it silently — and because a second client cannot see localStorage
    # at all. Both were true of onboarding progress in V0.6.1 and the answer is
    # the same one.

    def workspace_state(self) -> dict:
        """The persisted workspace document, exactly as stored (possibly empty)."""
        with self._lock:
            stored = self._doc.get("workspace")
            return dict(stored) if isinstance(stored, dict) else {}

    def set_workspace_state(self, state: dict) -> dict:
        """Replace the workspace document wholesale and persist.

        Replacement, not merge — merging is `services/workspace.merge`'s job,
        and doing it in two places would give the two of them a chance to
        disagree about what a partial patch from a limited client means.
        """
        with self._lock:
            self._doc["workspace"] = dict(state)
            self._save()
            return dict(self._doc["workspace"])

    # ── persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._doc, indent=2), encoding="utf-8")
        tmp.replace(self._path)
