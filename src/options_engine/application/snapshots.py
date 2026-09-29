"""Snapshot ingestion: parse, assess quality, persist immutably, reload.

Every excluded quote keeps its reasons; counts by reason are part of the
manifest, so no data loss is hidden behind a cleaned table.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from options_engine.adapters.snapshots import format_v1
from options_engine.adapters.snapshots.store import ObjectStore, default_data_dir
from options_engine.application.hashing import canonical_json, sha256_hex, to_jsonable
from options_engine.domain.conventions import ExerciseStyle, OptionType, SettlementType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot
from options_engine.domain.quotes import FLAG_ONLY, OptionQuote, QualityPolicy, QuoteIssue


def assess(parsed: format_v1.ParsedSnapshot, policy: QualityPolicy) -> list[OptionQuote]:
    """Attach quarantine reasons and flags to every quote (pure function)."""
    out: list[OptionQuote] = []
    for q in parsed.quotes:
        issues = list(q.issues)
        if q.strike is not None and q.strike <= 0:
            issues.append(QuoteIssue.INVALID_STRIKE)
        for price in (q.bid, q.ask):
            if price is not None and price < 0:
                issues.append(QuoteIssue.NEGATIVE_PRICE)
        if (q.bid is None or q.ask is None) and QuoteIssue.UNPARSEABLE not in issues:
            issues.append(QuoteIssue.MISSING_BID_OR_ASK)
        if q.bid is not None and q.ask is not None:
            if q.bid > q.ask:
                issues.append(QuoteIssue.CROSSED)
            elif q.bid == 0 and policy.zero_bid == "quarantine":
                issues.append(QuoteIssue.ZERO_BID)
            elif (
                q.bid > 0
                and float((q.ask - q.bid) / ((q.ask + q.bid) / 2)) > policy.max_relative_spread
            ):
                issues.append(QuoteIssue.WIDE_SPREAD)
        if q.expiry is not None and q.expiry <= parsed.as_of:
            issues.append(QuoteIssue.EXPIRED)
        if q.deliverable not in (None, "standard"):
            issues.append(QuoteIssue.UNSUPPORTED_DELIVERABLE)
        if q.settlement_lag_days not in (None, 0):
            issues.append(QuoteIssue.UNSUPPORTED_SETTLEMENT)
        if q.quote_time is None:
            issues.append(QuoteIssue.QUOTE_TIME_UNKNOWN)
        else:
            if (parsed.as_of - q.quote_time).total_seconds() > policy.max_quote_age_seconds:
                issues.append(QuoteIssue.STALE)
            if (
                parsed.spot_observed_at is not None
                and abs((q.quote_time - parsed.spot_observed_at).total_seconds())
                > policy.max_underlying_gap_seconds
            ):
                issues.append(QuoteIssue.UNDERLYING_TIME_MISMATCH)
        if q.bid_size is None or q.ask_size is None:
            issues.append(QuoteIssue.MISSING_SIZE)
        out.append(dataclasses.replace(q, issues=tuple(dict.fromkeys(issues))))
    return _mark_duplicates(out)


def _mark_duplicates(quotes: list[OptionQuote]) -> list[OptionQuote]:
    groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for i, q in enumerate(quotes):
        if q.option_type is not None and q.strike is not None and q.expiry is not None:
            groups[(q.option_type, q.strike, q.expiry, q.exercise_style, q.deliverable)].append(i)
    out = list(quotes)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        prices = {(quotes[i].bid, quotes[i].ask) for i in idxs}
        for n, i in enumerate(idxs):
            if len(prices) > 1:
                issue = QuoteIssue.DUPLICATE_CONFLICT
            elif n > 0:
                issue = QuoteIssue.DUPLICATE_IDENTICAL
            else:
                continue
            out[i] = dataclasses.replace(out[i], issues=(*out[i].issues, issue))
    return out


def quality_report(parsed: format_v1.ParsedSnapshot, quotes: list[OptionQuote]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    for q in quotes:
        for i in q.issues:
            (flags if i in FLAG_ONLY else reasons)[i.value] += 1
    accepted = [q for q in quotes if q.accepted]
    by_expiry: Counter[str] = Counter(
        q.expiry.isoformat() for q in accepted if q.expiry is not None
    )
    return {
        "total_quotes": len(quotes),
        "accepted": len(accepted),
        "quarantined": len(quotes) - len(accepted),
        "quarantine_reasons": dict(sorted(reasons.items())),
        "flags_on_kept_or_quarantined": dict(sorted(flags.items())),
        "accepted_by_expiry": dict(sorted(by_expiry.items())),
        "exercise_styles_accepted": dict(
            Counter(
                str(q.exercise_style.value if q.exercise_style else "unknown") for q in accepted
            )
        ),
        "freshness": {
            "spot_observed_at": parsed.spot_observed_at,
            "quotes_with_known_time": sum(q.quote_time is not None for q in quotes),
            "quotes_with_unknown_time": sum(q.quote_time is None for q in quotes),
            "note": "unknown quote times are flagged and never assumed fresh",
        },
        "note": "a quote may carry several reasons; counts are per reason",
    }


def quote_to_json(q: OptionQuote) -> dict[str, Any]:
    d: dict[str, Any] = to_jsonable(q)
    d["accepted"] = q.accepted
    return d


def quote_from_json(d: dict[str, Any]) -> OptionQuote:
    def dec(k: str) -> Decimal | None:
        return None if d.get(k) is None else Decimal(d[k])

    def ts(k: str) -> datetime | None:
        return None if d.get(k) is None else datetime.fromisoformat(d[k].replace("Z", "+00:00"))

    return OptionQuote(
        row=d["row"],
        contract_id=d.get("contract_id"),
        option_type=None if d.get("option_type") is None else OptionType(d["option_type"]),
        strike=dec("strike"),
        expiry=ts("expiry"),
        exercise_style=None
        if d.get("exercise_style") is None
        else ExerciseStyle(d["exercise_style"]),
        settlement_type=None
        if d.get("settlement_type") is None
        else SettlementType(d["settlement_type"]),
        settlement_lag_days=d.get("settlement_lag_days"),
        multiplier=dec("multiplier"),
        deliverable=d.get("deliverable"),
        bid=dec("bid"),
        ask=dec("ask"),
        bid_size=d.get("bid_size"),
        ask_size=d.get("ask_size"),
        quote_time=ts("quote_time"),
        issues=tuple(QuoteIssue(i) for i in d.get("issues", [])),
    )


@dataclass(frozen=True)
class StoredSnapshot:
    manifest: dict[str, Any]
    quotes: list[OptionQuote]

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def as_of(self) -> datetime:
        return datetime.fromisoformat(self.manifest["as_of"].replace("Z", "+00:00"))

    def market(self) -> MarketSnapshot:
        """Carry inputs as supplied in the file; absent carry is an explicit error."""
        u, c = self.manifest["underlying"], self.manifest["carry"]
        if c is None:
            raise DomainError(
                ErrorCode.INVALID_MARKET_INPUT,
                "snapshot has no carry inputs (rate/dividend yield); they are not assumed",
            )
        observed = u.get("spot_observed_at")
        return MarketSnapshot(
            spot=Decimal(u["spot"]),
            rate=Decimal(c["rate"]),
            dividend_yield=Decimal(c["dividend_yield"]),
            snapshot_id=self.snapshot_id,
            source=self.manifest["source"],
            observed_at=None
            if observed is None
            else datetime.fromisoformat(observed.replace("Z", "+00:00")),
        )


class SnapshotService:
    def __init__(self, data_dir: Path | None = None, policy: QualityPolicy | None = None) -> None:
        self.store = ObjectStore(data_dir or default_data_dir(), "snapshots")
        self.policy = policy or QualityPolicy()

    def ingest(self, raw: bytes) -> tuple[dict[str, Any], bool]:
        """Returns (manifest, created). Re-ingesting identical bytes is a no-op."""
        raw_sha = sha256_hex(raw)
        policy_json = canonical_json(self.policy)
        snapshot_id = "snap-" + sha256_hex(raw_sha + format_v1.PARSER_VERSION + policy_json)[:16]
        if self.store.exists(snapshot_id):
            return self.manifest(snapshot_id), False
        parsed = format_v1.parse(raw)
        quotes = assess(parsed, self.policy)
        normalized = json.dumps([quote_to_json(q) for q in quotes], indent=1).encode()
        manifest = {
            "snapshot_id": snapshot_id,
            "format": format_v1.FORMAT,
            "parser_version": format_v1.PARSER_VERSION,
            "quality_policy": to_jsonable(self.policy),
            "source": parsed.source,
            "synthetic": parsed.synthetic,
            "as_of": to_jsonable(parsed.as_of),
            "retrieved_at": to_jsonable(parsed.retrieved_at),
            "ingested_at": to_jsonable(datetime.now(UTC)),
            "underlying": {
                "id": parsed.underlying_id,
                "currency": parsed.currency,
                "spot": str(parsed.spot),
                "spot_observed_at": to_jsonable(parsed.spot_observed_at),
            },
            "carry": None
            if parsed.rate is None
            else {
                "rate": str(parsed.rate),
                "dividend_yield": str(parsed.dividend_yield),
                "source": parsed.carry_source,
            },
            "raw_sha256": raw_sha,
            "normalized_sha256": sha256_hex(normalized),
            "quality": to_jsonable(quality_report(parsed, quotes)),
            "synthetic_metadata": to_jsonable(parsed.extra),
        }
        files = {
            "raw.json": raw,
            "normalized.json": normalized,
            "manifest.json": json.dumps(manifest, indent=1).encode(),
        }
        created = self.store.put(snapshot_id, files)
        return self.manifest(snapshot_id), created

    def ingest_file(self, path: Path) -> tuple[dict[str, Any], bool]:
        return self.ingest(path.read_bytes())

    def manifest(self, snapshot_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.store.read_json(snapshot_id, "manifest.json")
        return data

    def load(self, snapshot_id: str) -> StoredSnapshot:
        manifest = self.manifest(snapshot_id)
        normalized = self.store.read_bytes(snapshot_id, "normalized.json")
        if sha256_hex(normalized) != manifest["normalized_sha256"]:
            raise DomainError(
                ErrorCode.NUMERICAL_FAILURE, f"{snapshot_id}: stored data hash mismatch"
            )
        return StoredSnapshot(manifest, [quote_from_json(d) for d in json.loads(normalized)])

    def list(self) -> list[dict[str, Any]]:
        out = []
        for sid in self.store.ids():
            m = self.manifest(sid)
            out.append(
                {k: m[k] for k in ("snapshot_id", "source", "synthetic", "as_of", "ingested_at")}
                | {
                    "underlying": m["underlying"]["id"],
                    "accepted": m["quality"]["accepted"],
                    "total_quotes": m["quality"]["total_quotes"],
                }
            )
        return sorted(out, key=lambda m: (m["as_of"], m["snapshot_id"]))
