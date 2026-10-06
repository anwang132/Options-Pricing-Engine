"""Snapshot file format ``options-snapshot/v1`` (JSON).

    {
      "format": "options-snapshot/v1",
      "source": "free text provenance (e.g. 'synthetic:ssvi-generator v1')",
      "synthetic": true,                       # required boolean; label synthetic data
      "as_of": "2026-09-28T20:00:00Z",          # snapshot valuation time
      "retrieved_at": "...Z" | null,
      "underlying": {"id": "SYNTH-IDX", "currency": "USD", "spot": "4500.00",
                     "spot_observed_at": "...Z" | null},
      "carry": {"rate": "0.04", "dividend_yield": "0.015", "source": "..."} | null,
      "quotes": [{"contract_id": "...", "option_type": "call", "strike": "4500",
                  "expiry": "...Z", "exercise_style": "european",
                  "settlement_type": "cash", "settlement_lag_days": 0,
                  "multiplier": "100", "deliverable": "standard",
                  "bid": "12.35", "ask": "12.80", "bid_size": 10, "ask_size": 12,
                  "quote_time": "...Z" | null}, ...]
    }

Numbers should be JSON strings to keep their decimal text; JSON numbers are
accepted and converted through their shortest decimal representation. Row-level
problems quarantine the row; they do not reject the file. File-level problems
(missing header fields) reject the file.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from options_engine.domain.conventions import ExerciseStyle, OptionType, SettlementType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.quotes import OptionQuote, QuoteIssue

FORMAT = "options-snapshot/v1"
PARSER_VERSION = "1.0.0"
MAX_QUOTES = 20_000
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ParsedSnapshot:
    source: str
    synthetic: bool
    as_of: datetime
    retrieved_at: datetime | None
    underlying_id: str
    currency: str
    spot: Decimal
    spot_observed_at: datetime | None
    rate: Decimal | None
    dividend_yield: Decimal | None
    carry_source: str | None
    quotes: tuple[OptionQuote, ...]
    extra: dict[str, Any]


def _fail(msg: str) -> DomainError:
    return DomainError(ErrorCode.INVALID_REQUEST, f"snapshot file: {msg}")


def _ts(value: Any, field: str, required: bool) -> datetime | None:
    if value is None:
        if required:
            raise _fail(f"'{field}' is required")
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise _fail(f"'{field}' is not an ISO-8601 timestamp") from None
    if ts.tzinfo is None:
        raise _fail(f"'{field}' must include a timezone")
    return ts


def _dec(value: Any) -> Decimal | None:
    """Parse a decimal; raises ValueError for unparseable, returns None for missing."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean")
    try:
        d = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("not a decimal") from None
    return d


def _header_dec(value: Any, field: str) -> Decimal:
    try:
        d = _dec(value)
    except ValueError:
        raise _fail(f"'{field}' is not a number") from None
    if d is None or not d.is_finite():
        raise _fail(f"'{field}' must be a finite number")
    return d


def _parse_quote(i: int, raw: dict[str, Any]) -> OptionQuote:
    issues: list[QuoteIssue] = []

    def dec(name: str) -> Decimal | None:
        try:
            d = _dec(raw.get(name))
        except ValueError:
            issues.append(QuoteIssue.UNPARSEABLE)
            return None
        if d is not None and not d.is_finite():
            issues.append(QuoteIssue.NONFINITE)
            return None
        return d

    def enum[E](name: str, cls: type[E]) -> E | None:
        v = raw.get(name)
        if v is None:
            return None
        try:
            return cls(str(v).lower())  # type: ignore[call-arg]
        except ValueError:
            issues.append(QuoteIssue.UNPARSEABLE)
            return None

    def integer(name: str) -> int | None:
        v = raw.get(name)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            issues.append(QuoteIssue.UNPARSEABLE)
            return None

    def ts(name: str) -> datetime | None:
        try:
            return _ts(raw.get(name), name, required=False)
        except DomainError:
            issues.append(QuoteIssue.UNPARSEABLE)
            return None

    q = OptionQuote(
        row=i,
        contract_id=None if raw.get("contract_id") is None else str(raw["contract_id"]),
        option_type=enum("option_type", OptionType),
        strike=dec("strike"),
        expiry=ts("expiry"),
        exercise_style=enum("exercise_style", ExerciseStyle),
        settlement_type=enum("settlement_type", SettlementType),
        settlement_lag_days=integer("settlement_lag_days"),
        multiplier=dec("multiplier"),
        deliverable=None if raw.get("deliverable") is None else str(raw["deliverable"]),
        bid=dec("bid"),
        ask=dec("ask"),
        bid_size=integer("bid_size"),
        ask_size=integer("ask_size"),
        quote_time=ts("quote_time"),
    )
    if q.option_type is None or q.strike is None or q.expiry is None:
        issues.append(QuoteIssue.UNPARSEABLE)
    return dataclasses.replace(q, issues=tuple(dict.fromkeys(issues)))


def parse(raw_bytes: bytes) -> ParsedSnapshot:
    if len(raw_bytes) > MAX_BYTES:
        raise DomainError(ErrorCode.WORK_LIMIT_EXCEEDED, f"snapshot larger than {MAX_BYTES} bytes")
    try:
        doc = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _fail(f"not valid JSON ({exc})") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise _fail(f"'format' must be '{FORMAT}'")
    if not isinstance(doc.get("synthetic"), bool):
        raise _fail("'synthetic' must be true or false (label synthetic data explicitly)")
    und = doc.get("underlying")
    if not isinstance(und, dict):
        raise _fail("'underlying' object is required")
    quotes = doc.get("quotes")
    if not isinstance(quotes, list):
        raise _fail("'quotes' must be a list")
    if len(quotes) > MAX_QUOTES:
        raise DomainError(ErrorCode.WORK_LIMIT_EXCEEDED, f"more than {MAX_QUOTES} quotes")
    carry = doc.get("carry")
    spot = _header_dec(und.get("spot"), "underlying.spot")
    if spot <= 0:
        raise _fail("'underlying.spot' must be positive")
    as_of = _ts(doc.get("as_of"), "as_of", required=True)
    assert as_of is not None
    return ParsedSnapshot(
        source=str(doc.get("source") or "unknown"),
        synthetic=doc["synthetic"],
        as_of=as_of,
        retrieved_at=_ts(doc.get("retrieved_at"), "retrieved_at", required=False),
        underlying_id=str(und.get("id") or ""),
        currency=str(und.get("currency") or ""),
        spot=spot,
        spot_observed_at=_ts(und.get("spot_observed_at"), "underlying.spot_observed_at", False),
        rate=None if not carry else _header_dec(carry.get("rate"), "carry.rate"),
        dividend_yield=None
        if not carry
        else _header_dec(carry.get("dividend_yield", "0"), "carry.dividend_yield"),
        carry_source=None if not carry else str(carry.get("source") or "unspecified"),
        quotes=tuple(
            _parse_quote(i, q) if isinstance(q, dict) else _unparseable_row(i)
            for i, q in enumerate(quotes)
        ),
        extra={k: v for k, v in doc.items() if k.startswith("synthetic_")},
    )


def _unparseable_row(i: int) -> OptionQuote:
    return OptionQuote(row=i, issues=(QuoteIssue.UNPARSEABLE,))
