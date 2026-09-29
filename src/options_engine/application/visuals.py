"""Data for visualisations. Every number comes from the engines; the UI only draws.

* Value profile: price against spot at the current valuation time and at later
  valuation times (calendar roll, spot grid and volatility held), plus the
  expiry payoff. Greek profiles at the current time.
* Early-exercise boundary (American): from the CRR tree, per time step.
* Surface views: implied-vol grid over (k, T), ATM term structure, and the
  risk-neutral density of log-moneyness implied by the fitted SSVI surface,
  p(k) = g(k) / sqrt(2 pi w) * exp(-d_minus^2 / 2), d_minus = -k/sqrt(w) - sqrt(w)/2
  (Gatheral & Jacquier 2014, eq. 2.1). p >= 0 everywhere is equivalent to no
  butterfly arbitrage in that slice; its mass and mean E[F_T/F] are reported as
  checks (both should be ~1 on a wide enough grid).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from options_engine.application.pricing import PriceRequest, PricingService, resolve_problem
from options_engine.domain.conventions import (
    GREEK_DISPLAY,
    SECONDS_PER_YEAR,
    ExerciseStyle,
    GreekName,
)
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import AnalyticConfig, CRRConfig, NumericalConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.crr import build_tree, minimum_valid_steps, up_probability
from options_engine.models.ssvi import SSVISurface

PROFILE_POINTS = 61
PROFILE_ELAPSED_FRACTIONS = (0.0, 0.5, 0.9)  # share of the remaining life already elapsed
PROFILE_TREE_STEPS = 300
BOUNDARY_STEPS = 1000
BOUNDARY_MAX_POINTS = 250
PROFILE_GREEKS = (GreekName.DELTA, GreekName.GAMMA, GreekName.VEGA, GreekName.THETA)


def _profile_engine(request: PriceRequest) -> tuple[str, NumericalConfig, str]:
    """Closed form for European (exact, fast); CRR for American. Stated in the output."""
    if request.contract.exercise_style is ExerciseStyle.AMERICAN:
        cfg = CRRConfig(
            steps=PROFILE_TREE_STEPS,
            allow_step_refinement=True,
            odd_even_diagnostic=False,
            early_exercise_diagnostic=False,
        )
        return "crr_tree", cfg, f"CRR tree, {PROFILE_TREE_STEPS} steps"
    return "bsm_analytic", AnalyticConfig(), "closed-form BSM"


def spot_grid(
    spot: float, strike: float, total_vol: float, points: int = PROFILE_POINTS
) -> np.ndarray:
    """Log-spaced spots centred on the current spot (included exactly), covering the strike."""
    half_width = max(2.5 * total_vol, 0.15)
    lo, hi = spot * math.exp(-half_width), spot * math.exp(half_width)
    lo, hi = min(lo, 0.85 * strike), max(hi, 1.15 * strike)
    half = points // 2
    left = np.exp(np.linspace(math.log(lo), math.log(spot), half + 1))
    right = np.exp(np.linspace(math.log(spot), math.log(hi), half + 1))[1:]
    grid = np.concatenate([left, right])
    grid[half] = spot  # exact current spot (exp(log(spot)) can differ in the last bit)
    return grid


def value_profile(pricing: PricingService, request: PriceRequest) -> dict[str, Any]:
    problem = resolve_problem(request)
    T = problem.time_to_expiry
    sign, K = problem.option_type.sign, problem.strike
    grid = spot_grid(problem.spot, K, problem.volatility * math.sqrt(T))
    engine_id, config, engine_label = _profile_engine(request)
    horizons: list[dict[str, Any]] = []
    greeks: dict[str, list[float | None]] = {g.value: [] for g in PROFILE_GREEKS}
    units = {g.value: GREEK_DISPLAY[g][1] for g in PROFILE_GREEKS}
    fractions = PROFILE_ELAPSED_FRACTIONS if T > 0 else (0.0,)
    for f in fractions:
        valuation = replace(
            request.valuation,
            as_of=request.valuation.as_of + timedelta(seconds=f * T * SECONDS_PER_YEAR),
        )
        want = PROFILE_GREEKS if f == 0.0 else ()
        values = []
        for S in grid:
            req = replace(
                request,
                valuation=valuation,
                market=replace(request.market, spot=Decimal(repr(float(S)))),
                engine_id=engine_id if T > 0 else "bsm_analytic",
                config=config if T > 0 else AnalyticConfig(),
                greeks=want,
            )
            _, _, out = pricing.evaluate(req)
            values.append(out.price)
            for g in want:
                r = out.greeks[g]
                ok = r.status in (GreekStatus.OK, GreekStatus.ONE_SIDED) and r.value is not None
                greeks[g.value].append(
                    r.value * GREEK_DISPLAY[g][0] if ok and r.value is not None else None
                )
        remaining_days = (1.0 - f) * T * 365.0
        horizons.append({"elapsed_fraction": f, "remaining_days": remaining_days, "values": values})
    payoff = [max(sign * (float(S) - K), 0.0) for S in grid]
    return {
        "spots": grid.tolist(),
        "current_spot": problem.spot,
        "strike": K,
        "horizons": horizons,
        "payoff": payoff,
        "greeks": greeks,
        "greek_units": units,
        "engine": engine_label,
        "assumptions": (
            "Later horizons roll the valuation time forward with the spot grid, volatility, rates "
            "and dividend schedule held fixed (dividends whose ex-date passes drop out). Prices "
            "are per underlying unit; the payoff line is the value at expiry."
        ),
    }


def exercise_boundary(pricing: PricingService, request: PriceRequest) -> dict[str, Any]:
    if request.contract.exercise_style is not ExerciseStyle.AMERICAN:
        raise DomainError(
            ErrorCode.UNSUPPORTED_COMBINATION,
            "an early-exercise boundary exists only for American exercise",
        )
    problem = resolve_problem(request)
    if problem.time_to_expiry <= 0 or problem.volatility <= 0:
        raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "boundary needs T > 0 and sigma > 0")
    steps = BOUNDARY_STEPS
    if not 0.0 < up_probability(problem, steps) < 1.0:
        steps = minimum_valid_steps(problem)
        if steps > 20_000:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED, "valid tree needs more than 20,000 steps"
            )
    node_spacing = problem.volatility * math.sqrt(problem.time_to_expiry / steps)
    raw: list[tuple[float, float | None]] = []
    tree = build_tree(problem, steps, raw)
    raw.sort()
    stride = max(1, len(raw) // BOUNDARY_MAX_POINTS)
    points = [
        {"time_years": t, "days_from_now": t * 365.0, "boundary_spot": b} for t, b in raw[::stride]
    ]
    exercised_now = raw and raw[0][1] is not None
    return {
        "points": points,
        "strike": problem.strike,
        "current_spot": problem.spot,
        "option_type": problem.option_type.value,
        "steps": steps,
        "price": tree.price,
        "exercise_region": "at or below the boundary"
        if problem.option_type.sign < 0
        else "at or above the boundary",
        "no_node_exercised": all(b is None for _, b in raw),
        "exercise_optimal_now": bool(exercised_now),
        "gap_note": (
            "A missing point means no tree node at that time lies in the exercise region. Early "
            "in the tree the nodes span a narrow range of spots, so the boundary may simply lie "
            "beyond them; missing points are not evidence that exercise is never optimal."
        ),
        "resolution_note": (
            f"Read from CRR tree nodes ({steps} steps): the boundary is resolved to one node "
            f"spacing, sigma*sqrt(dt) = {node_spacing:.4f} "
            "in log-spot, so the curve is stepped."
        ),
    }


def density(surface: SSVISurface, k: np.ndarray, t: float) -> np.ndarray:
    """Risk-neutral density of k = ln(K/F) at maturity t implied by the surface."""
    w = surface.total_variance(k, t)
    sw = np.sqrt(w)
    d_minus = -k / sw - 0.5 * sw
    return np.asarray(
        surface.butterfly_density(k, t) / (math.sqrt(2 * math.pi) * sw) * np.exp(-0.5 * d_minus**2)
    )


def surface_views(surface: SSVISurface, artifact: dict[str, Any]) -> dict[str, Any]:
    rows = artifact.get("residuals") or []
    ks = [r["k"] for r in rows] or [-0.3, 0.2]
    k_lo, k_hi = min(ks) - 0.05, max(ks) + 0.05
    k_grid = np.linspace(k_lo, k_hi, 41)
    t_first, t_last = surface.expiries[0], surface.expiries[-1]
    t_grid = np.unique(
        np.concatenate([np.array(surface.expiries), np.linspace(t_first / 2, t_last, 20)])
    )
    iv = [[float(v) for v in surface.implied_vol(k_grid, t)] for t in t_grid]
    t_term = np.linspace(t_first / 4, t_last * 1.5, 80)
    term = [
        {
            "T": float(t),
            "atm_vol": float(math.sqrt(float(surface.theta(t)) / t)),
            "region": "extrapolated" if t < t_first or t > t_last else "interpolated",
        }
        for t in t_term
    ]
    k_dens = np.linspace(-1.5, 1.0, 401)
    dk = k_dens[1] - k_dens[0]
    densities = []
    for t in surface.expiries:
        p = density(surface, k_dens, t)
        densities.append(
            {
                "T": t,
                "density": [float(v) for v in p],
                "min_density": float(p.min()),
                "mass_on_grid": float(np.sum(p) * dk),
                "mean_forward_ratio_on_grid": float(np.sum(np.exp(k_dens) * p) * dk),
            }
        )
    return {
        "k_grid": k_grid.tolist(),
        "t_grid": t_grid.tolist(),
        "fitted_expiries": list(surface.expiries),
        "implied_vol": iv,
        "term_structure": term,
        "density_k": k_dens.tolist(),
        "densities": densities,
        "notes": (
            "Implied vols are evaluated from the fitted SSVI parameters; maturities between fitted "
            "expiries are interpolated in total variance, outside them extrapolated. Density: "
            "risk-neutral density of k = ln(K/F); non-negative everywhere iff no butterfly "
            "arbitrage; mass and E[F_T/F] should both be ~1."
        ),
    }
