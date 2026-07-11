import pytest

from optionspilot.analysis import smart_money as smc
from optionspilot.analysis.structure import find_swings
from optionspilot.core.models import Direction
from tests.conftest import df_from_ohlc, zigzag


def with_warmup(rows):
    """Prepend 15 dull bars so ATR(14) is defined when the interesting part starts."""
    warmup = [(100.0, 100.3, 99.7, 100.0)] * 15
    return df_from_ohlc(warmup + rows)


class TestFVG:
    def test_bullish_fvg_zone(self):
        df = with_warmup([
            (100.0, 100.5, 99.8, 100.2),
            (100.2, 103.0, 100.1, 102.8),   # displacement bar
            (102.9, 104.0, 101.5, 103.5),   # low 101.5 > bar1 high 100.5 -> gap
        ])
        fvgs = [z for z in smc.find_fvgs(df) if z.kind == "fvg_bull"]
        assert len(fvgs) == 1
        z = fvgs[0]
        assert z.bottom == pytest.approx(100.5) and z.top == pytest.approx(101.5)

    def test_bearish_fvg_and_mitigation(self):
        df = with_warmup([
            (100.0, 100.5, 99.8, 100.0),
            (99.9, 100.0, 97.0, 97.2),      # displacement down
            (97.1, 98.5, 96.5, 97.0),       # high 98.5 < bar1 low 99.8 -> gap
            (97.0, 100.0, 96.9, 99.5),      # rallies back into the gap
        ])
        fvgs = [z for z in smc.find_fvgs(df) if z.kind == "fvg_bear"]
        assert len(fvgs) == 1
        assert fvgs[0].mitigated_ts == df.index[-1]

    def test_min_size_filter(self):
        df = with_warmup([
            (100.0, 100.5, 99.8, 100.2),
            (100.2, 103.0, 100.1, 102.8),
            (102.9, 104.0, 100.6, 103.5),   # tiny 0.1 gap
        ])
        assert smc.find_fvgs(df, min_size_atr=1.0) == []


class TestOrderBlocks:
    def test_bullish_ob(self):
        df = with_warmup([
            (100.0, 100.4, 99.6, 99.8),     # bearish candle = the OB
            (99.8, 101.5, 99.7, 101.4),     # displacement up through its high
            (101.4, 102.8, 101.3, 102.7),
            (102.7, 103.5, 102.6, 103.4),
        ])
        obs = [z for z in smc.find_order_blocks(df) if z.kind == "ob_bull"]
        assert obs, "expected a bullish order block"
        assert obs[0].top == pytest.approx(100.4)
        assert obs[0].bottom == pytest.approx(99.6)

    def test_no_ob_without_displacement(self):
        df = with_warmup([
            (100.0, 100.4, 99.6, 99.8),
            (99.8, 100.1, 99.7, 100.0),     # drifts, no displacement
            (100.0, 100.2, 99.8, 100.1),
            (100.1, 100.3, 99.9, 100.2),
        ])
        assert [z for z in smc.find_order_blocks(df) if z.kind == "ob_bull"] == []


class TestLiquidity:
    def test_equal_highs_pool(self):
        df = zigzag([100, 105.0, 102, 105.05, 101], bars_per_leg=5)
        swings = find_swings(df, 2)
        pools = smc.find_equal_levels(df, swings, tolerance_atr=0.5)
        assert any(z.kind == "eqh" for z in pools)

    def test_distant_highs_are_not_equal(self):
        df = zigzag([100, 105, 102, 109, 101], bars_per_leg=5)
        swings = find_swings(df, 2)
        assert not any(z.kind == "eqh"
                       for z in smc.find_equal_levels(df, swings, tolerance_atr=0.5))

    def test_liquidity_grab_above_high(self):
        # Swing high at ~105, later bar wicks to 105.8 but closes at 104.4
        df = zigzag([100, 105, 102, 104], bars_per_leg=5)
        sweep = df_from_ohlc(
            [(104.0, 105.8, 103.9, 104.4)],
            start=df.index[-1] + (df.index[1] - df.index[0]),
        )
        import pandas as pd
        df2 = pd.concat([df, sweep])
        grabs = smc.find_liquidity_grabs(df2, find_swings(df2, 2))
        assert grabs and grabs[-1].direction is Direction.SHORT
        assert grabs[-1].level == pytest.approx(df["high"].max())


class TestPremiumDiscount:
    def test_premium_zone(self):
        # Final leg back up so the swing low at 103 gets confirmed
        df = zigzag([100, 110, 103, 108], bars_per_leg=5)
        swings = find_swings(df, 2)
        ctx = smc.premium_discount(108.0, swings)
        assert ctx is not None and ctx.zone == "premium"
        assert ctx.position == pytest.approx((108 - ctx.low) / (ctx.high - ctx.low))

    def test_discount_and_equilibrium(self):
        df = zigzag([100, 110, 103, 108], bars_per_leg=5)
        swings = find_swings(df, 2)
        low, high = swings[-1].price, max(s.price for s in swings if s.is_high)
        mid = (low + high) / 2
        assert smc.premium_discount(low + 0.1 * (high - low), swings).zone == "discount"
        assert smc.premium_discount(mid, swings).zone == "equilibrium"

    def test_no_range_returns_none(self):
        assert smc.premium_discount(100.0, []) is None
