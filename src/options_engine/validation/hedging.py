"""Delta-hedging acceptance checks (policy ``[hedging]``, ADR 0021)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from options_engine.application import hedging
from options_engine.domain.conventions import OptionType
from options_engine.domain.numerics import HestonConfig
from options_engine.engines.heston_fourier import heston_call
from options_engine.models.heston import HestonModel
from options_engine.validation.checks import CheckResult
from options_engine.validation.policy import load_policy


def _base(pol: dict[str, Any]) -> dict[str, Any]:
    return {
        "option_type": OptionType.CALL,
        "spot": pol["spot"],
        "strike": pol["strike"],
        "time": pol["time"],
        "rate": pol["rate"],
        "dividend_yield": pol["dividend_yield"],
    }


def _in_band(res: CheckResult, case: str, value: float, band: list[float]) -> None:
    lo, hi = band
    centre, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
    res.record(case, value - centre, centre, half, 0.0, value=value)


def check_hedging_gbm() -> CheckResult:
    pol = load_policy()["hedging"]
    cv, mv = pol["gbm_correct_vol"], pol["gbm_misspecified_vol"]
    res = CheckResult(
        "hedging_gbm",
        "Discrete delta hedging of a short call under GBM: hedging error std falls as "
        "N^-1/2 and matches the leading-order Gamma formula; with a wrong hedge vol the mean "
        "P&L matches the continuous-time Gamma identity",
        tolerance={"correct_vol": cv, "misspecified_vol": mv, "z": pol["z"]},
    )
    out = hedging.run(
        hedging.HedgeSetup(
            **_base(pol),
            paths=pol["paths"],
            rebalances=tuple(pol["rebalances"]),
            seed=pol["seed"],
            real_vol=cv["vol"],
        )
    )
    rows = [r for r in out["rows"] if r["rebalances"] >= 32]
    slope = float(
        np.polyfit(np.log([r["rebalances"] for r in rows]), np.log([r["std"] for r in rows]), 1)[0]
    )
    _in_band(res, "slope_log_std_vs_log_n", slope, cv["slope_band"])
    finest = max(out["rows"], key=lambda r: r["rebalances"])
    ratio = finest["std"] / finest["leading_order_std"]
    _in_band(res, "std_over_leading_order_at_finest_n", ratio, cv["leading_order_ratio_band"])

    mis = hedging.run(
        hedging.HedgeSetup(
            **_base(pol),
            paths=pol["paths"],
            rebalances=(mv["rebalances"],),
            seed=pol["seed"] + 1,
            real_vol=mv["real_vol"],
            hedge_vol=mv["hedge_vol"],
        )
    )["rows"][0]
    res.record(
        "misspecified_vol_mean_vs_gamma_identity",
        mis["pnl_minus_identity_mean"],
        mis["vol_mismatch_identity_mean"],
        pol["z"] * mis["pnl_minus_identity_se"] + mv["atol"],
        0.0,
    )
    res.details = {
        "rows": [
            {k: r[k] for k in ("rebalances", "mean", "std", "leading_order_std")}
            for r in out["rows"]
        ],
        "slope": slope,
        "ratio_at_finest": ratio,
        "misspecified": {
            k: mis[k]
            for k in (
                "mean",
                "std",
                "vol_mismatch_identity_mean",
                "pnl_minus_identity_mean",
                "pnl_minus_identity_se",
            )
        },
    }
    return res


def check_heston_simulator() -> CheckResult:
    pol = load_policy()
    hp, sp = pol["hedging"], pol["hedging"]["heston_simulator"]
    res = CheckResult(
        "heston_qe_simulator",
        f"Andersen QE Heston paths ({sp['steps_per_year']} steps/year, {sp['paths']:,} paths, "
        "T = 1): discounted spot is a martingale and call prices match the Fourier pricer",
        tolerance=sp | {"z": hp["z"]},
    )
    z = hp["z"]
    s0, r, q, T = hp["spot"], hp["rate"], hp["dividend_yield"], 1.0
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(hp["seed"] + 2)))
    for name in sp["parameter_sets"]:
        m = HestonModel(*pol["heston_parameter_sets"][name])
        paths = hedging.heston_qe_paths(s0, r, q, m, T, sp["steps_per_year"], sp["paths"], rng)
        s_t = paths.spot[:, -1]
        D, F = math.exp(-r * T), s0 * math.exp((r - q) * T)
        disc = D * s_t
        se = float(disc.std(ddof=1) / math.sqrt(disc.size))
        target = s0 * math.exp(-q * T)
        res.record(
            f"{name}:martingale",
            float(disc.mean()) - target,
            target,
            z * se + sp["martingale_atol"],
            0.0,
            se=se,
        )
        for K in sp["strikes"]:
            pay = D * np.maximum(s_t - K, 0.0)
            mc, se_c = float(pay.mean()), float(pay.std(ddof=1) / math.sqrt(pay.size))
            ref = heston_call(F, K, D, T, m, HestonConfig())[0]
            res.record(
                f"{name}:call:K{K:g}",
                mc - ref,
                ref,
                z * se_c + sp["price_atol"],
                0.0,
                se=se_c,
                mc=mc,
            )
    return res


def check_heston_min_variance_hedge() -> CheckResult:
    pol = load_policy()
    hp, mv = pol["hedging"], pol["hedging"]["heston_min_variance"]
    res = CheckResult(
        "heston_min_variance_hedge",
        "Under Heston, the minimum-variance delta dC/dS + (rho sigma/S) dC/dv leaves a smaller "
        "hedging error than the Heston delta dC/dS (paired test on the same paths); the BSM "
        "delta at the model's implied vol is reported alongside",
        tolerance={"min_z": mv["min_z"]},
    )
    out = hedging.run(
        hedging.HedgeSetup(
            **_base(hp),
            paths=mv["paths"],
            rebalances=(mv["rebalances"],),
            seed=hp["seed"] + 3,
            heston=HestonModel(*pol["heston_parameter_sets"][mv["parameter_set"]]),
            strategies=("bsm", "heston", "heston_mv"),
        )
    )
    n = mv["rebalances"]
    a, b = out["pnl"][("heston", n)], out["pnl"][("heston_mv", n)]
    d = a * a - b * b
    zstat = float(d.mean() / (d.std(ddof=1) / math.sqrt(d.size)))
    res.record(
        "paired_z_mv_beats_heston_delta",
        max(0.0, mv["min_z"] - zstat),
        mv["min_z"],
        1e-12,
        0.0,
        z=zstat,
    )
    res.details = {
        "z": zstat,
        "std_by_strategy": {r["strategy"]: r["std"] for r in out["rows"]},
        "mean_by_strategy": {r["strategy"]: r["mean"] for r in out["rows"]},
        "model_price": out["model_price"],
        "model_implied_vol": out["model_implied_vol"],
    }
    return res
