"""Append-only, hash-chained ledger files for paper-trading accounts.

Each account is one JSON-lines file. Every event carries the hash of the
previous event, so any edit or deletion of an earlier line is detectable by
``verify``. Appends take an exclusive file lock, so concurrent writers (HTTP
workers, CLI) cannot interleave or fork the chain.
"""

from __future__ import annotations

import fcntl
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from options_engine.application.hashing import canonical_json, sha256_hex, to_jsonable
from options_engine.domain.errors import DomainError, ErrorCode

ACCOUNT_ID = re.compile(r"^acct-[0-9a-f]{12}$")
GENESIS = "0" * 64


def event_hash(event: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({k: v for k, v in event.items() if k != "hash"}))


class LedgerStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "paper"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, account_id: str) -> Path:
        if not ACCOUNT_ID.match(account_id):
            raise DomainError(ErrorCode.INVALID_REQUEST, f"malformed account id '{account_id}'")
        return self.root / f"{account_id}.jsonl"

    def new_account_id(self) -> str:
        return "acct-" + secrets.token_hex(6)

    def exists(self, account_id: str) -> bool:
        return self._path(account_id).is_file()

    def append(
        self,
        account_id: str,
        kind: str,
        timestamp: datetime,
        payload: Any,
        *,
        create: bool = False,
        id_field: str | None = None,
    ) -> dict[str, Any]:
        """Append one event. ``id_field``, if given, is set in the payload to an id derived
        from the event's sequence number while the lock is held, so ids are unique even
        with concurrent writers."""
        path = self._path(account_id)
        if create and path.exists():
            raise DomainError(ErrorCode.INVALID_REQUEST, f"account {account_id} already exists")
        if not create and not path.exists():
            raise DomainError(ErrorCode.NOT_FOUND, f"paper account {account_id} not found")
        with path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                lines = [line for line in fh.read().splitlines() if line.strip()]
                prev = json.loads(lines[-1]) if lines else None
                seq = 0 if prev is None else prev["seq"] + 1
                body = to_jsonable(payload)
                if id_field is not None:
                    body[id_field] = f"t{seq:05d}"
                event: dict[str, Any] = {
                    "seq": seq,
                    "type": kind,
                    "timestamp": to_jsonable(timestamp),
                    "recorded_at": to_jsonable(datetime.now(UTC)),
                    "payload": body,
                    "prev_hash": GENESIS if prev is None else prev["hash"],
                }
                event["hash"] = event_hash(event)
                fh.seek(0, 2)
                fh.write(canonical_json(event) + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return event

    def events(self, account_id: str) -> list[dict[str, Any]]:
        path = self._path(account_id)
        if not path.is_file():
            raise DomainError(ErrorCode.NOT_FOUND, f"paper account {account_id} not found")
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def verify(self, account_id: str) -> dict[str, Any]:
        """Recompute the chain. Reports the first broken event, if any."""
        prev = GENESIS
        events = self.events(account_id)
        for i, e in enumerate(events):
            if e.get("seq") != i or e.get("prev_hash") != prev or event_hash(e) != e.get("hash"):
                return {"intact": False, "events": len(events), "first_bad_seq": i}
            prev = e["hash"]
        return {"intact": True, "events": len(events), "first_bad_seq": None, "head_hash": prev}

    def account_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("acct-*.jsonl") if ACCOUNT_ID.match(p.stem))
