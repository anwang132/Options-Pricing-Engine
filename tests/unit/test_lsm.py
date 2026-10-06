"""Longstaff-Schwartz engine: determinism, configuration limits, routing and cheap sanity."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from options_engine.application.pricing import PricingService
from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import LSMConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.crr import build_tree
from options_engine.engines.lsm_american import LongstaffSchwartzEngine, lsm_price
from tests.conftest import make_request, problem

SMALL = LSMConfig(paths=20_000, regression_paths=10_000, exercise_dates=20)


def american_put(**kw):
    return problem(
        option_type=OptionType.PUT, exercise_style=ExerciseStyle.AMERICAN, rate=0.05, **kw
    )


def test_same_seed_is_bit_identical_and_seed_matters():
    a = lsm_price(american_put(), SMALL)
    b = lsm_price(american_put(), SMALL)
    assert a[0] == b[0] and a[1] == b[1]
    c = lsm_price(american_put(), replace(SMALL, seed=7))
    assert c[0] != a[0]


def test_close_to_bermudan_tree_and_below_in_sample():
    p = american_put()
    price, unc, diag = lsm_price(p, SMALL)
    assert unc is not None
    bermudan = build_tree(p, 20 * 100, exercise_every=100).price
    assert abs(price - bermudan) < 5 * unc.standard_error + 0.02
    assert diag["exercise_now"] is False
    assert 0 < diag["fraction_exercised_before_expiry"] < 1
    assert unc.ci_low < price < unc.ci_high


def test_deep_itm_put_exercises_immediately():
    p = american_put(strike=200.0)
    price, unc, diag = lsm_price(p, SMALL)
    assert diag["exercise_now"] is True
    assert price == 100.0 and unc is None


def test_bermudan_tree_bounds():
    p = american_put()
    european = build_tree(p.with_changes(exercise_style=ExerciseStyle.EUROPEAN), 2000).price
    bermudan = build_tree(p, 2000, exercise_every=100).price
    american = build_tree(p, 2000).price
    assert european < bermudan < american


def test_control_variate_reduces_variance():
    p = american_put(strike=90.0)
    with_cv = lsm_price(p, SMALL)
    without = lsm_price(p, replace(SMALL, control_variate=False))
    assert with_cv[2]["variance_reduction_factor"] > 1.5
    assert with_cv[1].standard_error < without[1].standard_error


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"paths": 3}, ErrorCode.INVALID_NUMERICAL_CONFIG),
        ({"basis_degree": 9}, ErrorCode.INVALID_NUMERICAL_CONFIG),
        ({"exercise_dates": 0}, ErrorCode.INVALID_NUMERICAL_CONFIG),
        ({"paths": 1_000_000, "exercise_dates": 500}, ErrorCode.WORK_LIMIT_EXCEEDED),
    ],
)
def test_config_limits(kw, code):
    with pytest.raises(DomainError) as e:
        LSMConfig(**kw)
    assert e.value.code is code


def test_routing_and_greek_statuses():
    svc = PricingService()
    req = make_request(
        engine_id="lsm_american",
        config=SMALL,
        contract={"exercise_style": ExerciseStyle.AMERICAN, "option_type": OptionType.PUT},
        greeks=(GreekName.DELTA,),
    )
    result = svc.price(req)
    assert result.output.uncertainty is not None
    assert result.output.greeks[GreekName.DELTA].status is GreekStatus.NOT_SUPPORTED
    european = make_request(engine_id="lsm_american", config=SMALL)
    with pytest.raises(DomainError):
        svc.price(european)
    divs = make_request(
        engine_id="lsm_american",
        config=SMALL,
        contract={"exercise_style": ExerciseStyle.AMERICAN},
        market={"spot": Decimal("42")},
    )
    assert svc.price(divs).price > 0
    assert LongstaffSchwartzEngine.capabilities.stochastic


def test_dual_upper_bound_brackets_the_bermudan_price():
    p = american_put()
    cfg = LSMConfig(
        paths=20_000,
        regression_paths=20_000,
        exercise_dates=10,
        upper_bound=True,
        outer_paths=300,
        inner_paths=100,
    )
    price, unc, diag = lsm_price(p, cfg)
    assert unc is not None
    bermudan = build_tree(p, 10 * 200, exercise_every=200).price
    lo, hi = diag["bermudan_interval"]
    assert lo < bermudan < hi
    assert diag["upper_bound"] >= price
    assert diag["duality_gap"] == pytest.approx(diag["upper_bound"] - price)


def test_upper_bound_skipped_when_exercising_now():
    cfg = replace(SMALL, upper_bound=True, outer_paths=200, inner_paths=20)
    _, _, diag = lsm_price(american_put(strike=200.0), cfg)
    assert diag["exercise_now"] and "upper_bound" not in diag
    assert "exercises immediately" in diag["upper_bound_note"]


def test_upper_bound_work_limit():
    with pytest.raises(DomainError) as e:
        LSMConfig(exercise_dates=200, upper_bound=True, outer_paths=5000, inner_paths=5000)
    assert e.value.code is ErrorCode.WORK_LIMIT_EXCEEDED
