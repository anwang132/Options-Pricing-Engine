"""Immutable instrument definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from options_engine.domain.conventions import ExerciseStyle, OptionType, SettlementType
from options_engine.domain.errors import DomainError, ErrorCode

_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class Settlement:
    """How and when the contract settles.

    Only same-time settlement (``lag_days == 0``) is priced today; a positive
    lag would require discounting the payoff beyond expiry and is rejected as
    unsupported rather than silently ignored.
    """

    type: SettlementType = SettlementType.PHYSICAL
    lag_days: int = 0

    def __post_init__(self) -> None:
        if self.lag_days < 0:
            raise DomainError(ErrorCode.INVALID_CONTRACT, "settlement lag cannot be negative")


@dataclass(frozen=True, slots=True)
class VanillaContract:
    """A listed-style vanilla call or put.

    ``expiry`` is the exercise/expiration timestamp used for valuation.
    ``last_trade`` is informational metadata: it is not the valuation horizon.
    ``multiplier`` is instrument metadata (units of underlying per contract).
    """

    underlying: str
    currency: str
    strike: Decimal
    option_type: OptionType
    exercise_style: ExerciseStyle
    expiry: datetime
    multiplier: Decimal
    settlement: Settlement = Settlement()
    last_trade: datetime | None = None
    deliverable: str = "standard"
    contract_id: str | None = None

    def __post_init__(self) -> None:
        if not self.underlying.strip():
            raise DomainError(ErrorCode.INVALID_CONTRACT, "underlying identifier is required")
        if not _CURRENCY.match(self.currency):
            raise DomainError(ErrorCode.INVALID_CONTRACT, "currency must be a 3-letter ISO code")
        if not isinstance(self.strike, Decimal) or not self.strike.is_finite():
            raise DomainError(ErrorCode.INVALID_CONTRACT, "strike must be a finite Decimal")
        if self.strike <= 0:
            raise DomainError(ErrorCode.INVALID_CONTRACT, "strike must be positive")
        if not isinstance(self.multiplier, Decimal) or not self.multiplier.is_finite():
            raise DomainError(ErrorCode.INVALID_CONTRACT, "multiplier must be a finite Decimal")
        if self.multiplier <= 0:
            raise DomainError(ErrorCode.INVALID_CONTRACT, "multiplier must be positive")
        if self.expiry.tzinfo is None:
            raise DomainError(ErrorCode.INVALID_CONTRACT, "expiry must be timezone-aware")
        if self.last_trade is not None:
            if self.last_trade.tzinfo is None:
                raise DomainError(ErrorCode.INVALID_CONTRACT, "last_trade must be timezone-aware")
            if self.last_trade > self.expiry:
                raise DomainError(ErrorCode.INVALID_CONTRACT, "last_trade cannot follow expiry")

    def ensure_priceable(self) -> None:
        """Reject contract features no engine supports, before any computation."""
        if self.deliverable != "standard":
            raise DomainError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                "adjusted/non-standard deliverables are not supported",
                {"deliverable": self.deliverable},
            )
        if self.settlement.lag_days != 0:
            raise DomainError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                "settlement lag after expiry is not modelled; only lag_days=0 is supported",
                {"lag_days": self.settlement.lag_days},
            )
