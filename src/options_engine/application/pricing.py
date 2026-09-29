"""Pricing use cases shared by the CLI and HTTP API.

Every request is validated and checked against the chosen engine's
capabilities before any numerical work starts.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from options_engine import __version__
from options_engine.application.hashing import content_hash
from options_engine.domain.contracts import VanillaContract
from options_engine.domain.conventions import (
    MAX_ABS_LOG_MONEYNESS,
    MAX_TIME_TO_EXPIRY_YEARS,
    GreekName,
    QuoteBasis,
    year_fraction,
)
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import AnalyticConfig, NumericalConfig
from options_engine.domain.results import EngineOutput
from options_engine.engines.base import PricingEngine, PricingProblem
from options_engine.engines.registry import EngineRegistry, default_registry
from options_engine.models.black_scholes import ModelSpec


@dataclass(frozen=True, slots=True)
class PriceRequest:
    contract: VanillaContract
    valuation: ValuationContext
    market: MarketSnapshot
    model: ModelSpec
    engine_id: str = "bsm_analytic"
    config: NumericalConfig = field(default_factory=AnalyticConfig)
    greeks: tuple[GreekName, ...] = tuple(GreekName)


@dataclass(frozen=True, slots=True)
class PricingResult:
    request_hash: str
    engine_id: str
    engine_version: str
    package_version: str
    model_family: str
    currency: str
    quote_basis: QuoteBasis
    price: float  # per underlying unit, unrounded float64
    contract_value: float  # price * multiplier (one long contract)
    multiplier: str
    time_to_expiry: float
    dividend_treatment: str
    output: EngineOutput
    elapsed_seconds: float
    assumptions: dict[str, Any] = field(default_factory=dict)


def resolve_problem(request: PriceRequest) -> PricingProblem:
    return resolve_inputs(
        request.contract, request.valuation, request.market, request.model.volatility
    )


def resolve_inputs(
    contract: VanillaContract,
    valuation: ValuationContext,
    market: MarketSnapshot,
    volatility: float,
) -> PricingProblem:
    """Convert validated domain inputs to float64 at the numerical boundary."""
    contract.ensure_priceable()
    T = year_fraction(valuation.as_of, contract.expiry, valuation.day_count)
    if T < 0:
        raise DomainError(
            ErrorCode.EXPIRED_CONTRACT,
            "valuation time is after expiry; an expired contract has no model price",
            {"time_to_expiry_years": T},
        )
    if T > MAX_TIME_TO_EXPIRY_YEARS:
        raise DomainError(ErrorCode.UNSUPPORTED_CONTRACT, "maturity beyond supported domain limit")
    # Dividends with ex-date at or before the valuation time are already in the spot.
    dividends = tuple(
        (year_fraction(valuation.as_of, d.ex_date), float(d.amount))
        for d in sorted(market.cash_dividends, key=lambda d: d.ex_date)
        if valuation.as_of < d.ex_date <= contract.expiry
    )
    problem = PricingProblem(
        option_type=contract.option_type,
        exercise_style=contract.exercise_style,
        spot=float(market.spot),
        strike=float(contract.strike),
        time_to_expiry=T,
        rate=float(market.rate),
        dividend_yield=float(market.dividend_yield),
        volatility=volatility,
        dividends=dividends,
    )
    if abs(math.log(problem.spot / problem.strike)) > MAX_ABS_LOG_MONEYNESS:
        raise DomainError(
            ErrorCode.INVALID_MARKET_INPUT, "spot/strike ratio outside supported domain"
        )
    return problem


def unsupported_reasons(engine: PricingEngine, problem: PricingProblem, family: str) -> list[str]:
    caps = engine.capabilities
    at_expiry = problem.time_to_expiry == 0.0
    reasons = caps.unsupported_reasons(
        problem.exercise_style,
        problem.dividend_treatment,
        family,
        # At expiry volatility is irrelevant; the expiry check below applies instead.
        1.0 if at_expiry else problem.volatility,
    )
    if at_expiry and not caps.zero_volatility:
        reasons.append("contract is at expiry (use the analytic engine for the payoff)")
    return reasons


class PricingService:
    def __init__(self, registry: EngineRegistry | None = None) -> None:
        self.registry = registry or default_registry()

    def check_supported(self, request: PriceRequest, problem: PricingProblem) -> PricingEngine:
        engine = self.registry.get(request.engine_id)
        if not isinstance(request.config, engine.config_type):
            raise DomainError(
                ErrorCode.INVALID_NUMERICAL_CONFIG,
                f"engine '{engine.engine_id}' expects {engine.config_type.__name__}",
                {"received": type(request.config).__name__},
            )
        reasons = unsupported_reasons(engine, problem, request.model.family)
        if reasons:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION,
                f"engine '{engine.engine_id}' cannot price this request: " + "; ".join(reasons),
                {
                    "reasons": reasons,
                    "engines_supporting_request": self._supporting(problem, request),
                },
            )
        return engine

    def _supporting(self, problem: PricingProblem, request: PriceRequest) -> list[str]:
        return [
            e
            for e in self.registry.ids()
            if not unsupported_reasons(self.registry.get(e), problem, request.model.family)
        ]

    def compatible_engines(self, request: PriceRequest) -> list[str]:
        return self._supporting(resolve_problem(request), request)

    def evaluate(self, request: PriceRequest) -> tuple[PricingProblem, PricingEngine, EngineOutput]:
        """Validate, check capabilities and run the engine, without result metadata.

        Bulk callers (portfolio scenarios) use this directly: the request hash and
        assumption text in ``price`` cost ~30% of a closed-form repricing.
        """
        problem = resolve_problem(request)
        engine = self.check_supported(request, problem)
        return problem, engine, engine.price(problem, request.config, request.greeks)

    def price(self, request: PriceRequest) -> PricingResult:
        start = time.perf_counter()
        problem, engine, output = self.evaluate(request)
        elapsed = time.perf_counter() - start
        multiplier = request.contract.multiplier
        return PricingResult(
            request_hash=content_hash(request),
            engine_id=engine.engine_id,
            engine_version=engine.version,
            package_version=__version__,
            model_family=request.model.family,
            currency=request.contract.currency,
            quote_basis=QuoteBasis.PER_UNIT,
            price=output.price,
            contract_value=output.price * float(multiplier),
            multiplier=str(multiplier),
            time_to_expiry=problem.time_to_expiry,
            dividend_treatment=problem.dividend_treatment.value,
            output=output,
            elapsed_seconds=elapsed,
            assumptions=assumptions_for(problem, request),
        )


def assumptions_for(problem: PricingProblem, request: PriceRequest) -> dict[str, Any]:
    return {
        "model": "Black-Scholes-Merton (GBM, flat volatility, risk-neutral)",
        "exercise_style": problem.exercise_style.value,
        "dividends": (
            "escrowed cash dividends (spot net of PV of dividends before expiry)"
            if problem.dividends
            else "continuous dividend yield"
        ),
        "rates": "flat continuously compounded rate",
        "day_count": request.valuation.day_count.value,
        "time_to_expiry_years": problem.time_to_expiry,
        "quote_basis": QuoteBasis.PER_UNIT.value,
        "settlement": f"{request.contract.settlement.type.value}, paid at expiry",
    }
