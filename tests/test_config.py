from datetime import time

import pytest
from pydantic import ValidationError

from optionspilot.config import load_config


def test_defaults_are_safe():
    cfg = load_config()
    assert cfg.broker.name == "paper"
    assert cfg.broker.live_trading_enabled is False
    assert cfg.engine.min_confidence == 80.0
    assert cfg.risk.risk_per_trade_pct == 1.0


def test_yaml_overlay(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "risk:\n  risk_per_trade_pct: 0.5\n  trading_start: '10:00'\n"
        "engine:\n  min_confidence: 90\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.risk.risk_per_trade_pct == 0.5
    assert cfg.risk.trading_start == time(10, 0)
    assert cfg.engine.min_confidence == 90
    assert cfg.risk.max_daily_loss_pct == 3.0  # untouched default


def test_env_overrides_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("risk:\n  risk_per_trade_pct: 0.5\n", encoding="utf-8")
    cfg = load_config(p, environ={"OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT": "2.0"})
    assert cfg.risk.risk_per_trade_pct == 2.0


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("risk:\n  risk_per_trad_pct: 1.0\n", encoding="utf-8")  # typo
    with pytest.raises(ValidationError):
        load_config(p)


def test_out_of_range_rejected():
    with pytest.raises(ValidationError):
        load_config(environ={"OPTIONSPILOT__RISK__RISK_PER_TRADE_PCT": "50"})


def test_bad_timeframe_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("data:\n  timeframes: [7m]\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Unknown timeframe"):
        load_config(p)


def test_live_broker_requires_double_optin(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("broker:\n  name: alpaca\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="live_trading_enabled"):
        load_config(p)


def test_dte_window_ordering():
    with pytest.raises(ValidationError, match="max_dte"):
        load_config(environ={
            "OPTIONSPILOT__ENGINE__MIN_DTE": "30",
            "OPTIONSPILOT__ENGINE__MAX_DTE": "10",
        })


def test_repo_config_yaml_is_valid():
    """The config.yaml shipped in the repo must always load."""
    from pathlib import Path

    repo_cfg = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = load_config(repo_cfg)
    assert cfg.broker.name == "paper"
