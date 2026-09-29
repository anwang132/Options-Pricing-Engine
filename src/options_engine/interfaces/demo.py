"""Offline demonstration: successful calculations and meaningful failures.

All inputs are synthetic and labelled as such. No network access is used.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from options_engine.adapters.environment import PROJECT_ROOT
from options_engine.application.surface import SurfaceService
from options_engine.domain.errors import DomainError
from options_engine.interfaces import handlers
from options_engine.interfaces.schemas import (
    CompareRequestIn,
    ImpliedVolIn,
    PriceRequestIn,
)

AS_OF = "2026-09-28T20:00:00Z"
HALF_YEAR = "2027-03-30T08:00:00Z"  # exactly 182.5 days = 0.5 ACT/365F years


def _contract(**kw: Any) -> dict[str, Any]:
    base = {
        "underlying": "SYNTH",
        "currency": "USD",
        "strike": "40",
        "option_type": "call",
        "exercise_style": "european",
        "expiry": HALF_YEAR,
        "multiplier": "100",
    }
    base.update(kw)
    return base


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def run_demo() -> int:
    market = {"spot": "42", "rate": "0.10", "source": "synthetic"}
    instrument = {
        "contract": _contract(),
        "valuation": {"as_of": AS_OF},
        "market": market,
        "model": {"volatility": 0.2},
    }

    _section("1. Analytic BSM price (Hull's textbook inputs; published value 4.76)")
    r = handlers.price(PriceRequestIn.model_validate(instrument))
    print(f"price {r.price:.10f} {r.currency}/unit; delta {r.greeks[0].value:.6f}")

    _section("2. Cross-engine comparison with uncertainty context")
    cmp = handlers.compare(CompareRequestIn.model_validate(instrument))
    for c in cmp.comparisons:
        if c.result:
            print(f"{c.engine_id:<16} {c.result.price:.8f}  {c.interpretation or 'reference'}")

    _section("3. Implied volatility from a bid/ask pair")
    iv = handlers.implied_vol(
        ImpliedVolIn.model_validate(
            {
                **{k: instrument[k] for k in ("contract", "valuation", "market")},
                "bid": "4.70",
                "ask": "4.80",
            }
        )
    )
    for q in iv.results:
        print(f"{q.label:<4} {q.quote:.2f} -> {q.status.value}: IV={q.implied_vol}")

    _section("4. Meaningful failure: quote below the no-arbitrage lower bound")
    bad = handlers.implied_vol(
        ImpliedVolIn.model_validate(
            {**{k: instrument[k] for k in ("contract", "valuation", "market")}, "quote": "3.50"}
        )
    )
    q = bad.results[0]
    print(f"quote 3.50 vs lower bound {q.lower_bound:.4f}: {q.status.value}\n  {q.explanation}")

    _section("5. Meaningful failure: low-vega instability (deep OTM, short-dated)")
    unstable = handlers.implied_vol(
        ImpliedVolIn.model_validate(
            {
                "contract": _contract(strike="60", expiry="2026-10-28T20:00:00Z"),
                "valuation": {"as_of": AS_OF},
                "market": market,
                "quote": "0.01",
            }
        )
    )
    q = unstable.results[0]
    print(
        f"IV={q.implied_vol:.4f} status={q.status.value}; vega={q.vega:.3g}; "
        f"quote ±{q.price_resolution} implies IV in {q.iv_interval}"
    )

    _section("6. Meaningful failure: unsupported engine/exercise combination")
    american = {**instrument, "contract": _contract(exercise_style="american")}
    try:
        handlers.price(
            PriceRequestIn.model_validate({**american, "engine": {"engine": "mc_terminal_gbm"}})
        )
    except DomainError as exc:
        supported = exc.details.get("engines_supporting_request")
        print(f"{exc.code.value}: {exc.message}\n  engines supporting this request: {supported}")

    _section("7. Meaningful failure: invalid tree probability is rejected, not clipped")
    try:
        handlers.price(
            PriceRequestIn.model_validate(
                {
                    **instrument,
                    "model": {"volatility": 0.01},
                    "engine": {"engine": "crr_tree", "steps": 10},
                }
            )
        )
    except DomainError as exc:
        print(f"{exc.code.value}: {exc.message}")
    _release_b()
    return 0


def _release_b() -> None:
    """Snapshots -> quality report -> surface fit -> surface-driven scenarios (temp store)."""
    with tempfile.TemporaryDirectory() as tmp:
        svc = SurfaceService(Path(tmp))
        root = PROJECT_ROOT / "fixtures" / "snapshots"
        m1, _ = svc.snapshots.ingest_file(root / "synthetic_day1.json")
        m2, _ = svc.snapshots.ingest_file(root / "synthetic_day2.json")
        _section("8. Snapshot ingestion (synthetic file with injected defects)")
        q = m1["quality"]
        print(
            f"{m1['snapshot_id']}: {q['accepted']}/{q['total_quotes']} accepted; quarantined "
            f"by reason: {q['quarantine_reasons']}; flags: {q['flags_on_kept_or_quarantined']}"
        )
        _section("9. SSVI surface fit with held-out strikes and a later snapshot")
        art, _ = svc.fit(m1["snapshot_id"], m2["snapshot_id"])
        s = art["surface"]
        print(
            f"rho={s['rho']:.4f} eta={s['eta']:.4f} gamma={s['gamma']:.4f}; "
            f"{art['arbitrage_diagnostics']['statement']}"
        )
        for part in ("in_sample", "held_out", "later_snapshot"):
            st = art[part]
            print(
                f"  {part:<15} n={st['n']:>3} price RMSE {st['price_rmse']:.3f}  inside bid/ask "
                f"{st['bid_ask_containment']:.0%}"
            )
        n_american = art["exclusions"].get("american_exercise_not_european_observation", 0)
        iv_err = art["truth_recovery"]["max_abs_iv_error"]
        print(
            f"  American quotes excluded: {n_american}; "
            f"max IV error vs generating surface {iv_err:.2e}"
        )
        _section("10. Meaningful failure: fit with too little data is recorded, not hidden")
        doc = json.loads((root / "synthetic_day2.json").read_bytes())
        first = doc["quotes"][0]["expiry"]
        doc["quotes"] = [x for x in doc["quotes"] if x["expiry"] == first]
        bad_id = svc.snapshots.ingest(json.dumps(doc).encode())[0]["snapshot_id"]
        bad, _ = svc.fit(bad_id)
        print(
            f"{bad['fit_id']}: status={bad['status']} reason={bad['failure_reason']} "
            f"({bad['failure_detail']})"
        )
