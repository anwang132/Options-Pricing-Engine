from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from options_engine.application.hashing import canonical_json, content_hash
from options_engine.application.pricing import PricingService, resolve_problem
from options_engine.domain.conventions import ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import CashDividend
from options_engine.domain.numerics import AnalyticConfig, CRRConfig, MonteCarloConfig
from options_engine.domain.results import EngineOutput
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine
from options_engine.engines.registry import EngineRegistry
from tests.conftest import AS_OF, make_request


class SpyEngine(BlackScholesAnalyticEngine):
    engine_id = "spy"
    calls = 0

    def price(self, problem, config, greeks) -> EngineOutput:
        SpyEngine.calls += 1
        return super().price(problem, config, greeks)


def service_with_spy() -> PricingService:
    return PricingService(EngineRegistry([SpyEngine()]))


def test_unsupported_combination_rejected_before_computation():
    svc = service_with_spy()
    SpyEngine.calls = 0
    req = make_request(contract={"exercise_style": ExerciseStyle.AMERICAN}, engine_id="spy")
    with pytest.raises(DomainError) as e:
        svc.price(req)
    assert e.value.code is ErrorCode.UNSUPPORTED_COMBINATION
    assert SpyEngine.calls == 0


def test_config_type_mismatch_rejected():
    with pytest.raises(DomainError) as e:
        PricingService().price(make_request(engine_id="crr_tree", config=AnalyticConfig()))
    assert e.value.code is ErrorCode.INVALID_NUMERICAL_CONFIG


def test_unknown_engine():
    with pytest.raises(DomainError) as e:
        PricingService().price(make_request(engine_id="nope"))
    assert e.value.code is ErrorCode.UNKNOWN_ENGINE
    assert "bsm_analytic" in e.value.details["available"]


def test_expired_vs_at_expiry():
    req = make_request()
    expired = replace(
        req, valuation=replace(req.valuation, as_of=req.contract.expiry + timedelta(seconds=1))
    )
    with pytest.raises(DomainError) as e:
        PricingService().price(expired)
    assert e.value.code is ErrorCode.EXPIRED_CONTRACT
    at_expiry = replace(req, valuation=replace(req.valuation, as_of=req.contract.expiry))
    out = PricingService().price(at_expiry)
    assert out.price == 2.0  # payoff max(42 - 40, 0)
    with pytest.raises(DomainError) as e:
        PricingService().price(replace(at_expiry, engine_id="crr_tree", config=CRRConfig()))
    assert "expiry" in e.value.message


def test_result_metadata_and_contract_value():
    out = PricingService().price(make_request(contract={"multiplier": Decimal("10")}))
    assert out.contract_value == pytest.approx(out.price * 10)
    assert out.quote_basis.value == "per_underlying_unit"
    assert out.assumptions["day_count"] == "ACT/365F"
    assert len(out.request_hash) == 64


def test_dividends_before_as_of_or_after_expiry_ignored():
    req = make_request(
        market={
            "cash_dividends": (
                CashDividend(AS_OF - timedelta(days=1), Decimal("1")),
                CashDividend(AS_OF, Decimal("1")),
                CashDividend(AS_OF + timedelta(days=30), Decimal("0.5")),
                CashDividend(AS_OF + timedelta(days=400), Decimal("1")),
            )
        }
    )
    p = resolve_problem(req)
    assert len(p.dividends) == 1
    assert p.dividends[0] == (30 / 365, 0.5)


def test_request_hash_is_canonical_and_sensitive():
    a = make_request()
    b = make_request()
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(replace(a, greeks=(GreekName.DELTA,)))
    assert content_hash(a) != content_hash(replace(a, config=AnalyticConfig(method="x")))
    mc1 = replace(a, engine_id="mc_terminal_gbm", config=MonteCarloConfig(seed=1))
    mc2 = replace(a, engine_id="mc_terminal_gbm", config=MonteCarloConfig(seed=2))
    assert content_hash(mc1) != content_hash(mc2)
    assert '"strike":"40"' in canonical_json(a)


def test_compatible_engines():
    svc = PricingService()
    assert svc.compatible_engines(make_request()) == ["bsm_analytic", "crr_tree", "mc_terminal_gbm"]
    american = make_request(contract={"exercise_style": ExerciseStyle.AMERICAN})
    assert svc.compatible_engines(american) == ["crr_tree"]
    zero_vol = make_request(
        model=__import__("options_engine.models.black_scholes", fromlist=["x"]).BlackScholesModel(
            0.0
        )
    )
    assert svc.compatible_engines(zero_vol) == ["bsm_analytic"]
