"""Import an option-chain CSV export into ``options-snapshot/v1`` (ADR 0023).

Brokers and data vendors export chains in many shapes, so the importer is
driven by a small TOML mapping file instead of a hard-coded vendor format:

    [mapping]
    layout = "long"            # one row per contract; or "straddle": calls and puts per strike
    delimiter = ","
    skip_rows = 0              # lines before the header row
    expiry = "Expiration"      # column names ...
    strike = "Strike"
    option_type = "Type"       # long layout only
    bid = "Bid"                # long layout; straddle uses call_bid/call_ask/put_bid/put_ask
    ask = "Ask"
    bid_size = "Bid Size"      # optional columns
    ask_size = "Ask Size"
    quote_time = "Quote Time"
    contract_id = "Symbol"

    [mapping.option_type_values]
    call = ["C", "Call"]
    put = ["P", "Put"]

    [filters]                  # optional: keep only rows whose column value is listed
    Root = ["SPXW"]

    [formats]
    expiry = "%Y-%m-%d"        # strptime format, a list tried in order, or "iso8601"
    quote_time = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"]
    timezone = "America/New_York"  # for naive timestamps and date-only expiries
    expiry_time = "16:00"      # clock time attached to date-only expiries

    [contract]                 # terms the export does not carry, stated explicitly
    exercise_style = "european"
    settlement_type = "cash"
    settlement_lag_days = 0
    multiplier = "100"
    deliverable = "standard"

Snapshot-level facts (spot, valuation time, carry) are passed separately: an
export rarely contains them reliably, and they must not be guessed. Values the
importer cannot parse are passed through as text so that ingestion quarantines
the row with a specific reason instead of the importer silently dropping it.
The output records the source file's and the mapping's SHA-256 in ``source``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import tomllib
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from options_engine.domain.errors import DomainError, ErrorCode

LAYOUTS = ("long", "straddle")


def _fail(msg: str) -> DomainError:
    return DomainError(ErrorCode.INVALID_REQUEST, f"CSV import: {msg}")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class ChainMapping:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.mapping: dict[str, Any] = doc.get("mapping", {})
        self.formats: dict[str, Any] = doc.get("formats", {})
        self.contract: dict[str, Any] = doc.get("contract", {})
        self.filters: dict[str, set[str]] = {
            col: {str(v) for v in allowed} for col, allowed in doc.get("filters", {}).items()
        }
        self.layout = self.mapping.get("layout", "long")
        if self.layout not in LAYOUTS:
            raise _fail(f"mapping.layout must be one of {LAYOUTS}")
        required = ["expiry", "strike"] + (
            ["option_type", "bid", "ask"]
            if self.layout == "long"
            else ["call_bid", "call_ask", "put_bid", "put_ask"]
        )
        missing = [k for k in required if k not in self.mapping]
        if missing:
            raise _fail(f"mapping is missing column names for {missing}")
        if "exercise_style" not in self.contract:
            raise _fail(
                "contract.exercise_style is required (european index options vs american "
                "equity options is not something to guess)"
            )
        tz = self.formats.get("timezone", "UTC")
        try:
            self.tz = ZoneInfo(tz)
        except Exception as exc:  # zoneinfo raises several types for unknown keys
            raise _fail(f"unknown timezone '{tz}' ({exc})") from None
        values = self.mapping.get(
            "option_type_values", {"call": ["C", "Call", "call"], "put": ["P", "Put", "put"]}
        )
        self.type_lookup = {str(v).strip().lower(): kind for kind, vs in values.items() for v in vs}

    @classmethod
    def load(cls, path: Path) -> tuple[ChainMapping, bytes]:
        raw = path.read_bytes()
        try:
            return cls(tomllib.loads(raw.decode())), raw
        except tomllib.TOMLDecodeError as exc:
            raise _fail(f"mapping file is not valid TOML ({exc})") from None

    def _parse(self, text: str, key: str, default: str) -> tuple[datetime, str] | None:
        """(datetime, format used) with the first matching format, or None."""
        spec = self.formats.get(key, default)
        for fmt in [spec] if isinstance(spec, str) else list(spec):
            try:
                if fmt == "iso8601":
                    return datetime.fromisoformat(text.strip().replace("Z", "+00:00")), fmt
                return datetime.strptime(text.strip(), fmt), fmt
            except ValueError:
                continue
        return None

    def expiry(self, text: str) -> str:
        """ISO-8601 UTC expiry, or the original text when it does not parse."""
        parsed = self._parse(text, "expiry", "%Y-%m-%d")
        if parsed is None:
            return text
        dt, fmt = parsed
        if fmt != "iso8601" and "%H" not in fmt:
            hh, mm = (int(x) for x in self.formats.get("expiry_time", "16:00").split(":"))
            dt = datetime.combine(dt.date(), time(hh, mm))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return _iso(dt.astimezone(ZoneInfo("UTC")))

    def timestamp(self, text: str | None) -> str | None:
        if text is None or not text.strip():
            return None
        parsed = self._parse(text, "quote_time", "%Y-%m-%d %H:%M:%S")
        if parsed is None:
            return text
        dt = parsed[0]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return _iso(dt.astimezone(ZoneInfo("UTC")))


def _cell(row: dict[str, str], col: str | None) -> str | None:
    if col is None:
        return None
    v = row.get(col)
    if v is None:
        return None
    v = v.strip().replace(",", "") if v.strip() not in ("", "-", "--", "N/A") else ""
    return v or None


def _int(text: str | None) -> int | str | None:
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return text


def _quote(
    m: ChainMapping,
    row: dict[str, str],
    option_type: str | None,
    bid_col: str,
    ask_col: str,
    bid_size_col: str | None,
    ask_size_col: str | None,
    id_col: str | None,
) -> dict[str, Any]:
    c = m.contract
    strike = _cell(row, m.mapping["strike"])
    expiry_text = _cell(row, m.mapping["expiry"])
    return {
        "contract_id": _cell(row, id_col),
        "option_type": option_type,
        "strike": strike,
        "expiry": None if expiry_text is None else m.expiry(expiry_text),
        "exercise_style": c["exercise_style"],
        "settlement_type": c.get("settlement_type"),
        "settlement_lag_days": c.get("settlement_lag_days"),
        "multiplier": None if c.get("multiplier") is None else str(c["multiplier"]),
        "deliverable": c.get("deliverable"),
        "bid": _cell(row, bid_col),
        "ask": _cell(row, ask_col),
        "bid_size": _int(_cell(row, bid_size_col)),
        "ask_size": _int(_cell(row, ask_size_col)),
        "quote_time": m.timestamp(_cell(row, m.mapping.get("quote_time"))),
    }


def convert(
    csv_bytes: bytes,
    mapping: ChainMapping,
    *,
    underlying_id: str,
    currency: str,
    spot: str,
    as_of: datetime,
    spot_observed_at: datetime | None = None,
    rate: str | None = None,
    dividend_yield: str | None = None,
    source: str = "csv import",
    synthetic: bool = False,
) -> dict[str, Any]:
    """CSV bytes -> options-snapshot/v1 document (not yet ingested)."""
    if as_of.tzinfo is None:
        raise _fail("as_of must include a timezone")
    text = csv_bytes.decode("utf-8-sig")
    lines = text.splitlines()[int(mapping.mapping.get("skip_rows", 0)) :]
    reader = csv.DictReader(
        io.StringIO("\n".join(lines)), delimiter=mapping.mapping.get("delimiter", ",")
    )
    header = set(reader.fieldnames or [])
    needed = {
        v
        for k, v in mapping.mapping.items()
        if isinstance(v, str) and k not in ("layout", "delimiter")
    } | set(mapping.filters)
    absent = sorted(needed - header)
    if absent:
        raise _fail(f"columns not found in the file: {absent}")
    mp = mapping.mapping
    quotes: list[dict[str, Any]] = []
    filtered_out = 0
    for row in reader:
        if any(
            (row.get(col) or "").strip() not in allowed for col, allowed in mapping.filters.items()
        ):
            filtered_out += 1
            continue
        if mapping.layout == "long":
            raw_type = (_cell(row, mp["option_type"]) or "").lower()
            kind = mapping.type_lookup.get(raw_type, raw_type or None)
            quotes.append(
                _quote(
                    mapping,
                    row,
                    kind,
                    mp["bid"],
                    mp["ask"],
                    mp.get("bid_size"),
                    mp.get("ask_size"),
                    mp.get("contract_id"),
                )
            )
        else:
            for kind in ("call", "put"):
                quotes.append(
                    _quote(
                        mapping,
                        row,
                        kind,
                        mp[f"{kind}_bid"],
                        mp[f"{kind}_ask"],
                        mp.get(f"{kind}_bid_size"),
                        mp.get(f"{kind}_ask_size"),
                        mp.get(f"{kind}_contract_id"),
                    )
                )
    if filtered_out:
        source = f"{source}; {filtered_out} rows excluded by mapping filters"
    return {
        "format": "options-snapshot/v1",
        "source": source,
        "synthetic": synthetic,
        "as_of": _iso(as_of),
        "retrieved_at": None,
        "underlying": {
            "id": underlying_id,
            "currency": currency,
            "spot": spot,
            "spot_observed_at": None if spot_observed_at is None else _iso(spot_observed_at),
        },
        "carry": None
        if rate is None
        else {"rate": rate, "dividend_yield": dividend_yield or "0", "source": "user input"},
        "quotes": quotes,
    }


def convert_files(csv_path: Path, mapping_path: Path, **header: Any) -> dict[str, Any]:
    mapping, mapping_raw = ChainMapping.load(mapping_path)
    raw = csv_path.read_bytes()
    provenance = (
        f"csv import of {csv_path.name} (sha256 {hashlib.sha256(raw).hexdigest()[:16]}) with "
        f"mapping {mapping_path.name} (sha256 {hashlib.sha256(mapping_raw).hexdigest()[:16]})"
    )
    return convert(raw, mapping, source=provenance, **header)
