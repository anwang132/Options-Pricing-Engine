"""Checks for the vectorised Heston pricer (policy ``[heston_batch]``, ADR 0019)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from options_engine.domain.numerics import HestonConfig
from options_engine.engines.base import PricingProblem
from options_engine.engines.heston_batch import heston_batch
from options_engine.engines.heston_fourier import heston_call, heston_price
from options_engine.models.heston import HestonModel
from options_engine.validation.checks import CheckResult, _heston_problem, _scale, load_fixture
from options_engine.validation.policy import load_policy

TIGHT = HestonConfig(epsabs=1e-13, epsrel=1e-12, limit=5000)


def _batch_price(p: PricingProblem) -> tuple[float, float, float]:
    """(price, dP/dS, dP/dv) of one problem through the batch pricer."""
    m = p.heston
    assert m is not None
    T = p.time_to_expiry
    D = math.exp(-p.rate * T)
    F = p.spot * math.exp((p.rate - p.dividend_yield) * T)
    thr = load_policy()["heston_batch"]["fourier_min_total_variance"]
    r = heston_batch(p.option_type.sign, F, p.strike, D, T, m.v0, m, min_total_variance=thr)
    return float(r.price), float(r.d_forward) * F / p.spot, float(r.d_variance)


def check_heston_batch_vs_quantlib() -> CheckResult:
    tol = load_policy()["tolerance"]["heston_batch_vs_quantlib"]
    fx = load_fixture("heston_quantlib_v1.json")
    res = CheckResult(
        "heston_batch_vs_quantlib",
        "Vectorised Heston pricer (adaptive composite Gauss-Legendre) vs QuantLib "
        f"{fx['libraries']['QuantLib']} AnalyticHestonEngine, all fixture cases",
        tolerance=tol,
    )
    for c in fx["cases"]:
        cid = f"{c['parameter_set']}:{c['option_type']}:K{c['strike']}:d{c['days']}:r{c['rate']}"
        got = _batch_price(_heston_problem(c))[0]
        res.record(cid, got - c["price"], c["price"], tol["atol"] * _scale(c["spot"]), tol["rtol"])
    return res


def check_heston_batch_sweep() -> CheckResult:
    pol = load_policy()
    sw = pol["tolerance"]["heston_batch_vs_adaptive_sweep"]
    hb = pol["heston_batch"]
    sets = pol["heston_parameter_sets"]
    res = CheckResult(
        "heston_batch_vs_adaptive_sweep",
        "Vectorised vs adaptive Heston pricer over maturity x variance state x moneyness "
        "(3 parameter sets); Fourier-routed points gated, deterministic-variance-limit points "
        "reported",
        tolerance={k: sw[k] for k in ("atol", "rtol")} | {"routing": hb},
    )
    limit_err, limit_n = 0.0, 0
    for name in sw["parameter_sets"]:
        _, kappa, theta, sigma, rho = sets[name]
        for tau in sw["tau"]:
            for v in sw["v"]:
                m = HestonModel(v, kappa, theta, sigma, rho)
                sd = math.sqrt(m.integrated_variance(tau, v))
                for x in np.linspace(-8 * sd, 8 * sd, sw["x_points"]):
                    F, K, D = 100.0, 100.0 * math.exp(-x), math.exp(-0.03 * tau)
                    r = heston_batch(
                        1.0,
                        F,
                        K,
                        D,
                        tau,
                        v,
                        m,
                        hb["fourier_min_total_variance"],
                        hb["max_abs_x_in_sd"],
                    )
                    ref = heston_call(F, K, D, tau, m, TIGHT)[0]
                    err = float(r.price) - ref
                    if bool(r.fourier):
                        res.record(
                            f"{name}:tau{tau}:v{v}:x{x:.4f}", err, ref, sw["atol"], sw["rtol"]
                        )
                    else:
                        limit_n += 1
                        limit_err = max(limit_err, abs(err))
    res.details = {
        "points_routed_to_deterministic_variance_limit": limit_n,
        "max_abs_error_in_limit_region": limit_err,
    }
    return res


def check_heston_batch_greeks() -> CheckResult:
    tol = load_policy()["tolerance"]["heston_batch_greeks"]
    fx = load_fixture("heston_quantlib_v1.json")
    res = CheckResult(
        "heston_batch_greeks_vs_fd",
        "Analytic dP/dS and dP/dv of the vectorised pricer vs central differences of the "
        "adaptive pricer (every 4th fixture case)",
        tolerance=tol,
    )
    for c in fx["cases"][::4]:
        p = _heston_problem(c)
        m = p.heston
        assert m is not None
        cid = f"{c['parameter_set']}:{c['option_type']}:K{c['strike']}:d{c['days']}"
        _, delta, dv = _batch_price(p)
        h = 1e-4 * p.spot
        fd_delta = (
            heston_price(p.with_changes(spot=p.spot + h), TIGHT)[0]
            - heston_price(p.with_changes(spot=p.spot - h), TIGHT)[0]
        ) / (2 * h)
        hv = 1e-5 * max(m.v0, 1e-3)
        up = replace(p, heston=replace(m, v0=m.v0 + hv))
        down = replace(p, heston=replace(m, v0=m.v0 - hv))
        fd_dv = (heston_price(up, TIGHT)[0] - heston_price(down, TIGHT)[0]) / (2 * hv)
        res.record(f"{cid}:delta", delta - fd_delta, fd_delta, tol["atol"], tol["rtol"])
        res.record(f"{cid}:dP/dv", dv - fd_dv, fd_dv, tol["atol"] * _scale(p.spot), tol["rtol"])
    return res
