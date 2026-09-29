from __future__ import annotations

import math
from decimal import Decimal

import pytest

from options_engine.analytics.implied_vol import (
    IVStatus,
    implied_volatility,
    quote_resolution,
)
from options_engine.engines.bsm_analytic import black_scholes_price


def price(sign, S, K, T, r, q, v):
    return float(black_scholes_price(sign, S, K, T, r, q, v)[0])


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("K", [60.0, 95.0, 100.0, 105.0, 160.0])
def test_round_trip(sign, K):
    quote = price(sign, 100.0, K, 0.75, 0.03, 0.01, 0.27)
    r = implied_volatility(quote, sign, 100.0, K, 0.75, 0.03, 0.01)
    assert r.status is IVStatus.OK
    assert r.implied_vol == pytest.approx(0.27, abs=1e-9)
    assert abs(r.price_residual) <= 1e-10 + 1e-9 * quote
    assert r.iterations is not None and r.bracket is not None


def test_itm_inverts_time_value_via_parity():
    # Deep ITM call: time value is tiny relative to the price.
    quote = price(1, 100.0, 50.0, 0.5, 0.03, 0.0, 0.25)
    r = implied_volatility(quote, 1, 100.0, 50.0, 0.5, 0.03, 0.0)
    assert r.status in (IVStatus.OK, IVStatus.AT_LOWER_BOUND)


def test_failures_carry_no_implied_vol():
    r = implied_volatility(-1.0, 1, 100.0, 100.0, 1.0, 0.0, 0.0)
    assert r.implied_vol is None and r.explanation


def test_quote_resolution_from_decimal_text():
    assert quote_resolution(Decimal("1.25")) == pytest.approx(0.005)
    assert quote_resolution(Decimal("1.250")) == pytest.approx(0.0005)
    assert quote_resolution(Decimal("3")) == pytest.approx(0.5)


def test_iv_interval_brackets_solution():
    quote = price(1, 100.0, 100.0, 0.5, 0.03, 0.0, 0.2)
    r = implied_volatility(quote, 1, 100.0, 100.0, 0.5, 0.03, 0.0, price_resolution=0.005)
    lo, hi = r.iv_interval
    assert lo < r.implied_vol < hi
    assert r.vol_uncertainty == pytest.approx(0.005 / r.vega)


def test_low_vega_flagged_but_value_returned():
    r = implied_volatility(0.01, 1, 42.0, 60.0, 30 / 365, 0.1, 0.0, price_resolution=0.005)
    assert r.status is IVStatus.UNSTABLE_LOW_VEGA
    assert r.implied_vol is not None
    assert r.vol_uncertainty > 0.01


def test_lower_interval_end_reaches_zero_vol_bound():
    # Quote within resolution of the lower bound: IV interval extends to zero.
    D = math.exp(-0.03)
    lower = D * (100 * math.exp(0.03) - 90)
    r = implied_volatility(lower + 0.004, 1, 100.0, 90.0, 1.0, 0.03, 0.0, price_resolution=0.005)
    assert r.iv_interval[0] == 0.0
    assert r.notes
