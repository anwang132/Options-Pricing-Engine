from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from options_engine.application.snapshots import SnapshotService
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.quotes import QuoteIssue

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "snapshots"

EXPECTED = {
    "crossed_quote": QuoteIssue.CROSSED,
    "missing_ask": QuoteIssue.MISSING_BID_OR_ASK,
    "nonfinite_bid": QuoteIssue.NONFINITE,
    "stale_quote": QuoteIssue.STALE,
    "unknown_quote_time": QuoteIssue.QUOTE_TIME_UNKNOWN,
    "missing_size": QuoteIssue.MISSING_SIZE,
    "wide_spread": QuoteIssue.WIDE_SPREAD,
    "unparseable_strike": QuoteIssue.UNPARSEABLE,
    "duplicate_conflicting": QuoteIssue.DUPLICATE_CONFLICT,
    "adjusted_deliverable": QuoteIssue.UNSUPPORTED_DELIVERABLE,
    "expired_contract": QuoteIssue.EXPIRED,
    "negative_bid": QuoteIssue.NEGATIVE_PRICE,
}


@pytest.fixture
def svc(tmp_path) -> SnapshotService:
    return SnapshotService(tmp_path)


def raw(name: str = "synthetic_day1.json") -> bytes:
    return (FIXTURES / name).read_bytes()


def test_every_injected_defect_has_its_specific_reason(svc):
    manifest, created = svc.ingest(raw())
    assert created
    snap = svc.load(manifest["snapshot_id"])
    doc = json.loads(raw())
    for d in doc["synthetic_defects"]:
        q = snap.quotes[d["row"]]
        if d["defect"] == "american_exercise":
            assert q.accepted  # ingestion keeps it; calibration eligibility excludes it
            continue
        assert EXPECTED[d["defect"]] in q.issues, (d, q.issues)


def test_flags_do_not_quarantine_and_unknown_time_stays_unknown(svc):
    snap = svc.load(svc.ingest(raw())[0]["snapshot_id"])
    unknown = [q for q in snap.quotes if QuoteIssue.QUOTE_TIME_UNKNOWN in q.issues]
    assert unknown
    assert all(q.quote_time is None for q in unknown)
    assert any(q.accepted for q in unknown)


def test_counts_account_for_every_quote(svc):
    m = svc.ingest(raw())[0]
    q = m["quality"]
    assert q["accepted"] + q["quarantined"] == q["total_quotes"] == len(json.loads(raw())["quotes"])
    assert q["quarantine_reasons"]["zero_bid"] > 0  # natural deep-OTM zero bids


def test_clean_fixture_has_no_injected_defects(svc):
    q = svc.ingest(raw("synthetic_clean_day1.json"))[0]["quality"]
    assert set(q["quarantine_reasons"]) <= {"zero_bid", "wide_spread"}


def test_idempotent_and_immutable(svc):
    m1, created1 = svc.ingest(raw())
    m2, created2 = svc.ingest(raw())
    assert created1 and not created2
    assert m1 == m2
    sid = m1["snapshot_id"]
    assert not svc.store.put(sid, {"manifest.json": b"{}"})
    path = svc.store.root / sid / "normalized.json"
    assert not os.access(path, os.W_OK)


def test_tampering_detected(svc):
    sid = svc.ingest(raw())[0]["snapshot_id"]
    path = svc.store.root / sid / "normalized.json"
    os.chmod(path, 0o644)
    path.write_bytes(path.read_bytes().replace(b'"row": 0', b'"row": 99', 1))
    with pytest.raises(DomainError, match="hash mismatch"):
        svc.load(sid)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d.pop("synthetic"), "synthetic"),
        (lambda d: d.update(format="v0"), "format"),
        (lambda d: d["underlying"].update(spot="-1"), "spot"),
        (lambda d: d.update(as_of="2026-09-28T20:00:00"), "timezone"),
        (lambda d: d.update(quotes={}), "quotes"),
    ],
)
def test_file_level_errors_reject_the_file(svc, mutate, fragment):
    doc = json.loads(raw())
    mutate(doc)
    with pytest.raises(DomainError) as e:
        svc.ingest(json.dumps(doc).encode())
    assert e.value.code is ErrorCode.INVALID_REQUEST
    assert fragment in e.value.message


def test_invalid_json_rejected(svc):
    with pytest.raises(DomainError, match="not valid JSON"):
        svc.ingest(b"{nope")


def test_missing_carry_is_explicit(svc):
    doc = json.loads(raw())
    doc["carry"] = None
    snap = svc.load(svc.ingest(json.dumps(doc).encode())[0]["snapshot_id"])
    with pytest.raises(DomainError, match="not assumed"):
        snap.market()


def test_malformed_ids_rejected(svc):
    with pytest.raises(DomainError):
        svc.manifest("../../etc/passwd")
    with pytest.raises(DomainError) as e:
        svc.manifest("snap-0000000000000000")
    assert e.value.code is ErrorCode.NOT_FOUND


def test_api_snapshot_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from options_engine.interfaces.http.app import create_app

    monkeypatch.setenv("OPTIONS_ENGINE_DATA_DIR", str(tmp_path))
    with TestClient(create_app(workers=0)) as c:
        r = c.post("/api/v1/snapshots", content=raw(), headers={"content-type": "application/json"})
        assert r.status_code == 201, r.text
        sid = r.json()["manifest"]["snapshot_id"]
        assert c.post("/api/v1/snapshots", content=raw()).status_code == 200  # already present
        assert [s["snapshot_id"] for s in c.get("/api/v1/snapshots").json()] == [sid]
        quotes = c.get(f"/api/v1/snapshots/{sid}/quotes").json()
        assert any(not q["accepted"] for q in quotes)
        assert c.get("/api/v1/snapshots/snap-0000000000000000").status_code == 404
        bad = c.post("/api/v1/snapshots", content=b"[]")
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "invalid_request"
        bundled = c.post("/api/v1/snapshots/bundled").json()
        assert len(bundled) == 4
