from __future__ import annotations

import math

import numpy as np
import pytest

from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.errors import DomainError
from options_engine.domain.numerics import AnalyticConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.bsm_analytic import (
    BlackScholesAnalyticEngine,
    black_scholes_price,
    normalized_otm_price,
)
from tests.conftest import problem

ENGINE = BlackScholesAnalyticEngine()
ALL = tuple(GreekName)


def run(**kw):
    return ENGINE.price(problem(**kw), AnalyticConfig(), ALL)


def test_hull_example_matches_printed_values():
    call = run(spot=42, strike=40, time_to_expiry=0.5, rate=0.1, dividend_yield=0.0)
    put = run(
        spot=42,
        strike=40,
        time_to_expiry=0.5,
        rate=0.1,
        dividend_yield=0.0,
        option_type=OptionType.PUT,
    )
    assert round(call.price, 2) == 4.76
    assert round(put.price, 2) == 0.81


def test_batch_matches_scalar():
    rng = np.random.default_rng(3)
    n = 200
    S = rng.uniform(50, 150, n)
    K = rng.uniform(50, 150, n)
    T = rng.uniform(0.01, 3, n)
    r = rng.uniform(-0.02, 0.08, n)
    q = rng.uniform(0, 0.05, n)
    v = rng.uniform(0.05, 0.8, n)
    w = rng.choice([-1.0, 1.0], n)
    batch, _ = black_scholes_price(w, S, K, T, r, q, v)
    for i in range(n):
        scalar = run(
            option_type=OptionType.CALL if w[i] > 0 else OptionType.PUT,
            spot=S[i],
            strike=K[i],
            time_to_expiry=T[i],
            rate=r[i],
            dividend_yield=q[i],
            volatility=v[i],
        ).price
        assert batch[i] == scalar


class TestExpiryAndZeroVol:
    def test_at_expiry_price_is_undiscounted_payoff(self):
        out = run(time_to_expiry=0.0, spot=110.0)
        assert out.price == 10.0
        assert out.greeks[GreekName.DELTA].value == 1.0
        assert out.greeks[GreekName.THETA].status is GreekStatus.NOT_APPLICABLE_AT_EXPIRY
        assert out.greeks[GreekName.THETA].value is None

    def test_at_expiry_at_strike_delta_undefined(self):
        out = run(time_to_expiry=0.0)
        assert out.price == 0.0
        assert out.greeks[GreekName.DELTA].status is GreekStatus.UNDEFINED_AT_KINK
        assert out.greeks[GreekName.GAMMA].status is GreekStatus.UNDEFINED_AT_KINK

    def test_zero_vol_is_discounted_forward_intrinsic(self):
        out = run(volatility=0.0, strike=90.0, dividend_yield=0.0)
        assert out.price == pytest.approx(100 - 90 * math.exp(-0.03), abs=1e-13)
        assert out.greeks[GreekName.VEGA].status is GreekStatus.ONE_SIDED
        assert out.greeks[GreekName.THETA].value == pytest.approx(-0.03 * 90 * math.exp(-0.03))

    def test_zero_vol_at_forward_kink(self):
        # K e^{-rT} == S e^{-qT} up to rounding: side of the kink is unknowable.
        out = run(volatility=0.0, dividend_yield=0.0, strike=100 * math.exp(0.03))
        assert out.greeks[GreekName.DELTA].status is GreekStatus.UNDEFINED_AT_KINK
        vega = out.greeks[GreekName.VEGA]
        assert vega.status is GreekStatus.ONE_SIDED
        assert vega.value == pytest.approx(100 / math.sqrt(2 * math.pi))

    def test_small_vol_approaches_zero_vol_limit(self):
        tiny = run(volatility=1e-9, strike=90.0).price
        zero = run(volatility=0.0, strike=90.0).price
        assert tiny == pytest.approx(zero, rel=1e-14)


class TestTailStability:
    def test_deep_otm_positive_and_small(self):
        p = run(strike=400.0, time_to_expiry=0.25, volatility=0.15).price
        assert 0.0 < p < 1e-30

    def test_quadrature_engaged_in_cancellation_regime(self):
        b, quad = normalized_otm_price(np.array([-0.1, -1e-8]), np.array([0.01, 1e-7]))
        assert quad.all()
        assert (b >= 0).all()

    def test_atm_forward_uses_erf_identity(self):
        b, quad = normalized_otm_price(0.0, 1e-9)
        assert not quad
        assert float(b) == pytest.approx(1e-9 / math.sqrt(2 * math.pi), rel=1e-12)

    def test_otm_price_never_negative(self):
        xs = -np.logspace(-8, 1, 60)
        for s in np.logspace(-8, 1, 30):
            b, _ = normalized_otm_price(xs, s)
            assert (b >= 0).all()

    def test_domain_guard(self):
        with pytest.raises(ValueError, match="x <= 0"):
            normalized_otm_price(0.1, 0.2)


class TestEscrowedDividends:
    def test_european_equals_bsm_on_escrowed_spot(self):
        divs = ((0.25, 1.0), (0.75, 1.0))
        with_divs = run(dividends=divs, dividend_yield=0.0)
        pv = sum(a * math.exp(-0.03 * t) for t, a in divs)
        plain = run(spot=100.0 - pv, dividend_yield=0.0)
        assert with_divs.price == pytest.approx(plain.price, rel=1e-15)
        assert with_divs.greeks[GreekName.DELTA].value == plain.greeks[GreekName.DELTA].value

    def test_greeks_with_dividends_match_finite_differences(self):
        divs = ((0.3, 2.0),)
        base = problem(dividends=divs, dividend_yield=0.0)
        out = ENGINE.price(base, AnalyticConfig(), ALL)

        def price(p):
            return ENGINE.price(p, AnalyticConfig(), ()).price

        h = 1e-5
        # theta: roll valuation time forward (maturity and dividend times shrink)
        fwd = base.with_changes(time_to_expiry=1.0 - h, dividends=((0.3 - h, 2.0),))
        bwd = base.with_changes(time_to_expiry=1.0 + h, dividends=((0.3 + h, 2.0),))
        theta_fd = (price(fwd) - price(bwd)) / (2 * h)
        assert out.greeks[GreekName.THETA].value == pytest.approx(theta_fd, rel=1e-6)
        rho_fd = (
            price(base.with_changes(rate=0.03 + h)) - price(base.with_changes(rate=0.03 - h))
        ) / (2 * h)
        assert out.greeks[GreekName.RHO].value == pytest.approx(rho_fd, rel=1e-6)

    def test_dividends_exceeding_spot_rejected(self):
        with pytest.raises(DomainError):
            run(dividends=((0.5, 150.0),))


def test_american_rejected_by_analytic_engine():
    with pytest.raises(DomainError):
        ENGINE.price(problem(exercise_style=ExerciseStyle.AMERICAN), AnalyticConfig(), ())
