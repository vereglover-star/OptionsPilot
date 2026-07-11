"""Options math: Black-Scholes pricing/greeks, implied volatility, liquidity
scoring, expected move.

Conventions (chosen to match common broker/platform displays):
  - T is in years; theta is returned PER CALENDAR DAY; vega PER 1 VOL POINT
    (i.e. the premium change if IV moves 0.20 -> 0.21).
  - European Black-Scholes with no dividend yield — a documented approximation
    for the American equity options this system paper-trades. Good enough for
    delta targeting, liquidity filtering, and backtest reconstruction; not for
    deep-ITM early-exercise edge cases.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import date

from optionspilot.core.models import OptionContract, OptionRight

DEFAULT_RISK_FREE_RATE = 0.05
TRADING_YEAR_DAYS = 365.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float   # per calendar day
    vega: float    # per 1 vol point (0.01)


def bs_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    right: OptionRight,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> Greeks:
    """Black-Scholes price and greeks. Degenerates to intrinsic value when the
    option has expired or volatility is zero."""
    if spot <= 0 or strike <= 0:
        raise ValueError(f"spot and strike must be positive (got {spot}, {strike})")
    if t_years <= 0 or sigma <= 0:
        if right is OptionRight.CALL:
            intrinsic = max(spot - strike, 0.0)
            delta = 1.0 if spot > strike else 0.0
        else:
            intrinsic = max(strike - spot, 0.0)
            delta = -1.0 if spot < strike else 0.0
        return Greeks(price=intrinsic, delta=delta, gamma=0.0, theta=0.0, vega=0.0)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc = math.exp(-r * t_years)
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t / 100.0  # per vol point

    if right is OptionRight.CALL:
        price = spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = (-spot * pdf_d1 * sigma / (2 * sqrt_t)
                        - r * strike * disc * _norm_cdf(d2))
    else:
        price = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_annual = (-spot * pdf_d1 * sigma / (2 * sqrt_t)
                        + r * strike * disc * _norm_cdf(-d2))

    return Greeks(price=price, delta=delta, gamma=gamma,
                  theta=theta_annual / TRADING_YEAR_DAYS, vega=vega)


def implied_vol(
    option_price: float,
    spot: float,
    strike: float,
    t_years: float,
    right: OptionRight,
    r: float = DEFAULT_RISK_FREE_RATE,
    tol: float = 1e-6,
) -> float | None:
    """Solve Black-Scholes for sigma by bisection. Returns None when the quoted
    price is inconsistent with any volatility (below intrinsic / stale quote) —
    callers must treat that as 'do not trust this contract'."""
    if t_years <= 0 or option_price <= 0:
        return None
    lo, hi = 1e-4, 5.0
    price_lo = bs_greeks(spot, strike, t_years, lo, right, r).price
    price_hi = bs_greeks(spot, strike, t_years, hi, right, r).price
    if not (price_lo <= option_price <= price_hi):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        p = bs_greeks(spot, strike, t_years, mid, right, r).price
        if abs(p - option_price) < tol:
            return mid
        if p < option_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def expected_move(spot: float, iv: float, days: int) -> float:
    """One-standard-deviation expected move over `days` calendar days."""
    return spot * iv * math.sqrt(max(days, 0) / TRADING_YEAR_DAYS)


def liquidity_score(contract: OptionContract) -> float:
    """0–100 tradability score. Weighting: spread tightness 40 (the direct cost
    of being wrong), open interest 30, volume 30. The engine's
    `min_liquidity_score` gate consumes this."""
    if contract.mid <= 0:
        return 0.0
    spread = contract.spread_pct
    spread_score = 40.0 * _clamp(1.0 - (spread - 0.01) / 0.09)   # full <=1%, zero >=10%
    oi_score = 30.0 * _clamp(math.log10(max(contract.open_interest, 1)) / math.log10(5000))
    vol_score = 30.0 * _clamp(math.log10(max(contract.volume, 1)) / 3.0)  # full at 1000+
    return round(spread_score + oi_score + vol_score, 1)


def enrich_greeks(
    contract: OptionContract,
    spot: float,
    today: date,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> OptionContract:
    """Return a copy of the contract with greeks computed from its IV (solving
    IV from the mid price when the feed didn't supply one)."""
    t_years = max(contract.dte(today), 0) / TRADING_YEAR_DAYS
    sigma = contract.implied_volatility
    if sigma <= 0 and contract.mid > 0:
        sigma = implied_vol(contract.mid, spot, contract.strike, t_years, contract.right, r) or 0.0
    if sigma <= 0:
        return contract
    g = bs_greeks(spot, contract.strike, t_years, sigma, contract.right, r)
    return dataclasses.replace(
        contract, implied_volatility=sigma,
        delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega,
    )


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
