"""Property-based tests of model-free and model-specific relations."""

from __future__ import annotations

import math

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from options_engine.analytics.implied_vol import SOLVED, implied_volatility
from options_engine.engines.bsm_analytic import black_scholes_price
from options_engine.engines.crr import build_tree
from tests.conftest import problem

spot = st.floats(1.0, 1000.0)
moneyness = st.floats(0.3, 3.0)
time = st.floats(1e-3, 10.0)
rate = st.floats(-0.03, 0.15)
yld = st.floats(-0.02, 0.1)
vol = st.floats(0.01, 2.0)


def price(sign, S, K, T, r, q, v):
    return float(black_scholes_price(sign, S, K, T, r, q, v)[0])


@settings(max_examples=300, deadline=None)
@given(spot, moneyness, time, rate, yld, vol)
def test_put_call_parity_and_bounds(S, m, T, r, q, v):
    K = S * m
    c, p = price(1, S, K, T, r, q, v), price(-1, S, K, T, r, q, v)
    D, F = math.exp(-r * T), S * math.exp((r - q) * T)
    tol = 1e-12 * max(S, K) * 10
    assert abs((c - p) - D * (F - K)) <= tol
    assert D * max(F - K, 0) - tol <= c <= D * F + tol
    assert D * max(K - F, 0) - tol <= p <= D * K + tol


@settings(max_examples=200, deadline=None)
@given(spot, time, rate, yld, vol, st.floats(0.5, 0.99), st.floats(1.01, 2.0))
def test_call_decreasing_convex_in_strike(S, T, r, q, v, a, b):
    k1, k2, k3 = S * a, S, S * b
    c1, c2, c3 = (price(1, S, k, T, r, q, v) for k in (k1, k2, k3))
    tol = 1e-10 * S
    assert c1 >= c2 - tol >= c3 - 2 * tol
    lam = (k3 - k2) / (k3 - k1)
    assert c2 <= lam * c1 + (1 - lam) * c3 + tol


@settings(max_examples=200, deadline=None)
@given(spot, moneyness, time, rate, yld, vol, st.floats(1.01, 1.5), st.sampled_from([1, -1]))
def test_increasing_in_volatility(S, m, T, r, q, v, bump, sign):
    assert price(sign, S, S * m, T, r, q, v * bump) >= price(sign, S, S * m, T, r, q, v) - 1e-12 * S


@settings(max_examples=200, deadline=None)
@given(spot, moneyness, time, rate, yld, vol, st.floats(0.1, 100.0), st.sampled_from([1, -1]))
def test_homogeneous_degree_one(S, m, T, r, q, v, lam, sign):
    base = price(sign, S, S * m, T, r, q, v)
    scaled = price(sign, lam * S, lam * S * m, T, r, q, v)
    assert math.isclose(scaled, lam * base, rel_tol=1e-11, abs_tol=1e-12 * lam * S)


@settings(max_examples=300, deadline=None)
@given(
    spot,
    st.floats(0.7, 1.4),
    st.floats(0.05, 3.0),
    rate,
    yld,
    st.floats(0.05, 1.0),
    st.sampled_from([1, -1]),
)
def test_iv_round_trip_when_well_conditioned(S, m, T, r, q, v, sign):
    K = S * m
    quote = price(sign, S, K, T, r, q, v)
    res = implied_volatility(quote, sign, S, K, T, r, q)
    lower = math.exp(-r * T) * max(sign * (S * math.exp((r - q) * T) - K), 0.0)
    assume(quote - lower > 1e-6 * S)  # time value not negligible
    assert res.status in SOLVED
    assert res.vega is not None and res.implied_vol is not None
    assert abs(res.implied_vol - v) <= 1e-9 + 10 * (1e-10 + 1e-9 * quote) / res.vega


@settings(max_examples=40, deadline=None)
@given(
    st.floats(70, 130),
    st.floats(0.1, 2.0),
    st.floats(0.0, 0.1),
    st.floats(0.15, 0.5),
    st.sampled_from([1, -1]),
)
def test_american_dominates_european_on_same_tree(K, T, r, v, sign):
    from options_engine.domain.conventions import ExerciseStyle, OptionType

    opt = OptionType.CALL if sign > 0 else OptionType.PUT
    eu = problem(option_type=opt, strike=K, time_to_expiry=T, rate=r, volatility=v)
    am = eu.with_changes(exercise_style=ExerciseStyle.AMERICAN)
    v_eu, v_am = build_tree(eu, 300).price, build_tree(am, 300).price
    assert v_am >= v_eu - 1e-12
    assert v_am >= max(sign * (100.0 - K), 0.0) - 1e-12
