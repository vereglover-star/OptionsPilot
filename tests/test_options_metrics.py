from datetime import date

import pytest

from optionspilot.analysis import options_metrics as om
from optionspilot.core.models import OptionContract, OptionRight


class TestBlackScholes:
    """Reference values: S=100, K=100, T=1y, r=5%, sigma=20% — the textbook case."""

    def test_call_reference(self):
        g = om.bs_greeks(100, 100, 1.0, 0.20, OptionRight.CALL, r=0.05)
        assert g.price == pytest.approx(10.4506, abs=1e-3)
        assert g.delta == pytest.approx(0.6368, abs=1e-3)
        assert g.gamma == pytest.approx(0.018762, abs=1e-5)
        assert g.vega == pytest.approx(0.37524, abs=1e-4)       # per vol point
        assert g.theta == pytest.approx(-6.414 / 365, abs=1e-4)  # per day

    def test_put_reference_and_parity(self):
        import math
        call = om.bs_greeks(100, 100, 1.0, 0.20, OptionRight.CALL, r=0.05)
        put = om.bs_greeks(100, 100, 1.0, 0.20, OptionRight.PUT, r=0.05)
        assert put.price == pytest.approx(5.5735, abs=1e-3)
        assert put.delta == pytest.approx(call.delta - 1.0, abs=1e-9)
        # put-call parity: C - P = S - K*exp(-rT)
        assert call.price - put.price == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-9)
        # gamma and vega are right-independent
        assert put.gamma == pytest.approx(call.gamma) and put.vega == pytest.approx(call.vega)

    def test_expired_option_is_intrinsic(self):
        g = om.bs_greeks(110, 100, 0.0, 0.20, OptionRight.CALL)
        assert g.price == 10.0 and g.delta == 1.0 and g.vega == 0.0

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            om.bs_greeks(-1, 100, 1.0, 0.2, OptionRight.CALL)


class TestImpliedVol:
    def test_roundtrip(self):
        price = om.bs_greeks(100, 105, 0.25, 0.35, OptionRight.CALL, r=0.05).price
        iv = om.implied_vol(price, 100, 105, 0.25, OptionRight.CALL, r=0.05)
        assert iv == pytest.approx(0.35, abs=1e-4)

    def test_price_below_intrinsic_returns_none(self):
        # Call worth at least ~10.5 intrinsic-ish; 5.0 is impossible
        assert om.implied_vol(5.0, 110, 100, 0.5, OptionRight.CALL) is None

    def test_expired_returns_none(self):
        assert om.implied_vol(1.0, 100, 100, 0.0, OptionRight.CALL) is None


class TestExpectedMove:
    def test_scaling(self):
        # 20% IV on $100 over a year is ~$20; over ~91 days about half that
        assert om.expected_move(100, 0.20, 365) == pytest.approx(20.0, abs=0.01)
        assert om.expected_move(100, 0.20, 91) == pytest.approx(10.0, abs=0.15)


def _contract(bid, ask, oi, volume) -> OptionContract:
    return OptionContract("SPY", date(2026, 9, 18), 450.0, OptionRight.CALL,
                          bid=bid, ask=ask, volume=volume, open_interest=oi)


class TestLiquidityScore:
    def test_liquid_beats_illiquid(self):
        liquid = om.liquidity_score(_contract(2.00, 2.02, 5000, 2000))
        thin = om.liquidity_score(_contract(2.00, 2.60, 15, 3))
        assert liquid > 90
        assert thin < 30
        assert 0 <= thin < liquid <= 100

    def test_zero_market_scores_zero(self):
        assert om.liquidity_score(_contract(0.0, 0.0, 0, 0)) == 0.0


class TestEnrichGreeks:
    def test_computes_greeks_from_feed_iv(self):
        c = OptionContract("SPY", date(2026, 8, 21), 100.0, OptionRight.CALL,
                           bid=2.0, ask=2.1, implied_volatility=0.20)
        out = om.enrich_greeks(c, spot=100.0, today=date(2026, 7, 10))
        assert 0.5 < out.delta < 0.65     # slightly ITM-ish ATM call
        assert out.gamma > 0 and out.vega > 0 and out.theta < 0

    def test_solves_iv_when_missing(self):
        t = 42 / 365
        fair = om.bs_greeks(100, 100, t, 0.30, OptionRight.PUT).price
        c = OptionContract("SPY", date(2026, 8, 21), 100.0, OptionRight.PUT,
                           bid=fair - 0.01, ask=fair + 0.01)
        out = om.enrich_greeks(c, spot=100.0, today=date(2026, 7, 10))
        assert out.implied_volatility == pytest.approx(0.30, abs=0.01)
        assert out.delta < 0
