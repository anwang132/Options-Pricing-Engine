"""Longstaff-Schwartz acceptance checks (policy section ``[lsm]``, ADR 0018)."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from typing import Any

import numpy as np

from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.numerics import LSMConfig
from options_engine.engines.base import PricingProblem
from options_engine.engines.crr import build_tree
from options_engine.engines.lsm_american import lsm_price
from options_engine.validation.checks import CheckResult, analytic_price
from options_engine.validation.policy import AXES, load_policy


def _grid(section: dict[str, Any], option_type: OptionType) -> list[PricingProblem]:
    return [
        PricingProblem(
            option_type,
            ExerciseStyle.AMERICAN,
            spot,
            strike,
            time,
            rate,
            dividend_yield,
            vol,
        )
        for spot, strike, time, vol, rate, dividend_yield in itertools.product(
            *(section[a] for a in AXES)
        )
    ]


def lsm_cases() -> list[tuple[str, PricingProblem, bool]]:
    """(case id, problem, early exercise possible) for the predeclared matrix."""
    pol = load_policy()["lsm"]
    cases: list[tuple[str, PricingProblem, bool]] = []
    for p in _grid(pol["puts"], OptionType.PUT):
        cases.append(("put", p, True))
    for p in _grid(pol["calls_high_yield"], OptionType.CALL):
        cases.append(("call_high_yield", p, True))
    divs = tuple((float(t), float(a)) for t, a in pol["cash_dividends"]["dividends"])
    for ot in (OptionType.PUT, OptionType.CALL):
        p = PricingProblem(ot, ExerciseStyle.AMERICAN, 100.0, 100.0, 1.0, 0.05, 0.0, 0.25, divs)
        cases.append(("cash_dividends", p, True))
    for p in _grid(pol["no_early_exercise_calls"], OptionType.CALL):
        cases.append(("no_early_exercise_call", p, False))
    return cases


def _case_id(group: str, p: PricingProblem) -> str:
    return (
        f"{group}:{p.option_type.value}:K{p.strike:g}:T{p.time_to_expiry:g}:v{p.volatility:g}:"
        f"r{p.rate:g}:q{p.dividend_yield:g}"
    )


def check_lsm_vs_bermudan() -> CheckResult:
    pol = load_policy()
    lp = pol["lsm"]
    k = pol["tolerance"]["crr_vs_bsm"]["k"]
    cfg = LSMConfig(
        paths=lp["paths"],
        regression_paths=lp["regression_paths"],
        exercise_dates=lp["exercise_dates"],
        basis_degree=lp["basis_degree"],
    )
    M, m, z = lp["exercise_dates"], lp["tree_steps_per_date"], lp["z"]
    res = CheckResult(
        "lsm_vs_bermudan_crr",
        f"Longstaff-Schwartz ({cfg.paths:,} pricing + {cfg.regression_paths:,} regression paths, "
        f"{M} exercise dates) vs a CRR tree exercising on the same dates ({M * m} steps); "
        "calls without dividends vs the closed-form European price",
        tolerance={k2: lp[k2] for k2 in ("z", "regression_atol", "regression_rtol")} | {"crr_k": k},
    )
    seeds = np.random.SeedSequence(lp["seed"]).spawn(len(lsm_cases()))
    rows = []
    for (group, p, early), ss in zip(lsm_cases(), seeds, strict=True):
        cid = _case_id(group, p)
        seed = int(ss.generate_state(1)[0])
        L, unc, diag = lsm_price(p, replace(cfg, seed=seed))
        s = unc.standard_error if unc is not None else 0.0
        if early:
            B = build_tree(p, M * m, exercise_every=m).price
            american = build_tree(p, M * m).price
            eps = k * p.spot * p.volatility * math.sqrt(p.time_to_expiry) / (M * m)
        else:
            B = american = analytic_price(p.with_changes(exercise_style=ExerciseStyle.EUROPEAN))
            eps = 0.0
        scale = p.spot / 100.0
        res.record(f"{cid}:upper", max(L - B, 0.0), B, z * s + eps, 0.0, got=L, se=s)
        res.record(
            f"{cid}:lower",
            max(B - L, 0.0),
            B,
            z * s + eps + lp["regression_atol"] * scale,
            lp["regression_rtol"],
            got=L,
            se=s,
        )
        rows.append(
            {
                "case": cid,
                "lsm": L,
                "se": s,
                "bermudan_reference": B,
                "american_reference": american,
                "difference_in_se": (L - B) / s if s > 0 else None,
                "in_sample_estimate": diag["in_sample_estimate"],
                "variance_reduction_factor": diag.get("variance_reduction_factor"),
            }
        )
    diffs = [r["difference_in_se"] for r in rows if r["difference_in_se"] is not None]
    res.details = {
        "cases": rows,
        "mean_difference_in_se": float(np.mean(diffs)) if diffs else None,
        "max_bermudan_to_american_gap": max(
            r["american_reference"] - r["bermudan_reference"] for r in rows
        ),
    }
    return res


def check_lsm_se_calibration() -> CheckResult:
    sp = load_policy()["lsm"]["se_calibration"]
    c = sp["case"]
    p = PricingProblem(
        OptionType(c["option_type"]),
        ExerciseStyle.AMERICAN,
        c["spot"],
        c["strike"],
        c["time"],
        c["rate"],
        c["dividend_yield"],
        c["vol"],
    )
    lo, hi = sp["ratio_band"]
    res = CheckResult(
        "lsm_se_calibration",
        f"Spread of {sp['replications']} independent LSM estimates vs their mean reported SE "
        f"(ATM put; {sp['paths']:,} pricing paths each)",
        tolerance={"ratio_band": [lo, hi]},
    )
    seeds = np.random.SeedSequence(load_policy()["lsm"]["seed"] + 1).spawn(sp["replications"])
    estimates, ses = [], []
    for ss in seeds:
        cfg = LSMConfig(
            paths=sp["paths"],
            regression_paths=sp["regression_paths"],
            exercise_dates=sp["exercise_dates"],
            seed=int(ss.generate_state(1)[0]),
        )
        price, unc, _ = lsm_price(p, cfg)
        assert unc is not None
        estimates.append(price)
        ses.append(unc.standard_error)
    ratio = float(np.std(estimates, ddof=1) / np.mean(ses))
    centre = 0.5 * (lo + hi)
    res.record("se_ratio", ratio - centre, centre, 0.5 * (hi - lo), 0.0, ratio=ratio)
    res.details = {
        "ratio": ratio,
        "mean_estimate": float(np.mean(estimates)),
        "sd_of_estimates": float(np.std(estimates, ddof=1)),
        "mean_reported_se": float(np.mean(ses)),
    }
    return res


def check_lsm_upper_bound() -> CheckResult:
    pol = load_policy()
    ub, lp = pol["lsm"]["upper_bound"], pol["lsm"]
    k = pol["tolerance"]["crr_vs_bsm"]["k"]
    M, m, z = ub["exercise_dates"], ub["tree_steps_per_date"], ub["z"]
    cfg = LSMConfig(
        paths=ub["paths"],
        regression_paths=ub["regression_paths"],
        exercise_dates=M,
        upper_bound=True,
        outer_paths=ub["outer_paths"],
        inner_paths=ub["inner_paths"],
    )
    res = CheckResult(
        "lsm_dual_upper_bound",
        f"Andersen-Broadie upper bound ({cfg.outer_paths:,} outer x {cfg.inner_paths} inner "
        f"paths, {M} dates) vs a CRR tree exercising on the same dates: a valid bound, and "
        "within the predeclared gap",
        tolerance={key: ub[key] for key in ("z", "gap_atol", "gap_rtol")} | {"crr_k": k},
    )
    problems = [
        (
            f"{ot}:K{K:g}:T{T:g}:v{vol:g}:r{r:g}:q{q:g}",
            PricingProblem(OptionType(ot), ExerciseStyle.AMERICAN, 100.0, K, T, r, q, vol),
        )
        for ot, K, T, vol, r, q in ub["cases"]
    ]
    if ub["include_cash_dividend_call"]:
        divs = tuple((float(t), float(a)) for t, a in lp["cash_dividends"]["dividends"])
        problems.append(
            (
                "cash_dividends:call:K100",
                PricingProblem(
                    OptionType.CALL,
                    ExerciseStyle.AMERICAN,
                    100.0,
                    100.0,
                    1.0,
                    0.05,
                    0.0,
                    0.25,
                    divs,
                ),
            )
        )
    seeds = np.random.SeedSequence(ub["seed"]).spawn(len(problems))
    rows = []
    for (cid, p), ss in zip(problems, seeds, strict=True):
        L, unc, diag = lsm_price(p, replace(cfg, seed=int(ss.generate_state(1)[0])))
        if "upper_bound" not in diag:
            res.fail(cid, diag.get("upper_bound_note", "upper bound not computed"))
            continue
        U, se_u = diag["upper_bound"], diag["upper_bound_se"]
        B = build_tree(p, M * m, exercise_every=m).price
        eps = k * p.spot * p.volatility * math.sqrt(p.time_to_expiry) / (M * m)
        res.record(f"{cid}:valid", max(B - U, 0.0), B, z * se_u + eps, 0.0, upper=U)
        res.record(
            f"{cid}:tight",
            max(U - B, 0.0),
            B,
            ub["gap_atol"] * p.spot / 100 + z * se_u + eps,
            ub["gap_rtol"],
            upper=U,
        )
        rows.append(
            {
                "case": cid,
                "lower": L,
                "lower_se": unc.standard_error if unc else 0.0,
                "upper": U,
                "upper_se": se_u,
                "bermudan_reference": B,
                "upper_minus_reference": U - B,
                "duality_gap": diag["duality_gap"],
            }
        )
    res.details = {"cases": rows}
    return res
