from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from options_engine.application.pricing import PriceRequest
from options_engine.domain.contracts import VanillaContract
from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.engines.base import PricingProblem
from options_engine.models.black_scholes import BlackScholesModel

AS_OF = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
HALF_YEAR = AS_OF + timedelta(days=182.5)


def make_contract(**kw) -> VanillaContract:
    base = {
        "underlying": "SYNTH",
        "currency": "USD",
        "strike": Decimal("40"),
        "option_type": OptionType.CALL,
        "exercise_style": ExerciseStyle.EUROPEAN,
        "expiry": HALF_YEAR,
        "multiplier": Decimal("100"),
    }
    base.update(kw)
    return VanillaContract(**base)


def make_request(**kw) -> PriceRequest:
    contract_kw = kw.pop("contract", {})
    market_kw = kw.pop("market", {})
    market = {"spot": Decimal("42"), "rate": Decimal("0.10")}
    market.update(market_kw)
    base = {
        "contract": make_contract(**contract_kw),
        "valuation": ValuationContext(AS_OF),
        "market": MarketSnapshot(**market),
        "model": BlackScholesModel(0.2),
    }
    base.update(kw)
    return PriceRequest(**base)


def problem(**kw) -> PricingProblem:
    base = {
        "option_type": OptionType.CALL,
        "exercise_style": ExerciseStyle.EUROPEAN,
        "spot": 100.0,
        "strike": 100.0,
        "time_to_expiry": 1.0,
        "rate": 0.03,
        "dividend_yield": 0.01,
        "volatility": 0.2,
    }
    base.update(kw)
    return PricingProblem(**base)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """No test may write snapshots, fits or paper ledgers into the working tree."""
    monkeypatch.setenv("OPTIONS_ENGINE_DATA_DIR", str(tmp_path_factory.mktemp("data")))


@pytest.fixture
def hull_request() -> PriceRequest:
    return make_request()
