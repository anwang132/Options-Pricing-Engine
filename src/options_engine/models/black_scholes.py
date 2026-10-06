"""Black-Scholes-Merton (geometric Brownian motion) model specification."""

from __future__ import annotations

import math
from dataclasses import dataclass

from options_engine.domain.conventions import MAX_VOLATILITY
from options_engine.domain.errors import DomainError, ErrorCode

BLACK_SCHOLES = "black_scholes"


@dataclass(frozen=True, slots=True)
class BlackScholesModel:
    """Flat volatility GBM under the risk-neutral measure.

    ``volatility`` is a decimal annualised model parameter, not a market
    observation. Zero is allowed (deterministic forward); negative is not.
    """

    volatility: float
    family: str = BLACK_SCHOLES

    def __post_init__(self) -> None:
        if not math.isfinite(self.volatility):
            raise DomainError(ErrorCode.INVALID_MODEL_PARAMETER, "volatility must be finite")
        if self.volatility < 0:
            raise DomainError(ErrorCode.INVALID_MODEL_PARAMETER, "volatility cannot be negative")
        if self.volatility > MAX_VOLATILITY:
            raise DomainError(
                ErrorCode.INVALID_MODEL_PARAMETER,
                f"volatility exceeds the supported domain limit of {MAX_VOLATILITY}",
            )
