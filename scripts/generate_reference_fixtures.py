"""Generate independent reference fixtures (requires the `reference` dependency group).

    uv run --group reference python scripts/generate_reference_fixtures.py

Sources, all independent of the engine code under test:
* mpmath (50+ digit arithmetic): a separate BSM price implementation written
  here from the textbook formula; Greeks are high-precision numerical
  derivatives of that price (mpmath.diff), not the analytic Greek formulas.
* QuantLib (pinned version): AnalyticEuropeanEngine, AnalyticDividendEuropeanEngine,
  FdBlackScholesVanillaEngine (American, incl. escrowed cash dividends).
* Published textbook values (rounded as printed).

Fixtures record library versions, this script's SHA-256 and the policy hash.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mpmath as mp
import QuantLib as ql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from options_engine.validation.policy import (  # noqa: E402
    FIXTURE_DIR,
    load_policy,
    policy_sha256,
    stress_cases,
)

mp.mp.dps = 60
DIGITS = 25


def _s(x: Any) -> str:
    return str(mp.nstr(mp.mpf(x), DIGITS, strip_zeros=False))


def provenance(name: str, description: str) -> dict[str, Any]:
    return {
        "fixture": name,
        "fixture_version": 1,
        "description": description,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator": "scripts/generate_reference_fixtures.py",
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "policy_sha256": policy_sha256(),
        "libraries": {
            "python": sys.version.split()[0],
            "mpmath": mp.__version__,
            "QuantLib": ql.__version__,
        },
    }


# ---------------------------------------------------------------------------
# mpmath: independent high-precision BSM
# ---------------------------------------------------------------------------
def mp_price(w: int, S: Any, K: Any, T: Any, r: Any, q: Any, v: Any) -> Any:
    S, K, T, r, q, v = (mp.mpf(a) for a in (S, K, T, r, q, v))
    df = mp.exp(-r * T)
    fwd = S * mp.exp((r - q) * T)
    sd = v * mp.sqrt(T)
    d1 = mp.log(fwd / K) / sd + sd / 2
    d2 = d1 - sd
    return df * w * (fwd * mp.ncdf(w * d1) - K * mp.ncdf(w * d2))


def mp_greeks(w: int, S: float, K: float, T: float, r: float, q: float, v: float) -> dict[str, str]:
    def f(**kw: Any) -> Any:
        args = {"S": S, "K": K, "T": T, "r": r, "q": q, "v": v}
        args.update(kw)
        return mp_price(w, **args)

    return {
        "delta": _s(mp.diff(lambda x: f(S=x), mp.mpf(S))),
        "gamma": _s(mp.diff(lambda x: f(S=x), mp.mpf(S), 2)),
        "vega": _s(mp.diff(lambda x: f(v=x), mp.mpf(v))),
        # theta: derivative w.r.t. valuation time = -d/dT
        "theta": _s(-mp.diff(lambda x: f(T=x), mp.mpf(T))),
        "rho": _s(mp.diff(lambda x: f(r=x), mp.mpf(r))),
        "dividend_rho": _s(mp.diff(lambda x: f(q=x), mp.mpf(q))),
    }


def build_mpmath_fixture() -> dict[str, Any]:
    cases = []
    for c in stress_cases(load_policy()):
        w = c.option_type.sign
        args = (c.spot, c.strike, c.time, c.rate, c.dividend_yield, c.vol)
        cases.append(
            {
                "case_id": c.case_id,
                "regime": c.regime,
                "option_type": c.option_type.value,
                "spot": c.spot,
                "strike": c.strike,
                "time": c.time,
                "vol": c.vol,
                "rate": c.rate,
                "dividend_yield": c.dividend_yield,
                "price": _s(mp_price(w, *args)),
                "greeks": mp_greeks(w, *args),
            }
        )
    out = provenance(
        "bsm_european_mpmath",
        "European BSM prices and Greeks at 60-digit working precision over the policy "
        "stress matrix. Greeks are numerical derivatives (mpmath.diff) of the price.",
    )
    out["conventions"] = {
        "time": "years (float inputs used exactly as given)",
        "theta": "-dV/dT per year",
        "vega/rho/dividend_rho": "per 1.00 absolute change",
    }
    out["cases"] = cases
    return out


# ---------------------------------------------------------------------------
# QuantLib
# ---------------------------------------------------------------------------
EVAL = ql.Date(28, 9, 2026)
DC = ql.Actual365Fixed()
CAL = ql.NullCalendar()


def _process(S: float, r: float, q: float, v: float) -> ql.BlackScholesMertonProcess:
    ql.Settings.instance().evaluationDate = EVAL
    return ql.BlackScholesMertonProcess(
        ql.QuoteHandle(ql.SimpleQuote(S)),
        ql.YieldTermStructureHandle(ql.FlatForward(EVAL, q, DC, ql.Continuous)),
        ql.YieldTermStructureHandle(ql.FlatForward(EVAL, r, DC, ql.Continuous)),
        ql.BlackVolTermStructureHandle(ql.BlackConstantVol(EVAL, CAL, v, DC)),
    )


def _option(w: int, K: float, days: int, american: bool) -> ql.VanillaOption:
    payoff = ql.PlainVanillaPayoff(ql.Option.Call if w > 0 else ql.Option.Put, K)
    expiry = EVAL + days
    exercise = ql.AmericanExercise(EVAL, expiry) if american else ql.EuropeanExercise(expiry)
    return ql.VanillaOption(payoff, exercise)


def build_quantlib_european() -> dict[str, Any]:
    cases = []
    for w in (1, -1):
        for K in (70.0, 90.0, 100.0, 110.0, 140.0):
            for days in (7, 91, 365, 1825):
                for v in (0.05, 0.2, 0.6):
                    for r in (-0.01, 0.03):
                        for q in (0.0, 0.02):
                            opt = _option(w, K, days, american=False)
                            opt.setPricingEngine(
                                ql.AnalyticEuropeanEngine(_process(100.0, r, q, v))
                            )
                            cases.append(
                                {
                                    "option_type": "call" if w > 0 else "put",
                                    "spot": 100.0,
                                    "strike": K,
                                    "days": days,
                                    "time": days / 365.0,
                                    "vol": v,
                                    "rate": r,
                                    "dividend_yield": q,
                                    "price": opt.NPV(),
                                    "greeks": {
                                        "delta": opt.delta(),
                                        "gamma": opt.gamma(),
                                        "vega": opt.vega(),
                                        "theta": opt.theta(),
                                        "rho": opt.rho(),
                                        "dividend_rho": opt.dividendRho(),
                                    },
                                }
                            )
    out = provenance(
        "bsm_european_quantlib",
        "QuantLib AnalyticEuropeanEngine, flat continuous curves, Actual365Fixed, "
        "NullCalendar, integer-day maturities from 2026-09-28.",
    )
    out["conventions"] = {
        "theta": "QuantLib theta per year (-dV/dT)",
        "vega/rho/dividend_rho": "per 1.00 absolute change",
    }
    out["cases"] = cases
    return out


def _dividend_schedule(divs: list[tuple[int, float]]) -> Any:
    return ql.DividendVector([EVAL + d for d, _ in divs], [a for _, a in divs])


def build_quantlib_american() -> dict[str, Any]:
    t_grid, x_grid, damping = 2000, 1600, 10
    cases = []
    for w in (1, -1):
        for K in (80.0, 100.0, 120.0):
            for days in (91, 365, 730):
                for v in (0.2, 0.4):
                    for r, q in ((0.05, 0.0), (0.05, 0.03), (0.0, 0.03), (-0.01, 0.0)):
                        opt = _option(w, K, days, american=True)
                        opt.setPricingEngine(
                            ql.FdBlackScholesVanillaEngine(
                                _process(100.0, r, q, v), t_grid, x_grid, damping
                            )
                        )
                        cases.append(
                            {
                                "option_type": "call" if w > 0 else "put",
                                "spot": 100.0,
                                "strike": K,
                                "days": days,
                                "time": days / 365.0,
                                "vol": v,
                                "rate": r,
                                "dividend_yield": q,
                                "dividends": [],
                                "price": opt.NPV(),
                            }
                        )
    div_sets = {
        "two_quarterly": [(80, 1.5), (262, 1.5)],
        "large_single": [(120, 5.0)],
    }
    for w in (1, -1):
        for K in (90.0, 100.0, 110.0):
            for v in (0.2, 0.35):
                for name, divs in div_sets.items():
                    for american in (True, False):
                        opt = _option(w, K, 365, american=american)
                        proc = _process(100.0, 0.05, 0.0, v)
                        sched = _dividend_schedule(divs)
                        engine = (
                            ql.FdBlackScholesVanillaEngine(
                                proc,
                                sched,
                                t_grid,
                                x_grid,
                                damping,
                                ql.FdmSchemeDesc.Douglas(),
                                False,
                                -ql.nullDouble(),
                                ql.FdBlackScholesVanillaEngine.Escrowed,
                            )
                            if american
                            else ql.AnalyticDividendEuropeanEngine(proc, sched)
                        )
                        opt.setPricingEngine(engine)
                        cases.append(
                            {
                                "option_type": "call" if w > 0 else "put",
                                "exercise": "american" if american else "european",
                                "spot": 100.0,
                                "strike": K,
                                "days": 365,
                                "time": 1.0,
                                "vol": v,
                                "rate": 0.05,
                                "dividend_yield": 0.0,
                                "dividend_set": name,
                                "dividends": [[d / 365.0, a] for d, a in divs],
                                "price": opt.NPV(),
                                "engine": "FdBlackScholesVanillaEngine(Escrowed)"
                                if american
                                else "AnalyticDividendEuropeanEngine",
                            }
                        )
    out = provenance(
        "american_quantlib_fd",
        "QuantLib FdBlackScholesVanillaEngine (Douglas scheme) for American exercise; "
        "cash dividends use CashDividendModel=Escrowed; European dividend cases use "
        "AnalyticDividendEuropeanEngine. FD prices carry their own discretisation error.",
    )
    out["fd_grid"] = {"t_grid": t_grid, "x_grid": x_grid, "damping_steps": damping}
    out["cases"] = cases
    return out


def build_normalized_grid() -> dict[str, Any]:
    """Normalised OTM Black value b(x, s) on a dense log grid.

    Covers the near-forward-ATM regime (|x| down to 1e-14), tiny total
    volatility, and deep tails. Added after a defect near |x/s| << 1 was found.
    """
    import numpy as np

    xs = -np.logspace(-14, 1.3, 70)
    ss = np.logspace(-9, 1.6, 55)
    cases = []
    for x in xs:
        for sd in ss:
            xm, sm = mp.mpf(float(x)), mp.mpf(float(sd))
            h, t = xm / sm, sm / 2
            b = mp.exp(xm / 2) * mp.ncdf(h + t) - mp.exp(-xm / 2) * mp.ncdf(h - t)
            cases.append({"x": float(x), "s": float(sd), "b": _s(b)})
    out = provenance(
        "normalized_black_grid",
        "b(x,s) = e^{x/2} N(x/s + s/2) - e^{-x/2} N(x/s - s/2) for x <= 0 on a 70 x 55 "
        "log grid, 60-digit mpmath.",
    )
    out["cases"] = cases
    return out


def build_published() -> dict[str, Any]:
    out = provenance(
        "bsm_published_examples",
        "Worked example values as printed in a standard textbook (rounded to 2 decimals).",
    )
    out["cases"] = [
        {
            "source": "J. C. Hull, Options, Futures, and Other Derivatives: Black-Scholes-Merton "
            "worked example (S0=42, K=40, r=10%, sigma=20%, T=0.5)",
            "transcription_note": "values transcribed from the textbook example; edition/page "
            "not re-verified in this session",
            "spot": 42.0,
            "strike": 40.0,
            "time": 0.5,
            "vol": 0.2,
            "rate": 0.1,
            "dividend_yield": 0.0,
            "call": "4.76",
            "put": "0.81",
        }
    ]
    return out


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    targets = {
        "bsm_european_mpmath_v1.json": build_mpmath_fixture,
        "bsm_european_quantlib_v1.json": build_quantlib_european,
        "american_quantlib_fd_v1.json": build_quantlib_american,
        "bsm_published_examples_v1.json": build_published,
        "normalized_black_grid_v1.json": build_normalized_grid,
    }
    only = set(sys.argv[1:])
    for name, builder in targets.items():
        if only and name not in only:
            continue
        data = builder()
        (FIXTURE_DIR / name).write_text(json.dumps(data, indent=1) + "\n")
        print(f"wrote {name}: {len(data['cases'])} cases")


if __name__ == "__main__":
    main()
