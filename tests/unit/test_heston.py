from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from options_engine.application.pricing import PricingService
from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import AnalyticConfig, HestonConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine
from options_engine.engines.heston_fourier import (
    HestonFourierEngine,
    heston_price,
    log_characteristic,
)
from options_engine.models.heston import HestonModel
from tests.conftest import make_request, problem

M = HestonModel(v0=0.04, kappa=1.5, theta=0.05, sigma=0.4, rho=-0.7)
CFG = HestonConfig()


def hproblem(**kw):
    return problem(heston=kw.pop("model", M), **kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"rho": 1.0},
        {"sigma": -0.1},
        {"v0": -0.01},
        {"kappa": 60.0},
        {"v0": 0.0, "theta": 0.0},
        {"theta": float("nan")},
    ],
)
def test_invalid_parameters_rejected(kw):
    base = {"v0": 0.04, "kappa": 1.5, "theta": 0.04, "sigma": 0.3, "rho": -0.5}
    base.update(kw)
    with pytest.raises(DomainError) as e:
        HestonModel(**base)
    assert e.value.code is ErrorCode.INVALID_MODEL_PARAMETER


def test_characteristic_function_normalisation_and_martingale():
    w = np.array([0.0 + 0j, -1j])  # phi(0) = 1; phi(-i) = E[S_T/F] = 1
    np.testing.assert_allclose(np.exp(log_characteristic(w, 1.3, M)), [1.0, 1.0], atol=1e-14)


def test_integrated_variance_is_stable_near_zero_kappa():
    tiny = HestonModel(0.04, 1e-12, 0.09, 0.2, 0.0).integrated_variance(2.0)
    assert tiny == pytest.approx(0.04 * 2.0, rel=1e-10)  # kappa -> 0: variance stays at v0
    fast = HestonModel(0.04, 50.0, 0.09, 0.2, 0.0).integrated_variance(2.0)
    assert fast == pytest.approx(0.09 * 2.0 + (0.04 - 0.09) / 50.0, rel=1e-12)


def test_zero_vol_of_vol_routes_to_deterministic_variance_bsm():
    m = replace(M, sigma=0.0)
    price, diag = heston_price(hproblem(model=m), CFG)
    assert diag["regime"] == "deterministic_variance"
    vol = m.effective_volatility(1.0)
    expected = (
        BlackScholesAnalyticEngine().price(problem(volatility=vol), AnalyticConfig(), ()).price
    )
    assert price == expected


def test_feller_violation_reported_not_rejected():
    m = HestonModel(0.09, 0.5, 0.04, 0.9, -0.9)
    out = HestonFourierEngine().price(hproblem(model=m), CFG, ())
    assert out.diagnostics["feller_satisfied"] is False
    assert out.price > 0


def test_greeks_statuses_and_delta_identity():
    engine = HestonFourierEngine()
    call = engine.price(hproblem(), CFG, tuple(GreekName)).greeks
    put = engine.price(hproblem(option_type=OptionType.PUT), CFG, tuple(GreekName)).greeks
    assert call[GreekName.VEGA].status is GreekStatus.NOT_SUPPORTED
    assert call[GreekName.VEGA].value is None
    # Delta(call) - Delta(put) = e^{-qT} for any model with these carry inputs.
    assert call[GreekName.DELTA].value - put[GreekName.DELTA].value == pytest.approx(
        math.exp(-0.01), abs=1e-7
    )
    assert call[GreekName.GAMMA].value == pytest.approx(put[GreekName.GAMMA].value, rel=1e-5)
    assert 0 < call[GreekName.DELTA].value < 1


def test_at_expiry_is_the_payoff():
    price, diag = heston_price(hproblem(time_to_expiry=0.0, spot=110.0), CFG)
    assert (price, diag["regime"]) == (10.0, "at_expiry")


def test_capabilities_route_models_to_engines():
    svc = PricingService()
    heston_req = make_request(model=M, engine_id="heston_fourier", config=HestonConfig())
    assert svc.price(heston_req).engine_id == "heston_fourier"
    assert svc.compatible_engines(heston_req) == ["heston_fourier"]
    with pytest.raises(DomainError) as e:  # BSM engine cannot take a Heston model
        svc.price(replace(heston_req, engine_id="bsm_analytic", config=AnalyticConfig()))
    assert e.value.code is ErrorCode.UNSUPPORTED_COMBINATION
    with pytest.raises(DomainError):  # American exercise not supported by the Fourier engine
        svc.price(
            replace(
                heston_req,
                contract=replace(heston_req.contract, exercise_style=ExerciseStyle.AMERICAN),
            )
        )


def test_heston_config_limits():
    with pytest.raises(DomainError):
        HestonConfig(epsabs=0.0)
    with pytest.raises(DomainError) as e:
        HestonConfig(limit=50_000)
    assert e.value.code is ErrorCode.WORK_LIMIT_EXCEEDED
