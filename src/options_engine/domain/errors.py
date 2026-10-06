"""Typed failure outcomes shared by every layer.

Failures are values with a stable machine-readable ``code``. Callers never
receive a plausible number (such as zero) in place of a failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_CONTRACT = "invalid_contract"
    UNSUPPORTED_CONTRACT = "unsupported_contract"
    EXPIRED_CONTRACT = "expired_contract"
    INVALID_MARKET_INPUT = "invalid_market_input"
    INVALID_VALUATION_CONTEXT = "invalid_valuation_context"
    INVALID_MODEL_PARAMETER = "invalid_model_parameter"
    INVALID_NUMERICAL_CONFIG = "invalid_numerical_config"
    UNSUPPORTED_COMBINATION = "unsupported_combination"
    UNKNOWN_ENGINE = "unknown_engine"
    WORK_LIMIT_EXCEEDED = "work_limit_exceeded"
    INVALID_TREE_PROBABILITY = "invalid_tree_probability"
    NUMERICAL_FAILURE = "numerical_failure"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"


class DomainError(Exception):
    """A validated, structured failure. ``details`` must be JSON-serialisable."""

    def __init__(
        self, code: ErrorCode, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}

    def __repr__(self) -> str:
        return f"DomainError({self.code.value!r}, {self.message!r})"
