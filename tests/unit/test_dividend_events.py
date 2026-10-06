"""Behaviour immediately around ex-dividend dates (escrowed-dividend model, ADR 0005)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from options_engine.application.analysis import invert_quotes
from options_engine.application.pricing import PricingService
from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import CashDividend, MarketSnapshot, ValuationContext
from options_engine.domain.numerics import AnalyticConfig, CRRConfig
from options_engine.engines.crr import build_tree
from tests.conftest import AS_OF, make_contract, make_request, problem

SVC = PricingService()
EPS = timedelta(seconds=1)
DIV = Decimal("2.00")


def around_ex_date(option_type: OptionType, exercise: ExerciseStyle, engine: str, config):
    """Price with the ex-date 1 s after (cum) and 1 s before (ex, spot dropped) the valuation."""
    contract = make_contract(
        strike=Decimal("100"),
        option_type=option_type,
        exercise_style=exercise,
        expiry=AS_OF + timedelta(days=180),
    )
    cum = make_request(
        contract={
            "strike": Decimal("100"),
            "option_type": option_type,
            "exercise_style": exercise,
            "expiry": AS_OF + timedelta(days=180),
        },
        market={
            "spot": Decimal("100"),
            "rate": Decimal("0.05"),
            "cash_dividends": (CashDividend(AS_OF + EPS, DIV),),
        },
        engine_id=engine,
        config=config,
    )
    ex = replace(
        cum,
        contract=contract,
        market=MarketSnapshot(
            spot=Decimal("98.00"),
            rate=Decimal("0.05"),
            cash_dividends=(CashDividend(AS_OF - EPS, DIV),),
        ),
    )
    return SVC.price(cum).price, SVC.price(ex).price


@pytest.mark.parametrize("option_type", list(OptionType))
def test_european_price_continuous_across_ex_date(option_type):
    cum, ex = around_ex_date(option_type, ExerciseStyle.EUROPEAN, "bsm_analytic", AnalyticConfig())
    # Difference is only the one-second discounting of the dividend: D * r * dt.
    assert cum == pytest.approx(ex, abs=2.0 * 0.05 * 2 / 31_536_000 * 10)


def test_european_tree_continuous_across_ex_date():
    cum, ex = around_ex_date(
        OptionType.PUT, ExerciseStyle.EUROPEAN, "crr_tree", CRRConfig(steps=2000)
    )
    assert cum == pytest.approx(ex, abs=1e-6)


def test_american_put_continuous_across_ex_date():
    cum, ex = around_ex_date(
        OptionType.PUT, ExerciseStyle.AMERICAN, "crr_tree", CRRConfig(steps=2000)
    )
    assert cum == pytest.approx(ex, abs=1e-6)


def test_american_call_can_exercise_just_before_ex_date():
    # Deep ITM call, large dividend: exercising cum-dividend beats holding through the drop.
    p = problem(
        exercise_style=ExerciseStyle.AMERICAN,
        strike=60.0,
        dividend_yield=0.0,
        rate=0.01,
        dividends=((1e-6, 10.0),),
        time_to_expiry=0.5,
    )
    value = build_tree(p, 2000).price
    assert value == pytest.approx(100.0 - 60.0, abs=1e-6)  # immediate exercise at S (cum)
    ex_value = build_tree(p.with_changes(spot=90.0, dividends=()), 2000).price
    assert value > ex_value


def test_theta_holding_spot_fixed_is_discontinuous_across_ex_date():
    """Documented consequence: rolling time past an ex-date at fixed spot removes the dividend."""
    base = problem(dividend_yield=0.0, dividends=((0.1, 2.0),))
    before = build_tree(
        base.with_changes(time_to_expiry=1.0 - 0.0999, dividends=((0.0001, 2.0),)), 1000
    ).price
    after = build_tree(base.with_changes(time_to_expiry=1.0 - 0.1001, dividends=()), 1000).price
    assert after - before > 1.0  # call jumps up by roughly delta * dividend


def test_unsupported_american_cases_rejected():
    american = make_request(contract={"exercise_style": ExerciseStyle.AMERICAN})
    for engine, cfg in (("bsm_analytic", AnalyticConfig()),):
        with pytest.raises(DomainError) as e:
            SVC.price(replace(american, engine_id=engine, config=cfg))
        assert e.value.code is ErrorCode.UNSUPPORTED_COMBINATION
    with pytest.raises(DomainError) as e:
        invert_quotes(
            american.contract, ValuationContext(AS_OF), american.market, [("q", Decimal("3"))], None
        )
    assert "American quote" in e.value.message


def test_dividend_beyond_spot_rejected():
    req = make_request(
        market={"cash_dividends": (CashDividend(AS_OF + timedelta(days=10), Decimal("50")),)}
    )
    with pytest.raises(DomainError) as e:
        SVC.price(req)
    assert e.value.code is ErrorCode.INVALID_MARKET_INPUT
    assert math.isfinite(SVC.price(make_request()).price)
