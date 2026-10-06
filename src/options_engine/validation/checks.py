"""Deterministic validation checks shared by the test suite and the report.

Each check takes the implementation under test as a callable, so fault-
injection tests can pass deliberately broken pricers and confirm detection.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from options_engine.analytics.implied_vol import SOLVED, IVStatus, implied_volatility
from options_engine.application.surface import SurfaceService
from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import AnalyticConfig, HestonConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.base import PricingProblem
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine, normalized_otm_price
from options_engine.engines.crr import build_tree, up_probability
from options_engine.engines.heston_fourier import heston_price
from options_engine.models.heston import HestonModel
from options_engine.validation.policy import (
    FIXTURE_DIR,
    StressCase,
    load_policy,
    stress_cases,
)

PriceFn = Callable[[PricingProblem], float]
GreeksFn = Callable[[PricingProblem], dict[GreekName, float | None]]

_ANALYTIC = BlackScholesAnalyticEngine()


def analytic_price(problem: PricingProblem) -> float:
    return _ANALYTIC.price(problem, AnalyticConfig(), ()).price


def analytic_greeks(problem: PricingProblem) -> dict[GreekName, float | None]:
    out = _ANALYTIC.price(problem, AnalyticConfig(), tuple(GreekName))
    return {g: r.value for g, r in out.greeks.items()}


def crr_price(steps: int) -> PriceFn:
    return lambda p: build_tree(p, steps).price


@dataclass
class CheckResult:
    name: str
    description: str
    n_cases: int = 0
    n_failed: int = 0
    n_excluded: int = 0
    max_abs_error: float = 0.0
    # max |error| / (atol + rtol*|ref|); <= 1 passes
    max_scaled_error: float = 0.0
    tolerance: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.n_failed == 0 and self.n_cases > 0

    def record(
        self, case_id: str, error: float, reference: float, atol: float, rtol: float, **extra: Any
    ) -> bool:
        self.n_cases += 1
        bound = atol + rtol * abs(reference)
        scaled = abs(error) / bound if bound > 0 else (0.0 if error == 0 else math.inf)
        if not math.isfinite(error):
            scaled = math.inf
        self.max_abs_error = (
            max(self.max_abs_error, abs(error)) if math.isfinite(error) else math.inf
        )
        self.max_scaled_error = max(self.max_scaled_error, scaled)
        ok = scaled <= 1.0
        if not ok:
            self.n_failed += 1
            if len(self.failures) < 25:
                self.failures.append(
                    {
                        "case": case_id,
                        "error": error,
                        "reference": reference,
                        "bound": bound,
                        **extra,
                    }
                )
        return ok

    def fail(self, case_id: str, reason: str) -> None:
        self.n_cases += 1
        self.n_failed += 1
        self.max_scaled_error = math.inf
        if len(self.failures) < 25:
            self.failures.append({"case": case_id, "reason": reason})

    def exclude(self, reason: str) -> None:
        self.n_excluded += 1
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "passed": self.passed,
            "n_cases": self.n_cases,
            "n_failed": self.n_failed,
            "n_excluded": self.n_excluded,
            "exclusions": self.exclusions,
            "max_abs_error": self.max_abs_error,
            "max_scaled_error": self.max_scaled_error,
            "tolerance": self.tolerance,
            "failures": self.failures,
            "details": self.details,
        }


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURE_DIR / name).read_text())
    return data


def _problem(
    case: dict[str, Any], exercise: ExerciseStyle = ExerciseStyle.EUROPEAN
) -> PricingProblem:
    return PricingProblem(
        OptionType(case["option_type"]),
        ExerciseStyle(case.get("exercise", exercise.value)),
        float(case["spot"]),
        float(case["strike"]),
        float(case["time"]),
        float(case["rate"]),
        float(case["dividend_yield"]),
        float(case["vol"]),
        tuple((float(t), float(a)) for t, a in case.get("dividends", [])),
    )


def _ql_case_id(c: dict[str, Any]) -> str:
    return (
        f"ql:{c['option_type']}:K{c['strike']}:d{c['days']}:v{c['vol']}"
        f":r{c['rate']}:q{c['dividend_yield']}"
    )


def _scale(spot: float) -> float:
    return spot / 100.0


# --- BSM vs independent references ------------------------------------------------------


def check_mpmath_prices(price_fn: PriceFn = analytic_price) -> CheckResult:
    tol = load_policy()["tolerance"]["bsm_vs_mpmath"]["price"]
    res = CheckResult(
        "bsm_price_vs_mpmath",
        "European BSM price vs independent 60-digit mpmath implementation, full stress matrix",
        tolerance=tol,
    )
    by_regime: dict[str, float] = {}
    for c in load_fixture("bsm_european_mpmath_v1.json")["cases"]:
        ref = float(c["price"])
        got = price_fn(_problem(c))
        atol = tol["atol"] * _scale(c["spot"])
        res.record(c["case_id"], got - ref, ref, atol, tol["rtol"], got=got)
        bound = atol + tol["rtol"] * abs(ref)
        by_regime[c["regime"]] = max(by_regime.get(c["regime"], 0.0), abs(got - ref) / bound)
    res.details["max_scaled_error_by_regime"] = by_regime
    return res


def check_normalized_grid() -> CheckResult:
    """Dense grid of the normalised OTM Black function vs 60-digit mpmath."""
    tol = load_policy()["tolerance"]["bsm_vs_mpmath"]["price"]
    fx = load_fixture("normalized_black_grid_v1.json")["cases"]
    x = np.array([c["x"] for c in fx])
    s = np.array([c["s"] for c in fx])
    got, fallback = normalized_otm_price(x, s)
    res = CheckResult(
        "normalized_black_vs_mpmath",
        "Normalised OTM Black value b(x, s) on a 70x55 log grid incl. near-ATM-forward "
        "(|x| >= 1e-14), tiny total vol and deep tails; relative tolerance only, values "
        "below 1e-290 (float64 underflow region) must be <= 1e-280",
        tolerance={"rtol": tol["rtol"], "underflow_floor": 1e-280},
    )
    for c, b in zip(fx, got, strict=True):
        ref = float(c["b"]) if float(c["b"]) > 1e-290 else 0.0
        cid = f"x{c['x']:.3g}:s{c['s']:.3g}"
        if ref == 0.0:
            res.record(cid, float(b) if b > 1e-280 else 0.0, 0.0, 1e-280, 0.0)
        else:
            res.record(cid, float(b) - ref, ref, 0.0, tol["rtol"])
    res.details["fallback_evaluations"] = int(fallback.sum())
    return res


def check_mpmath_greeks(greeks_fn: GreeksFn = analytic_greeks) -> CheckResult:
    tol = load_policy()["tolerance"]["bsm_vs_mpmath"]["greeks"]
    res = CheckResult(
        "bsm_greeks_vs_mpmath",
        "Analytic Greeks vs 60-digit numerical derivatives of an independent mpmath price",
        tolerance=tol,
    )
    for c in load_fixture("bsm_european_mpmath_v1.json")["cases"]:
        got = greeks_fn(_problem(c))
        for name, ref_s in c["greeks"].items():
            g = GreekName(name)
            ref = float(ref_s)
            value = got.get(g)
            if value is None:
                res.fail(f"{c['case_id']}:{name}", "Greek missing")
                continue
            res.record(f"{c['case_id']}:{name}", value - ref, ref, tol["atol"], tol["rtol"])
    return res


def check_quantlib_european(
    price_fn: PriceFn = analytic_price, greeks_fn: GreeksFn = analytic_greeks
) -> tuple[CheckResult, CheckResult]:
    tol = load_policy()["tolerance"]["bsm_vs_quantlib"]
    fx = load_fixture("bsm_european_quantlib_v1.json")
    pr = CheckResult(
        "bsm_price_vs_quantlib",
        f"European price vs QuantLib {fx['libraries']['QuantLib']} AnalyticEuropeanEngine",
        tolerance=tol["price"],
    )
    gr = CheckResult(
        "bsm_greeks_vs_quantlib",
        "Analytic Greeks vs QuantLib (delta, gamma, vega, theta, rho, dividend rho)",
        tolerance=tol["greeks"],
    )
    for c in fx["cases"]:
        cid = _ql_case_id(c)
        p = _problem(c)
        pr.record(
            cid, price_fn(p) - c["price"], c["price"], tol["price"]["atol"], tol["price"]["rtol"]
        )
        got = greeks_fn(p)
        for name, ref in c["greeks"].items():
            value = got.get(GreekName(name))
            if value is None:
                gr.fail(f"{cid}:{name}", "Greek missing")
            else:
                gr.record(
                    f"{cid}:{name}", value - ref, ref, tol["greeks"]["atol"], tol["greeks"]["rtol"]
                )
    return pr, gr


def check_published(price_fn: PriceFn = analytic_price) -> CheckResult:
    fx = load_fixture("bsm_published_examples_v1.json")
    res = CheckResult(
        "bsm_published_examples",
        "Textbook worked examples, compared at the printed rounding (half unit in last place)",
    )
    for c in fx["cases"]:
        for opt in ("call", "put"):
            printed = c[opt]
            half = 0.5 * 10.0 ** (-len(printed.split(".")[1]))
            got = price_fn(_problem({**c, "option_type": opt}))
            res.record(
                f"{c['source'][:40]}:{opt}",
                got - float(printed),
                float(printed),
                half,
                0.0,
                got=got,
            )
    res.tolerance = {"atol": "half unit in the last printed decimal", "rtol": 0.0}
    return res


# --- Identities ---------------------------------------------------------------------------


def check_identities(
    price_fn: PriceFn = analytic_price, label: str = "bsm_analytic"
) -> CheckResult:
    """Put-call parity and European bounds under deterministic continuous carry."""
    tol = load_policy()["tolerance"]["identities"]
    res = CheckResult(
        f"identities_{label}",
        "C - P = D(F - K); D max(F-K,0) <= C <= D F; D max(K-F,0) <= P <= D K (European, "
        "continuous carry)",
        tolerance=tol,
    )
    seen: set[tuple[float, ...]] = set()
    for c in stress_cases():
        key = (c.spot, c.strike, c.time, c.vol, c.rate, c.dividend_yield)
        if key in seen:
            continue
        seen.add(key)
        call = price_fn(StressCase(c.regime, OptionType.CALL, *key).problem())
        put = price_fn(StressCase(c.regime, OptionType.PUT, *key).problem())
        D = math.exp(-c.rate * c.time)
        F = c.spot * math.exp((c.rate - c.dividend_yield) * c.time)
        atol = tol["atol"] * _scale(c.spot)
        parity = D * (F - c.strike)
        res.record(
            f"{c.case_id}:parity",
            (call - put) - parity,
            max(abs(call), abs(put), abs(parity)),
            atol,
            tol["rtol"],
        )
        for name, value, lo, hi in (
            ("call_bounds", call, D * max(F - c.strike, 0.0), D * F),
            ("put_bounds", put, D * max(c.strike - F, 0.0), D * c.strike),
        ):
            slack = atol + tol["rtol"] * abs(hi)
            violation = max(lo - value, value - hi, 0.0)
            res.record(f"{c.case_id}:{name}", violation, hi, slack, 0.0)
    return res


# --- Finite-difference sweeps ---------------------------------------------------------------


def _bumped(problem: PricingProblem, greek: GreekName, h: float) -> list[PricingProblem] | None:
    """Problems for a central difference; None if a bump leaves the valid domain."""
    field_for = {
        GreekName.DELTA: "spot",
        GreekName.GAMMA: "spot",
        GreekName.VEGA: "volatility",
        GreekName.THETA: "time_to_expiry",
        GreekName.RHO: "rate",
        GreekName.DIVIDEND_RHO: "dividend_yield",
    }
    name = field_for[greek]
    x = getattr(problem, name)
    lo = x - h
    if name in ("spot", "volatility", "time_to_expiry") and lo <= 0:
        return None
    return [problem.with_changes(**{name: x + h}), problem.with_changes(**{name: lo}), problem]


def fd_estimate(
    price_fn: PriceFn, problem: PricingProblem, greek: GreekName, rel: float
) -> tuple[float, float] | None:
    scale = {
        GreekName.DELTA: problem.spot,
        GreekName.GAMMA: problem.spot,
        GreekName.VEGA: problem.volatility,
        GreekName.THETA: problem.time_to_expiry,
        GreekName.RHO: 1.0,
        GreekName.DIVIDEND_RHO: 1.0,
    }[greek]
    h = rel * scale
    probs = _bumped(problem, greek, h)
    if probs is None:
        return None
    up, down, mid = (price_fn(p) for p in probs)
    if greek is GreekName.GAMMA:
        return (up - 2.0 * mid + down) / (h * h), h
    est = (up - down) / (2.0 * h)
    return (-est if greek is GreekName.THETA else est), h


def check_fd_sweep(
    price_fn: PriceFn = analytic_price, greeks_fn: GreeksFn = analytic_greeks
) -> CheckResult:
    pol = load_policy()["tolerance"]["finite_difference_sweep"]
    res = CheckResult(
        "greeks_fd_sweep",
        "Analytic Greeks vs central finite differences of the price over a bump-size sweep; "
        "the best bump must agree (truncation vs rounding trade-off)",
        tolerance={k: pol[k] for k in ("atol", "rtol")},
    )
    curves: dict[str, list[tuple[float, float]]] = {}
    for c in stress_cases():
        if c.regime not in pol["regimes"]:
            res.exclude(f"regime {c.regime} excluded by policy")
            continue
        problem = c.problem()
        analytic = greeks_fn(problem)
        for g in GreekName:
            a = analytic.get(g)
            if a is None:
                res.fail(f"{c.case_id}:{g.value}", "analytic Greek missing")
                continue
            errors = []
            for rel in pol["relative_bumps"]:
                est = fd_estimate(price_fn, problem, g, rel)
                if est is None:
                    continue
                errors.append((est[1], abs(est[0] - a)))
            if not errors:
                res.exclude("no bump inside the valid domain")
                continue
            best = min(e for _, e in errors)
            res.record(f"{c.case_id}:{g.value}", best, a, pol["atol"], pol["rtol"])
            if c.case_id.startswith("ordinary:call:S100:K100:T1:v0.2:r0.03:q0.02"):
                curves[g.value] = errors
    res.details["example_curves"] = {
        "case": "ordinary:call:S100:K100:T1:v0.2:r0.03:q0.02",
        "curves": curves,
    }
    return res


# --- CRR vs BSM on the stress matrix ---------------------------------------------------------


def check_crr_vs_bsm(
    tree_price: Callable[[PricingProblem, int], float] | None = None,
) -> CheckResult:
    pol = load_policy()["tolerance"]["crr_vs_bsm"]
    ident_atol = load_policy()["tolerance"]["identities"]["atol"]
    fn = tree_price or (lambda p, n: build_tree(p, n).price)
    res = CheckResult(
        "crr_european_vs_bsm",
        "CRR European price vs closed-form BSM over the stress matrix at N and N+1 steps; "
        "bound k*S*sigma*sqrt(T)/N; invalid-probability setups must be rejected",
        tolerance={"k": pol["k"], "steps": pol["steps"]},
    )
    rejected = 0
    for c in stress_cases():
        problem = c.problem()
        ref = analytic_price(problem)
        for n in pol["steps"]:
            carry = abs(c.rate - c.dividend_yield)
            # Independent validity test of p in (0, 1): |r-q| sqrt(dt) < sigma.
            valid = carry * math.sqrt(c.time / n) < c.vol
            try:
                got = fn(problem, n)
            except DomainError as exc:
                if not valid and exc.code is ErrorCode.INVALID_TREE_PROBABILITY:
                    rejected += 1
                    res.n_cases += 1
                else:
                    res.fail(f"{c.case_id}:N{n}", f"unexpected {exc.code.value}")
                continue
            if not valid:
                res.fail(f"{c.case_id}:N{n}", "invalid probability accepted")
                continue
            bound = pol["k"] * c.spot * c.vol * math.sqrt(c.time) / n + ident_atol * _scale(c.spot)
            res.record(f"{c.case_id}:N{n}", got - ref, ref, bound, 0.0)
    res.details["correctly_rejected_invalid_probability"] = rejected
    return res


def crr_convergence_study(
    problem: PricingProblem, steps: list[int]
) -> list[dict[str, float | int]]:
    ref = analytic_price(problem.with_changes(exercise_style=ExerciseStyle.EUROPEAN))
    out: list[dict[str, float | int]] = []
    for n in steps:
        if not 0.0 < up_probability(problem, n) < 1.0:
            continue
        price = build_tree(problem, n).price
        out.append({"steps": n, "price": price, "error": price - ref})
    return out


# --- Implied volatility --------------------------------------------------------------------


def check_iv_round_trip() -> CheckResult:
    pol = load_policy()["tolerance"]["implied_vol_round_trip"]
    res = CheckResult(
        "implied_vol_round_trip",
        "Price the stress matrix with mpmath references, invert, compare volatility; cases "
        "whose time value is below the bound tolerance must return at_lower_bound",
        tolerance=pol,
    )
    statuses: dict[str, int] = {}
    for c in load_fixture("bsm_european_mpmath_v1.json")["cases"]:
        quote = float(c["price"])
        p = _problem(c)
        r = implied_volatility(
            quote, p.option_type.sign, p.spot, p.strike, p.time_to_expiry, p.rate, p.dividend_yield
        )
        statuses[r.status.value] = statuses.get(r.status.value, 0) + 1
        bound_tol = 1e-10 + 1e-9 * max(quote, r.upper_bound)
        time_value = quote - r.lower_bound
        if time_value <= bound_tol:
            if r.status is IVStatus.AT_LOWER_BOUND:
                res.n_cases += 1
            else:
                res.fail(c["case_id"], f"expected at_lower_bound, got {r.status.value}")
            continue
        if r.status not in SOLVED or r.implied_vol is None or r.vega is None:
            res.fail(c["case_id"], f"no solution: {r.status.value}")
            continue
        price_tol = 1e-10 + 1e-9 * quote
        allowed = pol["vol_atol"] + pol["safety"] * price_tol / r.vega if r.vega > 0 else math.inf
        res.record(c["case_id"], r.implied_vol - c["vol"], c["vol"], allowed, 0.0, vega=r.vega)
    res.details["status_counts"] = statuses
    return res


def iv_failure_cases() -> list[dict[str, Any]]:
    """Named adversarial inversions with their required outcome."""
    S, K, T, r, q = 100.0, 100.0, 0.5, 0.03, 0.0
    D = math.exp(-r * T)
    F = S * math.exp((r - q) * T)
    lower_call = D * max(F - K, 0.0)
    cases = [
        ("below_lower_bound", 1, S, K, T, r, lower_call - 0.01, IVStatus.BELOW_LOWER_BOUND),
        ("at_lower_bound_exact", 1, S, K, T, r, lower_call, IVStatus.AT_LOWER_BOUND),
        ("at_upper_bound", 1, S, K, T, r, D * F, IVStatus.AT_OR_ABOVE_UPPER_BOUND),
        ("above_upper_bound", 1, S, K, T, r, D * F + 1.0, IVStatus.AT_OR_ABOVE_UPPER_BOUND),
        ("needs_vol_above_cap", 1, S, K, T, r, D * F * 0.9999999, IVStatus.NO_BRACKET),
        ("negative_quote", 1, S, K, T, r, -1.0, IVStatus.INVALID_QUOTE),
        ("nan_quote", 1, S, K, T, r, math.nan, IVStatus.INVALID_QUOTE),
        ("at_expiry", 1, S, K, 0.0, r, 1.0, IVStatus.AT_EXPIRY),
        ("put_below_intrinsic", -1, S, 120.0, T, r, 18.0, IVStatus.BELOW_LOWER_BOUND),
    ]
    out = []
    for name, sign, s, k, t, rr, quote, expected in cases:
        res = implied_volatility(quote, sign, s, k, t, rr, q)
        out.append(
            {
                "case": name,
                "quote": quote,
                "expected": expected.value,
                "got": res.status.value,
                "passed": res.status is expected,
            }
        )
    # Low-vega instability: deep OTM short-dated call quoted to the cent.
    low = implied_volatility(0.01, 1, 42.0, 60.0, 30 / 365, 0.1, 0.0, price_resolution=0.005)
    out.append(
        {
            "case": "low_vega_unstable",
            "quote": 0.01,
            "expected": IVStatus.UNSTABLE_LOW_VEGA.value,
            "got": low.status.value,
            "passed": low.status is IVStatus.UNSTABLE_LOW_VEGA,
        }
    )
    return out


def check_iv_failures() -> CheckResult:
    res = CheckResult(
        "implied_vol_failure_cases", "Adversarial quotes must yield the specific structured status"
    )
    for row in iv_failure_cases():
        if row["passed"]:
            res.n_cases += 1
        else:
            res.fail(row["case"], f"expected {row['expected']}, got {row['got']}")
    res.details["cases"] = iv_failure_cases()
    return res


# --- Boundary behaviour ------------------------------------------------------------------------


def check_boundaries() -> CheckResult:
    """Expiry, zero volatility and kink handling produce exact values or explicit statuses."""
    res = CheckResult("boundary_behaviour", "T=0, sigma=0 and payoff-kink handling")
    engine = BlackScholesAnalyticEngine()
    base = PricingProblem(
        OptionType.CALL, ExerciseStyle.EUROPEAN, 100.0, 100.0, 1.0, 0.03, 0.0, 0.2
    )
    expectations = [
        ("expiry_itm_price", base.with_changes(time_to_expiry=0.0, spot=110.0), "price", 10.0),
        ("expiry_otm_price", base.with_changes(time_to_expiry=0.0, spot=90.0), "price", 0.0),
        (
            "zero_vol_itm_price",
            base.with_changes(volatility=0.0, strike=90.0),
            "price",
            100.0 - 90.0 * math.exp(-0.03),
        ),
        ("zero_vol_otm_price", base.with_changes(volatility=0.0, strike=120.0), "price", 0.0),
    ]
    for name, prob, _, expected in expectations:
        got = engine.price(prob, AnalyticConfig(), ()).price
        res.record(name, got - expected, expected, 1e-12, 1e-14)
    status_checks = [
        (
            "expiry_atm_delta",
            base.with_changes(time_to_expiry=0.0),
            GreekName.DELTA,
            GreekStatus.UNDEFINED_AT_KINK,
        ),
        (
            "expiry_theta",
            base.with_changes(time_to_expiry=0.0, spot=110.0),
            GreekName.THETA,
            GreekStatus.NOT_APPLICABLE_AT_EXPIRY,
        ),
        (
            "zero_vol_forward_atm_gamma",
            base.with_changes(volatility=0.0, strike=100.0 * math.exp(0.03)),
            GreekName.GAMMA,
            GreekStatus.UNDEFINED_AT_KINK,
        ),
        (
            "zero_vol_vega_one_sided",
            base.with_changes(volatility=0.0, strike=120.0),
            GreekName.VEGA,
            GreekStatus.ONE_SIDED,
        ),
    ]
    for name, prob, greek, status in status_checks:
        greek_result = engine.price(prob, AnalyticConfig(), (greek,)).greeks[greek]
        if greek_result.status is status:
            res.n_cases += 1
        else:
            res.fail(name, f"expected {status.value}, got {greek_result.status.value}")
    return res


# --- American exercise ---------------------------------------------------------------------------


def check_american_vs_quantlib(steps: int = 4000) -> CheckResult:
    tol = load_policy()["tolerance"]["crr_american_vs_quantlib_fd"]
    fx = load_fixture("american_quantlib_fd_v1.json")
    res = CheckResult(
        "crr_vs_quantlib_fd",
        f"CRR ({steps} steps) vs QuantLib {fx['libraries']['QuantLib']} "
        "FdBlackScholesVanillaEngine (American; escrowed cash dividends) and "
        "AnalyticDividendEuropeanEngine (European with cash dividends)",
        tolerance=tol,
    )
    for c in fx["cases"]:
        p = _problem(c, ExerciseStyle.AMERICAN)
        cid = f"{p.exercise_style.value}:{_ql_case_id(c)}:{c.get('dividend_set', 'none')}"
        got = build_tree(p, steps).price
        res.record(
            cid, got - c["price"], c["price"], tol["atol"] * _scale(c["spot"]), tol["rtol"], got=got
        )
    return res


def check_american_identities(steps: int = 1000) -> CheckResult:
    res = CheckResult(
        "american_identities",
        "American >= European on the same tree; American call == European call when q = 0, "
        "r >= 0 and no dividends; American put >= intrinsic",
    )
    for c in stress_cases():
        if c.regime not in ("ordinary", "negative_rates", "high_yield", "deep_itm_otm"):
            continue
        eu = c.problem()
        am = c.problem(ExerciseStyle.AMERICAN)
        if not 0.0 < up_probability(eu, steps) < 1.0:
            res.exclude("invalid tree probability at this step count")
            continue
        v_eu = build_tree(eu, steps).price
        v_am = build_tree(am, steps).price
        slack = 1e-10 * max(1.0, abs(v_eu))
        res.record(f"{c.case_id}:am>=eu", max(v_eu - v_am, 0.0), v_eu, slack, 0.0)
        intrinsic = max(c.option_type.sign * (c.spot - c.strike), 0.0)
        res.record(f"{c.case_id}:am>=intrinsic", max(intrinsic - v_am, 0.0), intrinsic, slack, 0.0)
        if c.option_type is OptionType.CALL and c.dividend_yield == 0.0 and c.rate >= 0.0:
            res.record(f"{c.case_id}:call_no_early_ex", v_am - v_eu, v_eu, slack, 0.0)
    return res


def fixture_hashes() -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(FIXTURE_DIR).glob("*.json"))
    }


# --- Release B: surface calibration gate ----------------------------------------------------------


def check_surface_gate() -> CheckResult:
    """Fit the committed synthetic snapshots in a scratch store and apply the policy gate."""
    gate = load_policy()["surface_gate"]
    res = CheckResult(
        "surface_calibration_gate",
        "SSVI fit on synthetic snapshots with known generating surface: held-out bid/ask "
        "containment, IV recovery vs truth, sampled static-arbitrage diagnostics, guarantee "
        "conditions",
        tolerance={k: v for k, v in gate.items() if k != "fixtures"},
    )
    fits = []
    with tempfile.TemporaryDirectory() as tmp:
        svc = SurfaceService(Path(tmp))
        root = FIXTURE_DIR.parent / "snapshots"
        for name in gate["fixtures"]:
            sid = svc.snapshots.ingest((root / name).read_bytes())[0]["snapshot_id"]
            art, _ = svc.fit(sid)
            if art["status"] != "ok":
                res.fail(name, f"fit failed: {art.get('failure_reason')}")
                continue
            cond = art["guarantee"]["conditions"]
            containment = art["held_out"]["bid_ask_containment"]
            iv_err = art["truth_recovery"]["max_abs_iv_error"]
            res.record(
                f"{name}:holdout_containment",
                max(gate["min_holdout_bid_ask_containment"] - containment, 0.0),
                1.0,
                0.0,
                0.0,
            )
            res.record(f"{name}:iv_vs_truth", iv_err, 0.0, gate["max_abs_iv_error_vs_truth"], 0.0)
            res.record(
                f"{name}:sampled_violations",
                float(art["arbitrage_diagnostics"]["sampled_violations_total"]),
                0.0,
                0.0,
                0.0,
            )
            ok = cond["eta_bound_ok"] and cond["gamma_in_(0,0.5]"] and cond["theta_non_decreasing"]
            res.record(f"{name}:guarantee_conditions", 0.0 if ok else 1.0, 0.0, 0.0, 0.0)
            fits.append(
                {
                    "fixture": name,
                    "surface": art["surface"],
                    "in_sample": art["in_sample"],
                    "held_out": art["held_out"],
                    "truth_recovery": art["truth_recovery"],
                    "arbitrage": art["arbitrage_diagnostics"],
                    "exclusions": art["exclusions"],
                }
            )
    res.details["fits"] = fits
    return res


# --- Heston --------------------------------------------------------------------------------------

HestonPriceFn = Callable[[PricingProblem, HestonConfig], float]


def heston_engine_price(problem: PricingProblem, cfg: HestonConfig) -> float:
    return heston_price(problem, cfg)[0]


def _heston_problem(c: dict[str, Any]) -> PricingProblem:
    return replace(
        _problem({**c, "vol": math.sqrt(c["heston"]["v0"])}), heston=HestonModel(**c["heston"])
    )


def check_heston_vs_quantlib(price_fn: HestonPriceFn = heston_engine_price) -> CheckResult:
    tol = load_policy()["tolerance"]["heston_vs_quantlib"]
    fx = load_fixture("heston_quantlib_v1.json")
    res = CheckResult(
        "heston_price_vs_quantlib",
        f"Heston European prices vs QuantLib {fx['libraries']['QuantLib']} AnalyticHestonEngine "
        "(4 parameter sets incl. two Feller violations, negative rates)",
        tolerance=tol,
    )
    cfg = HestonConfig()
    worst: dict[str, float] = {}
    for c in fx["cases"]:
        cid = f"{c['parameter_set']}:{c['option_type']}:K{c['strike']}:d{c['days']}:r{c['rate']}"
        got = price_fn(_heston_problem(c), cfg)
        res.record(cid, got - c["price"], c["price"], tol["atol"] * _scale(c["spot"]), tol["rtol"])
        worst[c["parameter_set"]] = max(worst.get(c["parameter_set"], 0.0), abs(got - c["price"]))
    res.details["max_abs_error_by_parameter_set"] = worst
    return res


def _heston_limit_gap(
    price_fn: HestonPriceFn,
    cfg: HestonConfig,
    v0: float,
    kappa: float,
    theta: float,
    rho: float,
    sign: float,
    K: float,
    T: float,
    s: float,
) -> float:
    """Heston price minus BSM with the deterministic-variance effective volatility."""
    m = HestonModel(v0, kappa, theta, s, rho)
    p = PricingProblem(
        OptionType.CALL if sign > 0 else OptionType.PUT,
        ExerciseStyle.EUROPEAN,
        100.0,
        K,
        T,
        0.03,
        0.01,
        math.sqrt(v0),
        heston=m,
    )
    bs = analytic_price(p.with_changes(volatility=m.effective_volatility(T), heston=None))
    return price_fn(p, cfg) - bs


def check_heston_limit(price_fn: HestonPriceFn = heston_engine_price) -> CheckResult:
    """vol-of-vol -> 0 against BSM with the deterministic integrated variance."""
    tol = load_policy()["tolerance"]["heston_deterministic_limit"]
    res = CheckResult(
        "heston_deterministic_variance_limit",
        "sigma -> 0: rho = 0 must match BSM(sqrt(I(T)/T)) at sigma = 1e-7 (second-order gap); "
        "rho != 0 must converge at first order (gap/sigma stable between 1e-5 and 1e-7)",
        tolerance=tol,
    )
    cfg = HestonConfig()
    ratios = []
    for rho in (0.0, -0.7, 0.4):
        for v0, kappa, theta in ((0.04, 1.5, 0.09), (0.09, 0.3, 0.04), (0.04, 1e-10, 0.2)):
            for sign, K, T in ((1, 80.0, 0.5), (1, 110.0, 1.0), (-1, 95.0, 2.0)):
                gap = partial(
                    _heston_limit_gap, price_fn, cfg, v0, kappa, theta, rho, float(sign), K, T
                )
                cid = f"rho{rho}:v0{v0}:k{kappa}:th{theta}:{'C' if sign > 0 else 'P'}K{K}:T{T}"
                if rho == 0.0:
                    res.record(cid, gap(1e-7), 1.0, tol["atol"], tol["rtol"])
                else:
                    r5, r7 = gap(1e-5) / 1e-5, gap(1e-7) / 1e-7
                    ratios.append(
                        {"case": cid, "gap_over_sigma_1e-5": r5, "gap_over_sigma_1e-7": r7}
                    )
                    res.record(cid + ":order", r7 - r5, r5, 1e-12, tol["first_order_ratio_rtol"])
    res.details["first_order_ratios"] = ratios[:6]
    return res


def check_heston_parity(price_fn: HestonPriceFn = heston_engine_price) -> CheckResult:
    tol = load_policy()["tolerance"]["heston_parity"]
    res = CheckResult("heston_put_call_parity", "C - P = D(F - K) under Heston", tolerance=tol)
    cfg = HestonConfig()
    for c in load_fixture("heston_quantlib_v1.json")["cases"]:
        if c["option_type"] != "call":
            continue
        call = _heston_problem(c)
        put = call.with_changes(option_type=OptionType.PUT)
        D = math.exp(-c["rate"] * c["time"])
        F = c["spot"] * math.exp((c["rate"] - c["dividend_yield"]) * c["time"])
        target = D * (F - c["strike"])
        cid = f"{c['parameter_set']}:K{c['strike']}:d{c['days']}:r{c['rate']}"
        res.record(
            cid, price_fn(call, cfg) - price_fn(put, cfg) - target, target, tol["atol"], tol["rtol"]
        )
    return res


def check_heston_integration_settings() -> CheckResult:
    """Default quadrature tolerances vs much tighter ones: an estimate of integration error."""
    tol = load_policy()["tolerance"]["heston_integration_settings"]
    res = CheckResult(
        "heston_integration_settings",
        "Prices with the default quadrature (epsabs 1e-12, epsrel 1e-10) vs a tight setting "
        "(epsabs 1e-14, epsrel 1e-13, limit 5000)",
        tolerance=tol,
    )
    default, tight = HestonConfig(), HestonConfig(epsabs=1e-14, epsrel=1e-13, limit=5000)
    for c in load_fixture("heston_quantlib_v1.json")["cases"][::3]:
        p = _heston_problem(c)
        a, b = heston_engine_price(p, default), heston_engine_price(p, tight)
        cid = f"{c['parameter_set']}:{c['option_type']}:K{c['strike']}:d{c['days']}"
        res.record(cid, a - b, b, tol["atol"], tol["rtol"])
    return res
