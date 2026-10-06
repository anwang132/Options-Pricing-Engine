"""Cross-engine comparison, convergence studies and implied-volatility inversion."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from decimal import Decimal

from scipy import special

from options_engine.analytics.implied_vol import (
    IVConfig,
    IVResult,
    implied_volatility,
    quote_resolution,
)
from options_engine.application.pricing import (
    PriceRequest,
    PricingResult,
    PricingService,
    resolve_inputs,
    resolve_problem,
)
from options_engine.domain.contracts import VanillaContract
from options_engine.domain.conventions import ExerciseStyle
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import (
    AnalyticConfig,
    ControlVariate,
    CRRConfig,
    HestonConfig,
    LSMConfig,
    MonteCarloConfig,
    NumericalConfig,
)
from options_engine.engines.crr import build_tree
from options_engine.engines.mc_terminal import MonteCarloTerminalEngine

DEFAULT_CONFIGS: dict[str, NumericalConfig] = {
    "bsm_analytic": AnalyticConfig(),
    "crr_tree": CRRConfig(steps=1000),
    "mc_terminal_gbm": MonteCarloConfig(paths=200_000),
    "heston_fourier": HestonConfig(),
    "lsm_american": LSMConfig(),
}

MAX_CONVERGENCE_TREE_WORK = 60_000_000  # sum of N^2 over requested step counts
MAX_CONVERGENCE_TREE_STEPS = 5_000
MAX_CONVERGENCE_MC_PATHS = 4_000_000  # largest single path count


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    engine_id: str
    result: PricingResult | None
    error: DomainError | None
    difference: float | None = None
    interpretation: str | None = None


@dataclass(frozen=True, slots=True)
class Comparison:
    reference_engine: str | None
    reference_note: str
    outcomes: list[EngineOutcome]


def _interpret(result: PricingResult, reference: PricingResult) -> tuple[float, str]:
    diff = result.price - reference.price
    out = result.output
    if out.uncertainty is not None:
        se = out.uncertainty.standard_error
        z = diff / se if se > 0 else math.inf
        verdict = (
            "consistent with Monte Carlo sampling error (|z| < 3)"
            if abs(z) < 3
            else "larger than sampling error explains (|z| >= 3): investigate"
        )
        return diff, f"difference = {z:+.2f} standard errors; {verdict}"
    spread = out.diagnostics.get("odd_even_spread")
    if spread is not None:
        rel = "within" if abs(diff) <= spread else "outside"
        return diff, (
            f"discretisation error; |difference| is {rel} the odd/even spread ({spread:.2e}), "
            "a heuristic indicator rather than a bound"
        )
    return diff, "deterministic difference"


class AnalysisService:
    def __init__(self, pricing: PricingService | None = None) -> None:
        self.pricing = pricing or PricingService()

    def compare(
        self, base: PriceRequest, configs: list[tuple[str, NumericalConfig]] | None
    ) -> Comparison:
        if configs is None:
            configs = [(e, DEFAULT_CONFIGS[e]) for e in self.pricing.compatible_engines(base)]
        outcomes: list[EngineOutcome] = []
        for engine_id, config in configs:
            try:
                result = self.pricing.price(replace(base, engine_id=engine_id, config=config))
                outcomes.append(EngineOutcome(engine_id, result, None))
            except DomainError as exc:
                outcomes.append(EngineOutcome(engine_id, None, exc))
        reference = next(
            (o.result for o in outcomes if o.engine_id == "bsm_analytic" and o.result), None
        )
        note = (
            "Closed-form BSM is the reference: exact for this model up to floating-point error."
            if reference
            else "No closed-form reference for this contract (e.g. American exercise); engines "
            "are compared with each other only."
        )
        if reference is not None:
            for i, o in enumerate(outcomes):
                if o.result is not None and o.result is not reference:
                    diff, text = _interpret(o.result, reference)
                    outcomes[i] = replace(o, difference=diff, interpretation=text)
        return Comparison("bsm_analytic" if reference else None, note, outcomes)

    def _reference_price(self, base: PriceRequest) -> float | None:
        if base.contract.exercise_style is not ExerciseStyle.EUROPEAN:
            return None
        return self.pricing.price(
            replace(base, engine_id="bsm_analytic", config=AnalyticConfig(), greeks=())
        ).price

    def tree_convergence(
        self, base: PriceRequest, steps: list[int]
    ) -> tuple[float | None, list[tuple[int, float | None, str | None]], float]:
        if max(steps) > MAX_CONVERGENCE_TREE_STEPS:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED, f"steps above {MAX_CONVERGENCE_TREE_STEPS}"
            )
        if sum(n * n for n in steps) > MAX_CONVERGENCE_TREE_WORK:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED,
                "total tree work (sum of steps^2) exceeds the convergence-study budget",
                {"budget": MAX_CONVERGENCE_TREE_WORK},
            )
        request = replace(base, engine_id="crr_tree", config=CRRConfig(steps=max(steps)), greeks=())
        problem = resolve_problem(request)
        self.pricing.check_supported(request, problem)
        reference = self._reference_price(base)
        start = time.perf_counter()
        points: list[tuple[int, float | None, str | None]] = []
        for n in sorted(set(steps)):
            try:
                points.append((n, build_tree(problem, n).price, None))
            except DomainError as exc:
                points.append((n, None, exc.message))
        return reference, points, time.perf_counter() - start

    def mc_convergence(
        self,
        base: PriceRequest,
        path_counts: list[int],
        seed: int,
        antithetic: bool,
        control_variate: ControlVariate,
    ) -> tuple[float | None, list[tuple[int, PricingResult]], float]:
        if max(path_counts) > MAX_CONVERGENCE_MC_PATHS:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED, f"paths above {MAX_CONVERGENCE_MC_PATHS}"
            )
        reference = self._reference_price(base)
        start = time.perf_counter()
        out = []
        for n in sorted(set(path_counts)):
            n_even = n + (n % 2 if antithetic else 0)
            cfg = MonteCarloConfig(
                paths=n_even, seed=seed, antithetic=antithetic, control_variate=control_variate
            )
            out.append(
                (
                    n_even,
                    self.pricing.price(
                        replace(
                            base,
                            engine_id=MonteCarloTerminalEngine.engine_id,
                            config=cfg,
                            greeks=(),
                        )
                    ),
                )
            )
        return reference, out, time.perf_counter() - start


@dataclass(frozen=True, slots=True)
class QuoteInversion:
    label: str
    result: IVResult


def invert_quotes(
    contract: VanillaContract,
    valuation: ValuationContext,
    market: MarketSnapshot,
    quotes: list[tuple[str, Decimal]],
    price_resolution: float | None,
    config: IVConfig | None = None,
) -> tuple[list[QuoteInversion], dict[str, float]]:
    """Invert European quotes under BSM. American quotes are rejected, not approximated."""
    if contract.exercise_style is not ExerciseStyle.EUROPEAN:
        raise DomainError(
            ErrorCode.UNSUPPORTED_COMBINATION,
            "implied volatility inversion supports European exercise only; an American quote "
            "is not a European BSM observation",
        )
    problem = resolve_inputs(contract, valuation, market, volatility=0.0)
    S = problem.escrowed_spot()
    T, r, q = problem.time_to_expiry, problem.rate, problem.dividend_yield
    out = []
    for label, quote in quotes:
        resolution = price_resolution if price_resolution is not None else quote_resolution(quote)
        res = implied_volatility(
            float(quote), problem.option_type.sign, S, problem.strike, T, r, q, resolution, config
        )
        out.append(QuoteInversion(label, res))
    context = {
        "time_to_expiry": T,
        "forward": S * math.exp((r - q) * T),
        "discount_factor": math.exp(-r * T),
    }
    return out, context


def z_for(confidence: float) -> float:
    return float(special.ndtri(0.5 + 0.5 * confidence))
