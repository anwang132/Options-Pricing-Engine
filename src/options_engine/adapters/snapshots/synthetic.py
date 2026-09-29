"""Synthetic, clearly labelled snapshot generator (no market data involved).

Prices a European, cash-settled index-style chain from a known SSVI surface
under flat carry, adds bid/ask spreads, tick rounding and seeded noise, then
injects named defects so that ingestion and calibration failure paths can be
exercised offline. The generating parameters are embedded in the file under
``synthetic_truth`` so calibration can be checked for parameter recovery.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import numpy as np

from options_engine.domain.conventions import year_fraction
from options_engine.engines.bsm_analytic import black_scholes_price
from options_engine.models.ssvi import SSVISurface

TICK = Decimal("0.05")
EXPIRY_DAYS = (30, 61, 91, 182, 364)
MONEYNESS = tuple(round(0.70 + 0.025 * i, 3) for i in range(25))  # 70% .. 130%


def _round(x: float, mode: str) -> Decimal:
    d = Decimal(repr(max(x, 0.0))) / TICK
    return (d.to_integral_value(rounding=ROUND_FLOOR if mode == "down" else ROUND_CEILING)) * TICK


def generate(
    as_of: datetime,
    spot: float,
    rate: float,
    dividend_yield: float,
    surface: SSVISurface,
    seed: int,
    inject_defects: bool = True,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    quotes: list[dict[str, Any]] = []
    expiry_base = as_of.replace(hour=20, minute=0, second=0, microsecond=0)
    for days in EXPIRY_DAYS:
        expiry = expiry_base + timedelta(days=days)
        T = year_fraction(as_of, expiry)
        F = spot * math.exp((rate - dividend_yield) * T)
        for m in MONEYNESS:
            K = round(spot * m / 5) * 5  # strikes on a 5-point grid
            vol = float(surface.implied_vol(math.log(K / F), T))
            for opt, sign in (("call", 1), ("put", -1)):
                mid = float(black_scholes_price(sign, spot, K, T, rate, dividend_yield, vol)[0])
                half = max(0.10, 0.015 * mid)
                noisy = mid + rng.uniform(-0.25, 0.25) * half
                bid, ask = _round(noisy - half, "down"), _round(noisy + half, "up")
                qt = as_of - timedelta(seconds=float(rng.uniform(0, 30)))
                quotes.append(
                    {
                        "contract_id": f"SYNTH-{expiry:%Y%m%d}-{opt[0].upper()}{K}",
                        "option_type": opt,
                        "strike": str(K),
                        "expiry": expiry.isoformat().replace("+00:00", "Z"),
                        "exercise_style": "european",
                        "settlement_type": "cash",
                        "settlement_lag_days": 0,
                        "multiplier": "100",
                        "deliverable": "standard",
                        "bid": str(bid),
                        "ask": str(ask),
                        "bid_size": int(rng.integers(1, 200)),
                        "ask_size": int(rng.integers(1, 200)),
                        "quote_time": qt.isoformat().replace("+00:00", "Z"),
                    }
                )
    defects: list[dict[str, Any]] = []
    if inject_defects:
        defects = _inject(quotes, as_of, rng)
    return {
        "format": "options-snapshot/v1",
        "source": "synthetic: SSVI generator (options_engine.adapters.snapshots.synthetic)",
        "synthetic": True,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "retrieved_at": None,
        "underlying": {
            "id": "SYNTH-IDX",
            "currency": "USD",
            "spot": f"{spot:.2f}",
            "spot_observed_at": (as_of - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        },
        "carry": {
            "rate": repr(rate),
            "dividend_yield": repr(dividend_yield),
            "source": "synthetic: generator inputs",
        },
        "quotes": quotes,
        "synthetic_truth": {"surface": surface.to_dict(), "seed": seed},
        "synthetic_defects": defects,
    }


def _inject(
    quotes: list[dict[str, Any]], as_of: datetime, rng: np.random.Generator
) -> list[dict[str, Any]]:
    """Mutate a few rows (and append some) with labelled defects."""
    defects: list[dict[str, Any]] = []

    def pick() -> int:
        return int(rng.integers(0, len(quotes)))

    def mark(i: int, kind: str) -> None:
        defects.append({"row": i, "defect": kind, "contract_id": quotes[i]["contract_id"]})

    i = pick()
    quotes[i]["bid"], quotes[i]["ask"] = quotes[i]["ask"], quotes[i]["bid"]
    if quotes[i]["bid"] != quotes[i]["ask"]:
        mark(i, "crossed_quote")
    i = pick()
    quotes[i]["ask"] = None
    mark(i, "missing_ask")
    i = pick()
    quotes[i]["bid"] = "nan"
    mark(i, "nonfinite_bid")
    i = pick()
    quotes[i]["quote_time"] = (as_of - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    mark(i, "stale_quote")
    for _ in range(3):
        i = pick()
        quotes[i]["quote_time"] = None
        mark(i, "unknown_quote_time")
    i = pick()
    quotes[i]["bid_size"] = None
    mark(i, "missing_size")
    i = pick()
    quotes[i]["ask"] = str(Decimal(quotes[i]["ask"]) * 3 + 5)
    mark(i, "wide_spread")
    i = pick()
    quotes[i]["strike"] = "abc"
    mark(i, "unparseable_strike")
    # Appended rows
    base = dict(quotes[len(quotes) // 2])
    extra = [
        ({**base, "bid": str(Decimal(base["bid"]) + 1)}, "duplicate_conflicting"),
        (
            {**base, "exercise_style": "american", "contract_id": base["contract_id"] + "-AM"},
            "american_exercise",
        ),
        (
            {
                **base,
                "deliverable": "adjusted: 100 units + cash",
                "contract_id": base["contract_id"] + "-ADJ",
            },
            "adjusted_deliverable",
        ),
        (
            {
                **base,
                "expiry": (as_of - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "contract_id": "SYNTH-EXPIRED",
            },
            "expired_contract",
        ),
        ({**base, "bid": "-0.05", "contract_id": "SYNTH-NEG"}, "negative_bid"),
    ]
    for row, kind in extra:
        quotes.append(row)
        mark(len(quotes) - 1, kind)
    return defects


def default_surfaces() -> tuple[SSVISurface, SSVISurface]:
    """Day-1 truth and a day-2 surface with slightly lower ATM variance."""
    T = tuple(d / 365.0 for d in EXPIRY_DAYS)
    atm = (0.19, 0.195, 0.20, 0.205, 0.21)
    day1 = SSVISurface(-0.65, 1.1, 0.4, T, tuple(v * v * t for v, t in zip(atm, T, strict=True)))
    day2 = SSVISurface(
        -0.65, 1.1, 0.4, T, tuple((v - 0.01) ** 2 * t for v, t in zip(atm, T, strict=True))
    )
    return day1, day2


def write_default_fixtures(directory: str | Path) -> list[str]:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    day1, day2 = default_surfaces()
    t0 = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    docs = {
        "synthetic_day1.json": generate(t0, 4500.0, 0.04, 0.015, day1, seed=1),
        "synthetic_day2.json": generate(t0 + timedelta(days=1), 4545.0, 0.04, 0.015, day2, seed=2),
        "synthetic_clean_day1.json": generate(
            t0, 4500.0, 0.04, 0.015, day1, seed=1, inject_defects=False
        ),
    }
    for name, doc in docs.items():
        (out / name).write_text(json.dumps(doc, indent=1) + "\n")
    return list(docs)
