"""CSV chain import (both layouts, timezones, failures) and the two-day study."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from options_engine.adapters.snapshots.csv_chain import ChainMapping, convert, convert_files
from options_engine.application.snapshots import SnapshotService
from options_engine.application.study import run_study
from options_engine.domain.errors import DomainError
from options_engine.validation.policy import load_policy

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "examples" / "csv"
FIXTURES = ROOT / "fixtures" / "snapshots"
AS_OF = datetime(2026, 9, 28, 20, tzinfo=UTC)


def _long() -> dict[str, Any]:
    return convert_files(
        CSV / "synthetic_chain_day1_long.csv",
        CSV / "mapping_long.toml",
        underlying_id="SYNTH-IDX",
        currency="USD",
        spot="4500.00",
        as_of=AS_OF,
        synthetic=True,
    )


def test_long_layout_round_trips_the_fixture_quotes():
    doc = _long()
    original = json.loads((FIXTURES / "synthetic_heston_day1.json").read_text())["quotes"]
    assert len(doc["quotes"]) == len(original)
    for got, want in zip(doc["quotes"], original, strict=True):
        for key in ("contract_id", "option_type", "strike", "bid", "ask", "bid_size", "ask_size"):
            assert got[key] == want[key], key
        assert got["expiry"] == want["expiry"]
        assert got["quote_time"] == want["quote_time"]
    assert doc["synthetic"] is True
    assert "sha256" in doc["source"]


def test_straddle_layout_and_new_york_expiry_across_dst():
    doc = convert_files(
        CSV / "synthetic_chain_day2_straddle.csv",
        CSV / "mapping_straddle.toml",
        underlying_id="SYNTH-IDX",
        currency="USD",
        spot="4455",
        as_of=AS_OF,
        synthetic=True,
    )
    expiries = {q["expiry"] for q in doc["quotes"]}
    assert "2026-10-29T20:00:00Z" in expiries  # 16:00 EDT
    assert "2026-11-29T21:00:00Z" in expiries  # 16:00 EST: DST ended on 1 November
    assert {q["option_type"] for q in doc["quotes"]} == {"call", "put"}


def test_import_marks_unparseable_values_for_quarantine(tmp_path):
    csv = tmp_path / "c.csv"
    csv.write_text(
        "Expiry,Strike,Call Bid,Call Ask,Put Bid,Put Ask\n"
        "12/18/2026,4500,10.0,10.5,9.0,9.4\n"
        "not-a-date,4500,1,2,1,2\n"
        "12/18/2026,abc,1,2,1,2\n"
    )
    mapping = ChainMapping.load(CSV / "mapping_straddle.toml")[0]
    doc = convert(
        csv.read_bytes(), mapping, underlying_id="X", currency="USD", spot="4500", as_of=AS_OF
    )
    assert doc["synthetic"] is False
    svc = SnapshotService(tmp_path / "data")
    m, _ = svc.ingest(json.dumps(doc).encode())
    assert m["quality"]["accepted"] == 2
    assert m["quality"]["quarantine_reasons"]["unparseable_field"] == 4


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"mapping": {"layout": "wide"}}, "layout"),
        ({"contract": {}}, "exercise_style"),
        ({"formats": {"timezone": "Mars/Olympus"}}, "timezone"),
    ],
)
def test_mapping_errors(patch, message):
    import tomllib

    doc = tomllib.loads((CSV / "mapping_straddle.toml").read_text())
    for section, values in patch.items():
        doc[section] = {**doc.get(section, {}), **values} if values else values
    with pytest.raises(DomainError, match=message):
        ChainMapping(doc)


def test_missing_columns_are_named(tmp_path):
    csv = tmp_path / "c.csv"
    csv.write_text("Expiry,Strike\n12/18/2026,4500\n")
    mapping = ChainMapping.load(CSV / "mapping_straddle.toml")[0]
    with pytest.raises(DomainError, match="Call Bid"):
        convert(csv.read_bytes(), mapping, underlying_id="X", currency="USD", spot="1", as_of=AS_OF)


def test_two_day_study_reports_only_predeclared_metrics(tmp_path):
    svc = SnapshotService(tmp_path)
    d1 = svc.ingest(json.dumps(_long()).encode())[0]["snapshot_id"]
    d2 = svc.ingest_file(FIXTURES / "synthetic_heston_day2.json")[0]["snapshot_id"]
    pred = load_policy()["real_data_study"]
    pillars = tuple(load_policy()["heston_term_structure"]["pillars_days"])
    report = run_study(tmp_path, d1, d2, tmp_path / "study", pred, pillars)
    for model in pred["models"]:
        assert set(report["metrics"][model]) == set(pred["metrics"])
    assert report["metrics"]["heston"]["held_out_bid_ask_containment"] >= 0.9
    assert report["metrics"]["svi_slices"]["held_out_bid_ask_containment"] >= 0.9
    assert report["metrics"]["heston_ts"]["day2_bid_ask_containment_heston_v0_refit"] >= 0.9
    assert (tmp_path / "study" / "report.md").read_text().startswith("# Two-day model study")
    assert len(report["plots"]) == 5 and all(
        (tmp_path / "study" / p).exists() for p in report["plots"]
    )


def test_mapping_filters_keep_only_listed_values(tmp_path):
    import tomllib

    csv = tmp_path / "c.csv"
    csv.write_text(
        "Root,Expiry,Strike,Call Bid,Call Ask,Put Bid,Put Ask\n"
        "SPXW,12/18/2026,4500,10.0,10.5,9.0,9.4\n"
        "SPX,12/18/2026,4500,10.0,10.5,9.0,9.4\n"
    )
    doc = tomllib.loads((CSV / "mapping_straddle.toml").read_text())
    doc["filters"] = {"Root": ["SPXW"]}
    out = convert(
        csv.read_bytes(),
        ChainMapping(doc),
        underlying_id="SPX",
        currency="USD",
        spot="4500",
        as_of=AS_OF,
    )
    assert len(out["quotes"]) == 2  # one straddle row -> call and put
    assert "1 rows excluded by mapping filters" in out["source"]
    doc["filters"] = {"Missing": ["x"]}
    with pytest.raises(DomainError, match="Missing"):
        convert(
            csv.read_bytes(),
            ChainMapping(doc),
            underlying_id="SPX",
            currency="USD",
            spot="4500",
            as_of=AS_OF,
        )
