from __future__ import annotations

import json
from pathlib import Path

from options_engine.interfaces.cli import main

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_price_table(capsys):
    assert main(["price", "--input", str(EXAMPLES / "price_hull_call.json")]) == 0
    assert "4.7594" in capsys.readouterr().out


def test_price_flags_require_explicit_times(capsys):
    code = main(
        [
            "price",
            "--type",
            "call",
            "--spot",
            "42",
            "--strike",
            "40",
            "--rate",
            "0.1",
            "--vol",
            "0.2",
        ]
    )
    assert code == 2
    assert "invalid_request" in capsys.readouterr().err


def test_manifest_and_replay(tmp_path, capsys):
    m = tmp_path / "m.json"
    args = [
        "price",
        "--as-of",
        "2026-09-28T20:00:00Z",
        "--expiry",
        "2027-03-30T08:00:00Z",
        "--type",
        "put",
        "--spot",
        "42",
        "--strike",
        "40",
        "--rate",
        "0.1",
        "--vol",
        "0.2",
        "--engine",
        "crr_tree",
        "--steps",
        "300",
        "--manifest",
        str(m),
        "--format",
        "json",
    ]
    assert main(args) == 0
    assert json.loads(m.read_text())["result"]["engine_id"] == "crr_tree"
    capsys.readouterr()
    assert main(["replay", str(m)]) == 0
    assert '"bitwise_identical": true' in capsys.readouterr().out


def test_domain_error_exit_code(capsys):
    args = [
        "price",
        "--as-of",
        "2026-09-28T20:00:00Z",
        "--expiry",
        "2027-03-30T08:00:00Z",
        "--type",
        "call",
        "--spot",
        "42",
        "--strike",
        "40",
        "--rate",
        "0.1",
        "--vol",
        "0.01",
        "--engine",
        "crr_tree",
        "--steps",
        "10",
    ]
    assert main(args) == 2
    assert "invalid_tree_probability" in capsys.readouterr().err


def test_iv_and_compare(capsys):
    assert main(["iv", "--input", str(EXAMPLES / "iv_quotes.json")]) == 0
    assert main(["compare", "--input", str(EXAMPLES / "compare_otm_put.json")]) == 0
    out = capsys.readouterr().out
    assert "implied_vol" in out and "reference_engine" in out


def test_demo_runs_offline(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "below_lower_bound" in out and "unstable_low_vega" in out
    assert "invalid_tree_probability" in out
