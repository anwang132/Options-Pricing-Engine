"""Mathematical conventions and units. See docs/conventions.md for derivations.

All numerical valuation uses float64. Rates, yields and volatilities are
decimal annualised quantities (0.05 means 5% per year). Time is measured in
ACT/365F years computed from exact timestamp differences.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from options_engine.domain.errors import DomainError, ErrorCode

SECONDS_PER_DAY = 86_400
DAYS_PER_YEAR_ACT365F = 365
SECONDS_PER_YEAR = DAYS_PER_YEAR_ACT365F * SECONDS_PER_DAY

# Engineering domain limits (not mathematical limits). They keep exponentials
# finite and make work bounded; inputs outside them are rejected, not clipped.
MAX_ABS_RATE = 1.0  # |r|, |q| <= 100% per year
MAX_VOLATILITY = 5.0  # 500% per year
MAX_TIME_TO_EXPIRY_YEARS = 100.0
MAX_ABS_LOG_MONEYNESS = 50.0


class DayCount(StrEnum):
    ACT_365F = "ACT/365F"


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"

    @property
    def sign(self) -> int:
        return 1 if self is OptionType.CALL else -1


class ExerciseStyle(StrEnum):
    EUROPEAN = "european"
    AMERICAN = "american"


class SettlementType(StrEnum):
    PHYSICAL = "physical"
    CASH = "cash"


class QuoteBasis(StrEnum):
    """Prices are quoted per unit of underlying; contract value multiplies by the multiplier."""

    PER_UNIT = "per_underlying_unit"


class DividendTreatment(StrEnum):
    CONTINUOUS_YIELD = "continuous_yield"
    ESCROWED_CASH = "escrowed_cash"


class GreekName(StrEnum):
    DELTA = "delta"
    GAMMA = "gamma"
    VEGA = "vega"
    THETA = "theta"
    RHO = "rho"
    DIVIDEND_RHO = "dividend_rho"


# Internal (raw) units. Display conversions below are explicit and separate.
GREEK_UNITS: dict[GreekName, str] = {
    GreekName.DELTA: "price per 1.0 unit change in spot",
    GreekName.GAMMA: "delta per 1.0 unit change in spot",
    GreekName.VEGA: "price per 1.00 absolute volatility (100 vol points)",
    GreekName.THETA: "price per year of calendar time (ACT/365F), spot/vol/rates held fixed",
    GreekName.RHO: "price per 1.00 absolute change in the continuously compounded rate",
    GreekName.DIVIDEND_RHO: "price per 1.00 absolute change in the continuous dividend yield",
}

# Multiply a raw Greek by this factor to obtain the display unit.
GREEK_DISPLAY: dict[GreekName, tuple[float, str]] = {
    GreekName.DELTA: (1.0, "per 1.0 spot"),
    GreekName.GAMMA: (1.0, "per 1.0 spot"),
    GreekName.VEGA: (0.01, "per 1 vol point"),
    GreekName.THETA: (1.0 / DAYS_PER_YEAR_ACT365F, "per calendar day"),
    GreekName.RHO: (0.01, "per 1 percentage point"),
    GreekName.DIVIDEND_RHO: (0.01, "per 1 percentage point"),
}


def year_fraction(start: datetime, end: datetime, day_count: DayCount = DayCount.ACT_365F) -> float:
    """Signed ACT/365F year fraction between two timezone-aware timestamps."""
    if start.tzinfo is None or end.tzinfo is None:
        raise DomainError(
            ErrorCode.INVALID_VALUATION_CONTEXT,
            "timestamps must be timezone-aware; naive datetimes are ambiguous",
        )
    if day_count is not DayCount.ACT_365F:  # pragma: no cover - single convention today
        raise DomainError(ErrorCode.INVALID_VALUATION_CONTEXT, f"unsupported day count {day_count}")
    return (end - start).total_seconds() / SECONDS_PER_YEAR


def to_decimal(value: object, field: str) -> Decimal:
    """Convert user input to Decimal without passing through binary floating point.

    Floats are converted via ``repr`` (shortest round-trip text), so ``0.1``
    becomes ``Decimal('0.1')`` rather than its binary expansion.
    """
    if isinstance(value, bool):
        raise DomainError(ErrorCode.INVALID_REQUEST, f"{field}: boolean is not a number")
    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, int):
            result = Decimal(value)
        elif isinstance(value, float):
            result = Decimal(repr(value))
        elif isinstance(value, str):
            result = Decimal(value.strip())
        else:
            raise TypeError(type(value).__name__)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainError(ErrorCode.INVALID_REQUEST, f"{field}: not a decimal number") from exc
    if not result.is_finite():
        raise DomainError(ErrorCode.INVALID_REQUEST, f"{field}: must be finite")
    return result


def require_finite(value: float, field: str, code: ErrorCode) -> float:
    if not math.isfinite(value):
        raise DomainError(code, f"{field} must be finite", {"field": field, "value": repr(value)})
    return value
