from __future__ import annotations

import pytest

from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import AnalyticConfig, CRRConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine
from options_engine.engines.crr import CRREngine, build_tree, minimum_valid_steps, up_probability
from tests.conftest import problem

CRR = CRREngine()


def test_invalid_probability_rejected_not_clipped():
    p = problem(volatility=0.01, rate=0.1, dividend_yield=0.0)
    assert up_probability(p, 10) >= 1.0
    with pytest.raises(DomainError) as e:
        CRR.price(p, CRRConfig(steps=10), ())
    assert e.value.code is ErrorCode.INVALID_TREE_PROBABILITY
    assert e.value.details["minimum_valid_steps"] == minimum_valid_steps(p)


def test_step_refinement_is_explicit_and_reported():
    p = problem(volatility=0.01, rate=0.1, dividend_yield=0.0)
    out = CRR.price(
        p, CRRConfig(steps=10, allow_step_refinement=True, odd_even_diagnostic=False), ()
    )
    n = out.diagnostics["steps_used"]
    assert out.diagnostics["step_refinement_applied"] is True
    assert n == minimum_valid_steps(p)
    assert 0 < out.diagnostics["up_probability"] < 1
    assert not 0 < up_probability(p, n - 1) < 1


def test_refinement_respects_work_limit():
    p = problem(volatility=1e-4, rate=0.1, dividend_yield=0.0)
    with pytest.raises(DomainError) as e:
        CRR.price(p, CRRConfig(steps=10, allow_step_refinement=True, max_steps=1000), ())
    assert e.value.code is ErrorCode.WORK_LIMIT_EXCEEDED


def test_zero_vol_and_expiry_not_supported():
    with pytest.raises(DomainError):
        CRR.price(problem(volatility=0.0), CRRConfig(), ())
    with pytest.raises(DomainError):
        CRR.price(problem(time_to_expiry=0.0), CRRConfig(), ())


def test_odd_even_diagnostic():
    out = CRR.price(problem(), CRRConfig(steps=200), ())
    assert out.diagnostics["price_steps_plus_one"] == build_tree(problem(), 201).price
    assert out.diagnostics["odd_even_spread"] > 0


def test_converges_to_bsm_with_odd_and_even_steps():
    p = problem(strike=105.0)
    ref = BlackScholesAnalyticEngine().price(p, AnalyticConfig(), ()).price
    errors = {n: abs(build_tree(p, n).price - ref) for n in (100, 101, 1000, 1001, 4000, 4001)}
    assert errors[4000] < errors[100] and errors[4001] < errors[101]
    assert max(errors[4000], errors[4001]) < 5e-3


def test_european_put_call_parity_holds_on_tree():
    call = build_tree(problem(), 500).price
    put = build_tree(problem(option_type=OptionType.PUT), 500).price
    import math

    assert call - put == pytest.approx(100 * math.exp(-0.01) - 100 * math.exp(-0.03), abs=1e-11)


def test_tree_greeks_close_to_analytic():
    p = problem()
    ref = BlackScholesAnalyticEngine().price(p, AnalyticConfig(), tuple(GreekName)).greeks
    out = CRR.price(p, CRRConfig(steps=4000, odd_even_diagnostic=False), tuple(GreekName)).greeks
    for g, rtol in [
        (GreekName.DELTA, 1e-3),
        (GreekName.GAMMA, 1e-2),
        (GreekName.THETA, 1e-2),
        (GreekName.RHO, 1e-2),
        (GreekName.VEGA, 2e-2),
        (GreekName.DIVIDEND_RHO, 1e-2),
    ]:
        assert out[g].status is GreekStatus.OK
        assert out[g].value == pytest.approx(ref[g].value, rel=rtol), g


def test_single_step_tree_reports_unavailable_node_greeks():
    out = CRR.price(problem(), CRRConfig(steps=1, odd_even_diagnostic=False), (GreekName.GAMMA,))
    assert out.greeks[GreekName.GAMMA].status is GreekStatus.NOT_SUPPORTED
    assert out.greeks[GreekName.GAMMA].value is None


class TestAmerican:
    def test_put_early_exercise_premium_positive(self):
        p = problem(
            option_type=OptionType.PUT,
            exercise_style=ExerciseStyle.AMERICAN,
            rate=0.08,
            dividend_yield=0.0,
        )
        out = CRR.price(p, CRRConfig(steps=1000), ())
        assert out.diagnostics["early_exercise_premium"] > 0.05

    def test_call_without_dividends_equals_european(self):
        p = problem(exercise_style=ExerciseStyle.AMERICAN, dividend_yield=0.0)
        eu = build_tree(p.with_changes(exercise_style=ExerciseStyle.EUROPEAN), 800).price
        assert build_tree(p, 800).price == pytest.approx(eu, abs=1e-12)

    def test_call_with_negative_rate_can_exceed_european(self):
        # Equality of American and European calls needs r >= 0; not assumed otherwise.
        p = problem(
            exercise_style=ExerciseStyle.AMERICAN,
            dividend_yield=0.0,
            rate=-0.05,
            strike=60.0,
            time_to_expiry=3.0,
        )
        eu = build_tree(p.with_changes(exercise_style=ExerciseStyle.EUROPEAN), 800).price
        assert build_tree(p, 800).price > eu + 1e-3

    def test_cash_dividend_call_early_exercise(self):
        p = problem(
            exercise_style=ExerciseStyle.AMERICAN,
            dividend_yield=0.0,
            strike=80.0,
            dividends=((0.5, 8.0),),
        )
        am = build_tree(p, 1000).price
        eu = build_tree(p.with_changes(exercise_style=ExerciseStyle.EUROPEAN), 1000).price
        assert am > eu + 0.1

    def test_european_escrowed_tree_matches_escrowed_bsm(self):
        p = problem(dividend_yield=0.0, dividends=((0.25, 1.0), (0.75, 1.0)))
        ref = BlackScholesAnalyticEngine().price(p, AnalyticConfig(), ()).price
        assert build_tree(p, 4000).price == pytest.approx(ref, abs=5e-3)
