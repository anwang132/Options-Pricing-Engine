"""Option quotes inside a market snapshot, with explicit data-quality outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from options_engine.domain.conventions import ExerciseStyle, OptionType, SettlementType


class QuoteIssue(StrEnum):
    """Reasons a quote is quarantined (excluded) or flagged (kept, with a caveat)."""

    # quarantine
    UNPARSEABLE = "unparseable_field"
    NONFINITE = "nonfinite_value"
    NEGATIVE_PRICE = "negative_price"
    MISSING_BID_OR_ASK = "missing_bid_or_ask"
    CROSSED = "crossed_quote"
    ZERO_BID = "zero_bid"
    EXPIRED = "expired_contract"
    DUPLICATE_CONFLICT = "duplicate_conflicting_quote"
    DUPLICATE_IDENTICAL = "duplicate_identical_quote"
    WIDE_SPREAD = "wide_spread"
    STALE = "stale_quote"
    UNDERLYING_TIME_MISMATCH = "underlying_time_mismatch"
    UNSUPPORTED_DELIVERABLE = "unsupported_deliverable"
    UNSUPPORTED_SETTLEMENT = "unsupported_settlement_lag"
    INVALID_STRIKE = "invalid_strike"
    # flags (kept)
    QUOTE_TIME_UNKNOWN = "quote_time_unknown"
    MISSING_SIZE = "missing_size"


FLAG_ONLY = frozenset({QuoteIssue.QUOTE_TIME_UNKNOWN, QuoteIssue.MISSING_SIZE})


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """A normalised quote. Unknown metadata stays None; it is never defaulted."""

    row: int
    contract_id: str | None = None
    option_type: OptionType | None = None
    strike: Decimal | None = None
    expiry: datetime | None = None
    exercise_style: ExerciseStyle | None = None
    settlement_type: SettlementType | None = None
    settlement_lag_days: int | None = None
    multiplier: Decimal | None = None
    deliverable: str | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    quote_time: datetime | None = None
    issues: tuple[QuoteIssue, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return all(i in FLAG_ONLY for i in self.issues)

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """Versioned filtering policy; recorded in every snapshot manifest."""

    version: str = "1"
    max_relative_spread: float = 0.5  # (ask - bid) / mid
    max_quote_age_seconds: int = 900  # vs snapshot as_of
    max_underlying_gap_seconds: int = 60  # |quote_time - spot observation time|
    zero_bid: str = "quarantine"  # bid of exactly 0 gives no usable lower price
    missing_size: str = "flag"
    unknown_quote_time: str = "flag"  # freshness unknown: kept, never assumed fresh
    duplicate_identical: str = "keep_first"
    duplicate_conflicting: str = "quarantine_all"
