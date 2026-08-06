import json

import pytest

from optionspilot.config.runtime import (
    DEFAULT_SURFACE_LEVEL,
    MAX_WATCHLIST,
    SURFACE_LEVELS,
    RuntimeSettings,
)
from optionspilot.config.settings import AppConfig


def make(tmp_path, **cfg_overrides):
    cfg = AppConfig.model_validate(cfg_overrides)
    rt = RuntimeSettings(tmp_path / "settings.json", baseline=cfg)
    return cfg, rt


class TestUpdatePrefs:
    def test_defaults(self, tmp_path):
        _, rt = make(tmp_path)
        p = rt.update_prefs()
        assert p["auto_check"] is True
        assert p["frequency"] == "daily"
        assert p["channel"] == "stable"
        assert p["skip_version"] is None and p["last_checked"] is None

    def test_set_and_persist(self, tmp_path):
        _, rt = make(tmp_path)
        rt.set_update_prefs(frequency="weekly", channel="beta",
                            last_checked="2026-07-25T00:00:00+00:00")
        # a fresh instance reads the persisted values
        _, rt2 = make(tmp_path)
        p = rt2.update_prefs()
        assert p["frequency"] == "weekly" and p["channel"] == "beta"
        assert p["last_checked"] == "2026-07-25T00:00:00+00:00"

    def test_unknown_keys_ignored(self, tmp_path):
        _, rt = make(tmp_path)
        rt.set_update_prefs(frequency="launch", bogus="value")
        p = rt.update_prefs()
        assert p["frequency"] == "launch" and "bogus" not in p

    def test_partial_update_preserves_others(self, tmp_path):
        _, rt = make(tmp_path)
        rt.set_update_prefs(channel="beta")
        rt.set_update_prefs(auto_check=False)
        p = rt.update_prefs()
        assert p["channel"] == "beta" and p["auto_check"] is False


class TestSurfaceLevel:
    """UI V2 M1-C1. A presentation-only third axis; see UI_V2_DESIGN.md §8."""

    def test_default_is_full_when_nothing_is_stored(self, tmp_path):
        _, rt = make(tmp_path)
        assert rt.surface_level() == DEFAULT_SURFACE_LEVEL == 3

    @pytest.mark.parametrize("level", SURFACE_LEVELS)
    def test_every_level_round_trips_through_a_restart(self, tmp_path, level):
        _, rt = make(tmp_path)
        assert rt.set_surface_level(level) == level
        _, rt2 = make(tmp_path)
        assert rt2.surface_level() == level

    @pytest.mark.parametrize("bad", [0, 5, -1, "3", "guided", None, 3.5,
                                     [3], {"level": 3}])
    def test_a_client_sending_a_bad_level_is_rejected(self, tmp_path, bad):
        _, rt = make(tmp_path)
        with pytest.raises(ValueError, match="surface_level"):
            rt.set_surface_level(bad)
        assert rt.surface_level() == DEFAULT_SURFACE_LEVEL

    def test_an_integral_float_is_accepted(self, tmp_path):
        """JSON has one number type: a client that computed the level
        arithmetically sends 3.0 and means 3."""
        _, rt = make(tmp_path)
        assert rt.set_surface_level(4.0) == 4
        assert rt.surface_level() == 4

    @pytest.mark.parametrize("bad", [True, False, "1", None, 9, 0, [1]])
    def test_a_hand_edited_file_falls_back_and_never_raises(self, tmp_path, bad):
        """The failure mode is "you lose a preference", never "the app will
        not start" — `apply_control_state` learned this the expensive way.

        `True` is the one that matters: `isinstance(True, int)` is True in
        Python and `True == 1`, so a naive check would silently put a user
        into Guided — the most restrictive level, chosen by nobody.
        """
        (tmp_path / "settings.json").write_text(
            json.dumps({"surface_level": bad}), encoding="utf-8")
        cfg, rt = make(tmp_path)
        rt.apply(cfg)                                    # no crash
        assert rt.surface_level() == DEFAULT_SURFACE_LEVEL

    def test_it_does_not_touch_either_existing_mode_axis(self, tmp_path):
        """The third axis obeys the rule binding the other two: switching one
        never implicitly changes another (CLAUDE.md, UI_V2_DESIGN.md §8.1)."""
        cfg, rt = make(tmp_path)
        rt.set_mode(cfg, "high_risk")
        rt.set_operating_mode(cfg, "human")
        before = cfg.model_dump()

        rt.set_surface_level(1)

        assert cfg.model_dump() == before
        assert cfg.engine.trading_mode == "high_risk"
        assert cfg.engine.operating_mode == "human"
        # and the reverse direction: a mode switch leaves the level alone
        rt.set_mode(cfg, "conservative")
        rt.set_operating_mode(cfg, "ai")
        assert rt.surface_level() == 1

    def test_it_survives_alongside_the_other_persisted_keys(self, tmp_path):
        cfg, rt = make(tmp_path)
        rt.set_surface_level(2)
        rt.set_watchlist(cfg, ["SPY"])
        rt.set_update_prefs(channel="beta")
        doc = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert doc["surface_level"] == 2
        assert doc["watchlist"] == ["SPY"]


class TestShellV2:
    """UI V2 M2-C1. Live-editable because it is the migration's rollback path,
    and a rollback that needs a restart is not a rollback."""

    def test_the_shell_is_off_until_a_device_opts_in(self, tmp_path):
        _, rt = make(tmp_path)
        assert rt.shell_v2() is False

    def test_it_round_trips_through_a_restart(self, tmp_path):
        _, rt = make(tmp_path)
        assert rt.set_shell_v2(True) is True
        _, rt2 = make(tmp_path)
        assert rt2.shell_v2() is True
        rt2.set_shell_v2(False)
        _, rt3 = make(tmp_path)
        assert rt3.shell_v2() is False

    @pytest.mark.parametrize("bad", ["true", "1", 1, 0, None, [], {}])
    def test_a_client_sending_a_non_boolean_is_rejected(self, tmp_path, bad):
        _, rt = make(tmp_path)
        with pytest.raises(ValueError, match="shell_v2"):
            rt.set_shell_v2(bad)

    @pytest.mark.parametrize("bad", ["true", 1, None, [], "on"])
    def test_a_hand_edited_file_falls_back_and_never_raises(self, tmp_path, bad):
        (tmp_path / "settings.json").write_text(
            json.dumps({"shell_v2": bad}), encoding="utf-8")
        cfg, rt = make(tmp_path)
        rt.apply(cfg)
        assert rt.shell_v2() is False

    def test_it_leaves_every_other_axis_alone(self, tmp_path):
        """A fourth presentation flag must not disturb the three that exist."""
        cfg, rt = make(tmp_path)
        rt.set_mode(cfg, "high_risk")
        rt.set_operating_mode(cfg, "human")
        rt.set_surface_level(2)
        before = cfg.model_dump()
        rt.set_shell_v2(True)
        assert cfg.model_dump() == before
        assert rt.surface_level() == 2
        assert cfg.engine.trading_mode == "high_risk"
        assert cfg.engine.operating_mode == "human"


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
