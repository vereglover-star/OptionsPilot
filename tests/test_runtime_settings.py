import json

import pytest

from optionspilot.config.runtime import MAX_WATCHLIST, RuntimeSettings
from optionspilot.config.settings import AppConfig


def make(tmp_path, **cfg_overrides):
    cfg = AppConfig.model_validate(cfg_overrides)
    rt = RuntimeSettings(tmp_path / "settings.json", baseline=cfg)
    return cfg, rt


class TestWatchlist:
    def test_set_and_persist(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_watchlist(cfg, ["aapl", "TSLA"])
        assert cfg.data.watchlist == ["AAPL", "TSLA"]
        # a fresh process restores it
        cfg2, rt2 = make(tmp_path)
        rt2.apply(cfg2)
        assert cfg2.data.watchlist == ["AAPL", "TSLA"]

    def test_rejects_empty_dupes_and_cap(self, tmp_path):
        cfg, rt = make(tmp_path)
        with pytest.raises(ValueError, match="empty"):
            rt.set_watchlist(cfg, [])
        with pytest.raises(ValueError, match="duplicates"):
            rt.set_watchlist(cfg, ["AAPL", "AAPL"])
        with pytest.raises(ValueError, match="capped"):
            rt.set_watchlist(cfg, [f"S{i:03d}" for i in range(MAX_WATCHLIST + 1)])
        assert cfg.data.watchlist == AppConfig().data.watchlist  # untouched

    def test_pins_survive_reorder_and_prune_on_remove(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_watchlist(cfg, ["AAPL", "TSLA", "NVDA"])
        rt.set_pinned("TSLA", True)
        rt.set_watchlist(cfg, ["NVDA", "TSLA", "AAPL"])     # reorder keeps pin
        assert rt.pinned() == ["TSLA"]
        rt.set_watchlist(cfg, ["NVDA", "AAPL"])             # removal prunes pin
        assert rt.pinned() == []

    def test_favorites_roundtrip(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.save_favorites(["aapl", "MSFT"])
        _, rt2 = make(tmp_path)
        assert rt2.favorites() == ["AAPL", "MSFT"]


class TestMode:
    def test_switch_applies_live_and_persists(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_mode(cfg, "high_risk")
        assert cfg.engine.trading_mode == "high_risk"
        cfg2, rt2 = make(tmp_path)
        rt2.apply(cfg2)
        assert cfg2.engine.trading_mode == "high_risk"

    def test_custom_overrides_and_baseline_restore(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_mode(cfg, "custom", {"min_confidence": 65,
                                    "risk_per_trade_pct": 2.5,
                                    "daily_trade_limit": 8})
        assert cfg.engine.trading_mode == "custom"
        assert cfg.engine.min_confidence == 65
        assert cfg.risk.risk_per_trade_pct == 2.5
        assert cfg.risk.daily_trade_limit == 8
        # switching back restores the yaml-baseline values exactly
        rt.set_mode(cfg, "conservative")
        assert cfg.engine.min_confidence == 80.0
        assert cfg.risk.risk_per_trade_pct == 1.0
        assert cfg.risk.daily_trade_limit == 5

    def test_custom_values_are_validated_not_applied_on_error(self, tmp_path):
        cfg, rt = make(tmp_path)
        with pytest.raises(Exception, match="risk_per_trade_pct|less than"):
            rt.set_mode(cfg, "custom", {"risk_per_trade_pct": 50})
        assert cfg.risk.risk_per_trade_pct == 1.0       # untouched
        assert cfg.engine.trading_mode == "conservative"
        with pytest.raises(ValueError, match="unknown custom settings"):
            rt.set_mode(cfg, "custom", {"leverage": 100})

    def test_unknown_mode_rejected(self, tmp_path):
        cfg, rt = make(tmp_path)
        with pytest.raises(Exception, match="trading_mode"):
            rt.set_mode(cfg, "yolo")

    def test_corrupt_settings_file_starts_fresh(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
        cfg, rt = make(tmp_path)
        rt.apply(cfg)                                    # no crash
        assert cfg.engine.trading_mode == "conservative"

    def test_settings_file_is_valid_json(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_mode(cfg, "high_risk")
        rt.set_watchlist(cfg, ["SPY"])
        doc = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert doc["trading_mode"] == "high_risk"
        assert doc["watchlist"] == ["SPY"]
