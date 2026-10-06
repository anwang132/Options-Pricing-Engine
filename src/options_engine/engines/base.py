"""Engine protocol, capability declarations and the resolved numeric problem."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from options_engine.domain.conventions import (
    DividendTreatment,
    ExerciseStyle,
    GreekName,
    OptionType,
)
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.results import EngineOutput
from options_engine.models.heston import HestonModel


@dataclass(frozen=True, slots=True)
class PricingProblem:
    """float64 inputs resolved from domain objects at the application boundary.

    ``dividends`` holds (time_in_years, amount) for ex-dates in (0, T].
    """

    option_type: OptionType
    exercise_style: ExerciseStyle
    spot: float
    strike: float
    time_to_expiry: float
    rate: float
    dividend_yield: float
    volatility: float
    dividends: tuple[tuple[float, float], ...] = ()
    # Full Heston parameters when the model is Heston (``volatility`` is then sqrt(v0), a label).
    heston: HestonModel | None = None

    @property
    def dividend_treatment(self) -> DividendTreatment:
        return (
            DividendTreatment.ESCROWED_CASH
            if self.dividends
            else DividendTreatment.CONTINUOUS_YIELD
        )

    def dividend_pv(self) -> float:
        return math.fsum(a * math.exp(-self.rate * t) for t, a in self.dividends)

    def escrowed_spot(self) -> float:
        """Spot net of the PV of cash dividends paid before expiry (escrowed model)."""
        adjusted = self.spot - self.dividend_pv()
        if adjusted <= 0:
            raise DomainError(
                ErrorCode.INVALID_MARKET_INPUT,
                "present value of cash dividends is not below spot",
                {"spot": self.spot, "dividend_pv": self.spot - adjusted},
            )
        return adjusted

    def with_changes(self, **changes: Any) -> PricingProblem:
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    exercise_styles: frozenset[ExerciseStyle]
    dividend_treatments: frozenset[DividendTreatment]
    model_families: frozenset[str]
    # Greek -> estimator method. Absent Greeks are reported as not_supported.
    greeks: Mapping[GreekName, str]
    stochastic: bool
    batching: bool
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    zero_volatility: bool = True

    def unsupported_reasons(
        self, exercise: ExerciseStyle, dividends: DividendTreatment, family: str, volatility: float
    ) -> list[str]:
        reasons = []
        if exercise not in self.exercise_styles:
            reasons.append(f"exercise style '{exercise}' not supported")
        if dividends not in self.dividend_treatments:
            reasons.append(f"dividend treatment '{dividends}' not supported")
        if family not in self.model_families:
            reasons.append(f"model family '{family}' not supported")
        if volatility == 0 and not self.zero_volatility:
            reasons.append("zero volatility not supported (use the analytic engine)")
        return reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_styles": sorted(self.exercise_styles),
            "dividend_treatments": sorted(self.dividend_treatments),
            "model_families": sorted(self.model_families),
            "greeks": {g.value: m for g, m in self.greeks.items()},
            "stochastic": self.stochastic,
            "batching": self.batching,
            "diagnostics": list(self.diagnostics),
            "zero_volatility": self.zero_volatility,
        }


class PricingEngine(Protocol):
    engine_id: str
    version: str
    description: str
    capabilities: EngineCapabilities
    config_type: type

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput: ...
