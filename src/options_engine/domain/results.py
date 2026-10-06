"""Engine outputs: prices, per-Greek statuses, uncertainty and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from options_engine.domain.conventions import GREEK_UNITS, GreekName


class GreekStatus(StrEnum):
    OK = "ok"
    # Derivative exists only from one side (e.g. vega at sigma = 0).
    ONE_SIDED = "one_sided"
    # Payoff kink: the derivative does not exist at this point.
    UNDEFINED_AT_KINK = "undefined_at_kink"
    # Theta at expiry: calendar time cannot roll past expiration.
    NOT_APPLICABLE_AT_EXPIRY = "not_applicable_at_expiry"
    # The engine has no valid estimator for this Greek.
    NOT_SUPPORTED = "not_supported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GreekResult:
    name: GreekName
    status: GreekStatus
    value: float | None = None
    method: str | None = None
    standard_error: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        has_value = self.value is not None
        should_have = self.status in (GreekStatus.OK, GreekStatus.ONE_SIDED)
        if has_value != should_have:
            raise ValueError(f"{self.name}: status {self.status} inconsistent with value")

    @property
    def unit(self) -> str:
        return GREEK_UNITS[self.name]


@dataclass(frozen=True, slots=True)
class StochasticUncertainty:
    """Sampling uncertainty of a Monte Carlo estimate (not numerical bias)."""

    standard_error: float
    confidence_level: float
    ci_low: float
    ci_high: float
    independent_observations: int


@dataclass(frozen=True, slots=True)
class EngineOutput:
    price: float
    greeks: dict[GreekName, GreekResult]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    uncertainty: StochasticUncertainty | None = None
    warnings: tuple[str, ...] = ()
