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


def test_price_heston_flags(capsys):
    args = ["price", "--as-of", "2026-09-29T20:00:00Z", "--expiry", "2027-09-29T20:00:00Z"]
    args += ["--type", "put", "--spot", "100", "--strike", "90", "--rate", "0.03"]
    args += ["--heston", "0.04,1.5,0.04,0.3,-0.7"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "heston_fourier" in out
    assert "not_supported" in out  # vega


def test_price_lsm_flags(capsys):
    args = ["price", "--as-of", "2026-09-29T20:00:00Z", "--expiry", "2027-09-29T20:00:00Z"]
    args += ["--type", "put", "--exercise", "american", "--spot", "100", "--strike", "100"]
    args += ["--rate", "0.05", "--vol", "0.2", "--engine", "lsm_american", "--paths", "20000"]
    assert main(args) == 0
    assert "sampling      SE" in capsys.readouterr().out


def _snapshot_id(out: str) -> str:
    return out.split()[0]


def test_snapshot_import_surface_heston_and_study(tmp_path, capsys):
    data = str(tmp_path)
    csv_dir = EXAMPLES / "csv"
    common = ["--underlying", "SYNTH-IDX", "--synthetic", "--ingest", "--data-dir", data]
    assert (
        main(
            [
                "snapshot",
                "import-csv",
                str(csv_dir / "synthetic_chain_day1_long.csv"),
                "--mapping",
                str(csv_dir / "mapping_long.toml"),
                "--spot",
                "4500",
                "--as-of",
                "2026-09-28T20:00:00Z",
                *common,
            ]
        )
        == 0
    )
    day1 = _snapshot_id(capsys.readouterr().out)
    assert (
        main(
            [
                "snapshot",
                "import-csv",
                str(csv_dir / "synthetic_chain_day2_straddle.csv"),
                "--mapping",
                str(csv_dir / "mapping_straddle.toml"),
                "--spot",
                "4455",
                "--as-of",
                "2026-09-29T20:00:00Z",
                *common,
            ]
        )
        == 0
    )
    day2 = _snapshot_id(capsys.readouterr().out)
    assert main(["snapshot", "list", "--data-dir", data]) == 0
    assert day1 in capsys.readouterr().out

    assert main(["surface", "fit", day1, "--later", day2, "--data-dir", data]) == 0
    assert "status ok" in capsys.readouterr().out
    assert main(["heston", "calibrate", day1, "--later", day2, "--data-dir", data]) == 0
    out = capsys.readouterr().out
    assert "status ok" in out and "v0 refit" in out
    assert main(["surface", "fit", day1, "--model", "svi-slices", "--data-dir", data]) == 0
    assert "slices fitted" in capsys.readouterr().out
    assert main(["heston", "calibrate", day1, "--pillars", "7,30,91,182", "--data-dir", data]) == 0
    assert "theta_5" in capsys.readouterr().out
    assert main(["study", day1, day2, "--data-dir", data]) == 0
    assert "held_out_bid_ask_containment" in capsys.readouterr().out
    assert (tmp_path / "studies" / f"{day1}__{day2}" / "report.md").exists()


def test_import_csv_requires_snapshot_facts(tmp_path, capsys):
    code = main(
        [
            "snapshot",
            "import-csv",
            str(EXAMPLES / "csv" / "synthetic_chain_day1_long.csv"),
            "--mapping",
            str(EXAMPLES / "csv" / "mapping_long.toml"),
            "--underlying",
            "X",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "--spot and --as-of" in capsys.readouterr().err


def test_hedge_command(capsys):
    assert main(["hedge", "--paths", "1000", "--rebalances", "8,32"]) == 0
    out = capsys.readouterr().out
    assert "world gbm" in out and "theory std" in out
    assert (
        main(
            [
                "hedge",
                "--paths",
                "500",
                "--rebalances",
                "8",
                "--heston",
                "0.04,1.5,0.04,0.3,-0.7",
                "--strategies",
                "bsm,heston_mv",
            ]
        )
        == 0
    )
    assert "heston_mv" in capsys.readouterr().out
