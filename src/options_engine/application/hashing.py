"""Canonical JSON and content hashes used for replay and cache keys."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Deterministic JSON-compatible form. Decimals keep their text; floats use repr."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(to_jsonable(k)): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        items = [to_jsonable(v) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, frozenset | set) else items
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        return to_jsonable(value.item())
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))
