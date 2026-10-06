"""Write the synthetic example option-chain CSVs in examples/csv/ (from the Heston fixtures).

uv run python scripts/make_example_csv.py

Both files are synthetic and labelled as such in their names; they show the two layouts
the CSV importer understands (docs/real-data.md).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "snapshots"
OUT = ROOT / "examples" / "csv"


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def long_layout(doc: dict[str, Any], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "Symbol",
                "Expiration",
                "Strike",
                "Type",
                "Bid",
                "Ask",
                "Bid Size",
                "Ask Size",
                "Quote Time",
            ]
        )
        for q in doc["quotes"]:
            w.writerow(
                [
                    q["contract_id"],
                    q["expiry"],
                    q["strike"],
                    {"call": "C", "put": "P"}.get(q["option_type"], q["option_type"]),
                    q["bid"] if q["bid"] is not None else "",
                    q["ask"] if q["ask"] is not None else "",
                    q["bid_size"] if q["bid_size"] is not None else "",
                    q["ask_size"] if q["ask_size"] is not None else "",
                    (q["quote_time"] or "").replace("Z", ""),
                ]
            )


def straddle_layout(doc: dict[str, Any], path: Path) -> None:
    ny = ZoneInfo("America/New_York")
    rows: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for q in doc["quotes"]:
        if q["exercise_style"] != "european" or q["deliverable"] != "standard":
            continue  # the straddle export carries standard contracts only
        rows.setdefault((q["expiry"], q["strike"]), {})[q["option_type"]] = q
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Expiry", "Strike", "Call Bid", "Call Ask", "Put Bid", "Put Ask"])
        for (expiry, strike), side in sorted(rows.items()):
            c, p = side.get("call", {}), side.get("put", {})
            w.writerow(
                [
                    _utc(expiry).astimezone(ny).strftime("%m/%d/%Y"),
                    strike,
                    c.get("bid") or "",
                    c.get("ask") or "",
                    p.get("bid") or "",
                    p.get("ask") or "",
                ]
            )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    day1 = json.loads((FIXTURES / "synthetic_heston_day1.json").read_text())
    day2 = json.loads((FIXTURES / "synthetic_heston_day2.json").read_text())
    long_layout(day1, OUT / "synthetic_chain_day1_long.csv")
    straddle_layout(day2, OUT / "synthetic_chain_day2_straddle.csv")
    print("wrote", sorted(p.name for p in OUT.glob("*.csv")))


if __name__ == "__main__":
    main()
