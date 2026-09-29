"""Market inputs and valuation context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from options_engine.domain.conventions import MAX_ABS_RATE, DayCount
from options_engine.domain.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class ValuationContext:
    """Explicit as-of time. Nothing in the engine reads the wall clock."""

    as_of: datetime
    day_count: DayCount = DayCount.ACT_365F

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise DomainError(ErrorCode.INVALID_VALUATION_CONTEXT, "as_of must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CashDividend:
    """A known cash dividend per unit of underlying.

    The ex-date is used both as the spot-drop time and the discounting time
    (payment-date discounting is not modelled; see ADR 0005).
    """

    ex_date: datetime
    amount: Decimal

    def __post_init__(self) -> None:
        if self.ex_date.tzinfo is None:
            raise DomainError(ErrorCode.INVALID_MARKET_INPUT, "dividend ex_date must be tz-aware")
        if not self.amount.is_finite() or self.amount <= 0:
            raise DomainError(ErrorCode.INVALID_MARKET_INPUT, "dividend amount must be positive")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Market inputs for one underlying at one time.

    Provenance fields are optional because manual inputs have none; missing
    freshness metadata stays ``None`` (unknown) and is never defaulted.
    """

    spot: Decimal
    rate: Decimal
    dividend_yield: Decimal = Decimal(0)
    cash_dividends: tuple[CashDividend, ...] = ()
    snapshot_id: str | None = None
    source: str = "user_input"
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("spot", "rate", "dividend_yield"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise DomainError(
                    ErrorCode.INVALID_MARKET_INPUT, f"{name} must be a finite Decimal"
                )
        if self.spot <= 0:
            raise DomainError(ErrorCode.INVALID_MARKET_INPUT, "spot must be positive")
        for name in ("rate", "dividend_yield"):
            if abs(getattr(self, name)) > Decimal(str(MAX_ABS_RATE)):
                raise DomainError(
                    ErrorCode.INVALID_MARKET_INPUT,
                    f"|{name}| exceeds the supported domain limit of {MAX_ABS_RATE}",
                    {"field": name},
                )
        dates = [d.ex_date for d in self.cash_dividends]
        if len(set(dates)) != len(dates):
            raise DomainError(ErrorCode.INVALID_MARKET_INPUT, "duplicate dividend ex-dates")
