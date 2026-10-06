"""Fault injection: deliberately broken implementations must fail the validation checks.

Each mutant reproduces a consequential, realistic mistake. A validation suite
that passes these mutants would not be evidence of correctness.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from options_engine.domain.conventions import OptionType
from options_engine.domain.numerics import MonteCarloConfig
from options_engine.engines.base import PricingProblem
from options_engine.engines.mc_terminal import simulate
from options_engine.validation import checks
from options_engine.validation.statistical import run_suite

price = checks.analytic_price


def discount_sign_error(p: PricingProblem) -> float:
    return price(p.with_changes(rate=-p.rate))


def call_put_reversal(p: PricingProblem) -> float:
    flipped = OptionType.PUT if p.option_type is OptionType.CALL else OptionType.CALL
    return price(p.with_changes(option_type=flipped))


def ignores_dividend_yield(p: PricingProblem) -> float:
    return price(p.with_changes(dividend_yield=0.0))


def dividend_sign_error(p: PricingProblem) -> float:
    return price(p.with_changes(dividend_yield=-p.dividend_yield))


def vol_percentage_unit_error(p: PricingProblem) -> float:
    # Treats a decimal volatility as if it were quoted in percent (0.2 -> 0.002).
    return price(p.with_changes(volatility=p.volatility / 100))


def act360_instead_of_act365(p: PricingProblem) -> float:
    return price(p.with_changes(time_to_expiry=p.time_to_expiry * 365 / 360))


PRICE_MUTANTS = [
    discount_sign_error,
    call_put_reversal,
    ignores_dividend_yield,
    dividend_sign_error,
    vol_percentage_unit_error,
    act360_instead_of_act365,
]


@pytest.mark.parametrize("mutant", PRICE_MUTANTS, ids=lambda f: f.__name__)
def test_price_mutants_detected_by_mpmath_reference(mutant):
    assert not checks.check_mpmath_prices(mutant).passed


@pytest.mark.parametrize(
    "mutant",
    [call_put_reversal, discount_sign_error, dividend_sign_error],
    ids=lambda f: f.__name__,
)
def test_identity_checks_detect_mutants(mutant):
    assert not checks.check_identities(mutant).passed


def test_greek_unit_mistake_detected():
    def vega_per_vol_point(p: PricingProblem):
        g = checks.analytic_greeks(p)
        g[next(k for k in g if k.value == "vega")] = (
            g[next(k for k in g if k.value == "vega")] / 100
        )
        return g

    assert not checks.check_mpmath_greeks(vega_per_vol_point).passed


def test_theta_sign_convention_mistake_detected():
    def theta_as_dv_dT(p: PricingProblem):
        g = checks.analytic_greeks(p)
        key = next(k for k in g if k.value == "theta")
        g[key] = -g[key]
        return g

    assert not checks.check_mpmath_greeks(theta_as_dv_dT).passed


def test_tree_clipping_probability_detected():
    """A tree that silently clips p into [0, 1] instead of rejecting must fail."""
    from options_engine.domain.errors import DomainError
    from options_engine.engines.crr import build_tree

    def clipping_tree(p: PricingProblem, n: int) -> float:
        try:
            return build_tree(p, n).price
        except DomainError:
            return price(p.with_changes(volatility=max(p.volatility, 1e-3)))

    assert not checks.check_crr_vs_bsm(clipping_tree).passed


def test_naive_antithetic_standard_error_detected():
    """Treating the 2n antithetic payoffs as independent misstates the SE."""

    def naive_estimator(p: PricingProblem, cfg: MonteCarloConfig) -> tuple[float, float]:
        moments, _ = simulate(p, cfg)
        mean = float(moments.mean[0])
        # Mutant: variance of pair averages v_pair relates to per-payoff variance
        # v via v_pair = v (1 + rho) / 2. The naive SE uses per-payoff variance
        # over 2n draws; reconstruct it from the pair statistics.
        s = np.random.SeedSequence(cfg.seed).spawn(2)[0]
        z = np.random.Generator(np.random.PCG64(s)).standard_normal(cfg.paths // 2)
        T, r, q, v = p.time_to_expiry, p.rate, p.dividend_yield, p.volatility
        st = p.spot * np.exp((r - q - 0.5 * v * v) * T + v * math.sqrt(T) * np.concatenate([z, -z]))
        pay = math.exp(-r * T) * np.maximum(p.option_type.sign * (st - p.strike), 0.0)
        return mean, float(pay.std(ddof=1) / math.sqrt(cfg.paths))

    results = run_suite(
        replications=200,
        paths=4_000,
        methods=["antithetic"],
        case_names=["deep_itm_call"],
        estimator=naive_estimator,
    )
    assert not results[0].passed
    assert not results[0].passed_se_calibration


def test_correct_antithetic_estimator_passes_same_small_suite():
    results = run_suite(
        replications=200, paths=4_000, methods=["antithetic"], case_names=["deep_itm_call"]
    )
    assert results[0].passed


def test_heston_correlation_sign_mutant_detected():
    """Flipping the sign of rho (a common convention slip) must fail the QuantLib check."""
    from dataclasses import replace as dc_replace

    from options_engine.engines.heston_fourier import heston_price

    def flipped(p, cfg):
        assert p.heston is not None
        return heston_price(p.with_changes(heston=dc_replace(p.heston, rho=-p.heston.rho)), cfg)[0]

    assert not checks.check_heston_vs_quantlib(flipped).passed


def test_heston_limit_detects_missing_mean_reversion_term():
    """Using theta instead of the integrated variance breaks the sigma -> 0 limit check."""
    from options_engine.engines.heston_fourier import heston_price

    def wrong_variance(p, cfg):
        assert p.heston is not None
        h = p.heston
        return heston_price(
            p.with_changes(heston=type(h)(h.theta, h.kappa, h.theta, h.sigma, h.rho)), cfg
        )[0]

    assert not checks.check_heston_limit(wrong_variance).passed
