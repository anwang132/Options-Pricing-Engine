"""Heston European pricing by Lewis's Fourier integral (ADR 0017).

Price (per unit), with D = e^{-rT}, F = S e^{(r-q)T}, x = ln(F/K):

    C = D * ( F - sqrt(F K)/pi * int_0^inf Re[e^{i u x} phi(u - i/2)] / (u^2 + 1/4) du ),
    P = C - D (F - K),

where phi is the characteristic function of ln(S_T / F).

Characteristic function: Albrecher et al.'s "little Heston trap" form, which
keeps the complex logarithm on its principal branch, rearranged so nothing is
divided by sigma^2. With a = u^2 + i u, xi = kappa - sigma rho i u,
d = sqrt(xi^2 + sigma^2 a) and g = (xi - d)/(xi + d) = -sigma^2 a/(xi + d)^2:

    D(u) = -a/(xi + d) * (1 - e^{-dT}) / (1 - g e^{-dT})
    C(u) = kappa theta * ( -a T/(xi + d) - 2 * log1p(z)/z * z/sigma^2 ),
           z = g (1 - e^{-dT}) / (1 - g),   z/sigma^2 = -a (1 - e^{-dT}) / ((xi + d)^2 (1 - g))

At sigma = 0 this reduces exactly to the Black-Scholes characteristic function
with integrated variance I(T); sigma = 0 itself is priced by closed-form BSM
with vol sqrt(I(T)/T), and the vol-of-vol -> 0 limit is tested against it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import integrate

from options_engine.domain.conventions import DividendTreatment, ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import HestonConfig
from options_engine.domain.results import EngineOutput, GreekResult, GreekStatus
from options_engine.engines.base import EngineCapabilities, PricingProblem
from options_engine.engines.bsm_analytic import black_scholes_price
from options_engine.models.heston import HESTON, HestonModel, HestonTSModel


def _log1p_over(z: np.ndarray) -> np.ndarray:
    """log(1 + z) / z for complex z, accurate as z -> 0."""
    small = np.abs(z) < 1e-4
    out = np.empty_like(z)
    zs = z[small]
    out[small] = 1.0 - zs / 2.0 + zs * zs / 3.0 - zs**3 / 4.0
    zb = z[~small]
    out[~small] = np.log1p(zb) / zb
    return out


def _riccati(
    w: np.ndarray, T: float, kappa: float, sigma: float, rho: float
) -> tuple[np.ndarray, np.ndarray]:
    """(integral_0^T D(u, s) ds, D(u, T)) for complex w, vectorised; theta-free."""
    w = np.asarray(w, dtype=np.complex128)
    a = w * w + 1j * w
    xi = kappa - sigma * rho * 1j * w
    d = np.sqrt(xi * xi + sigma * sigma * a)  # principal root: Re(d) >= 0
    xpd = xi + d
    one_minus_e = -np.expm1(-d * T)
    e = 1.0 - one_minus_e
    g = -sigma * sigma * a / (xpd * xpd)
    d_term = -a / xpd * one_minus_e / (1.0 - g * e)
    z = g * one_minus_e / (1.0 - g)
    z_over_s2 = -a * one_minus_e / (xpd * xpd * (1.0 - g))
    int_d = -a * T / xpd - 2.0 * _log1p_over(z) * z_over_s2
    return np.asarray(int_d), np.asarray(d_term)


def characteristic_coefficients(
    w: np.ndarray, T: float, m: HestonModel | HestonTSModel
) -> tuple[np.ndarray, np.ndarray]:
    """(C, D) with log E[exp(i w ln(S_T/F)) | v] = C + D * v, for complex w (vectorised).

    The split lets one evaluation on a node set serve every variance state v. With a
    piecewise-constant theta(t), C(T) = kappa * sum_i theta_i * (I_D(T - a_i) - I_D(T - b_i))
    over the buckets [a_i, b_i] of [0, T], where I_D(tau) = integral_0^tau D.
    """
    int_d, d_term = _riccati(w, T, m.kappa, m.sigma, m.rho)
    if isinstance(m, HestonModel):
        return np.asarray(m.kappa * m.theta * int_d), d_term
    c_term = np.zeros_like(int_d)
    for a, b, th in m.buckets(T):
        upper = int_d if a == 0.0 else _riccati(w, T - a, m.kappa, m.sigma, m.rho)[0]
        lower = 0.0 if b >= T else _riccati(w, T - b, m.kappa, m.sigma, m.rho)[0]
        c_term = c_term + th * (upper - lower)
    return np.asarray(m.kappa * c_term), d_term


def log_characteristic(w: np.ndarray, T: float, m: HestonModel) -> np.ndarray:
    """log E[exp(i w ln(S_T/F))] for complex w at the model's initial variance v0."""
    c_term, d_term = characteristic_coefficients(w, T, m)
    return np.asarray(c_term + d_term * m.v0)


def heston_call(
    F: float, K: float, D: float, T: float, m: HestonModel, cfg: HestonConfig
) -> tuple[float, dict[str, Any]]:
    x = math.log(F / K)

    def integrand(u: float) -> float:
        w = np.array([u - 0.5j])
        value = np.exp(1j * u * x + log_characteristic(w, T, m))[0]
        return float(value.real) / (u * u + 0.25)

    integral, abserr, info = integrate.quad(
        integrand,
        0.0,
        math.inf,
        epsabs=cfg.epsabs,
        epsrel=cfg.epsrel,
        limit=cfg.limit,
        full_output=True,
    )[:3]
    price = D * (F - math.sqrt(F * K) / math.pi * integral)
    return price, {
        "integral_abs_error_estimate": float(abserr),
        "integrand_evaluations": int(info["neval"]),
        "price_error_bound_from_quadrature": D * math.sqrt(F * K) / math.pi * float(abserr),
    }


def heston_price(problem: PricingProblem, cfg: HestonConfig) -> tuple[float, dict[str, Any]]:
    """Per-unit European price under the problem's Heston parameters."""
    m = problem.heston
    if m is None:
        raise DomainError(
            ErrorCode.UNSUPPORTED_COMBINATION, "Heston engine needs Heston model parameters"
        )
    S, K, T = problem.spot, problem.strike, problem.time_to_expiry
    r, q, sign = problem.rate, problem.dividend_yield, problem.option_type.sign
    if T == 0.0:
        return max(sign * (S - K), 0.0), {"regime": "at_expiry"}
    D = math.exp(-r * T)
    F = S * math.exp((r - q) * T)
    if m.sigma == 0.0:
        vol = m.effective_volatility(T)
        price = float(black_scholes_price(sign, S, K, T, r, q, vol)[0][()])
        return price, {"regime": "deterministic_variance", "effective_volatility": vol}
    call, diag = heston_call(F, K, D, T, m, cfg)
    price = call if sign > 0 else call - D * (F - K)
    if not math.isfinite(price):
        raise DomainError(ErrorCode.NUMERICAL_FAILURE, "non-finite Heston price")
    lower = D * max(sign * (F - K), 0.0)
    if price < lower - max(1e-10, 10 * diag["price_error_bound_from_quadrature"]):
        raise DomainError(
            ErrorCode.NUMERICAL_FAILURE,
            "Heston price violates the no-arbitrage lower bound beyond quadrature error",
            {"price": price, "lower_bound": lower, **diag},
        )
    diag["regime"] = "fourier"
    return price, diag


class HestonFourierEngine:
    engine_id = "heston_fourier"
    version = "1.0.0"
    description = "Heston stochastic volatility, Lewis Fourier integral (European)"
    config_type: type = HestonConfig
    capabilities = EngineCapabilities(
        exercise_styles=frozenset({ExerciseStyle.EUROPEAN}),
        dividend_treatments=frozenset({DividendTreatment.CONTINUOUS_YIELD}),
        model_families=frozenset({HESTON}),
        greeks={
            GreekName.DELTA: "central_bump_reprice",
            GreekName.GAMMA: "central_bump_reprice",
            GreekName.THETA: "central_bump_reprice",
            GreekName.RHO: "central_bump_reprice",
            GreekName.DIVIDEND_RHO: "central_bump_reprice",
        },
        stochastic=False,
        batching=False,
        diagnostics=("integral_abs_error_estimate", "feller_satisfied", "effective_volatility"),
        zero_volatility=True,
    )

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput:
        cfg: HestonConfig = config
        if problem.exercise_style is not ExerciseStyle.EUROPEAN:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION, "Heston Fourier engine is European only"
            )
        m = problem.heston
        if m is None:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION, "Heston engine needs Heston model parameters"
            )
        price, diag = heston_price(problem, cfg)
        T = problem.time_to_expiry
        diag.update(
            {
                "feller_satisfied": m.feller_satisfied,
                "feller_ratio_2kappa_theta_over_sigma2": (2 * m.kappa * m.theta / m.sigma**2)
                if m.sigma > 0
                else None,
                "effective_volatility": m.effective_volatility(T) if T > 0 else None,
                "characteristic_function": "Albrecher et al. little-trap form, "
                "sigma^2-free rearrangement",
                "integration": f"scipy quad on [0, inf), epsabs={cfg.epsabs}, epsrel={cfg.epsrel}",
            }
        )
        out = {g: self._greek(g, problem, cfg, price) for g in greeks}
        return EngineOutput(price, out, diag)

    def _greek(
        self, name: GreekName, problem: PricingProblem, cfg: HestonConfig, base: float
    ) -> GreekResult:
        method = "central_bump_reprice"
        if name is GreekName.VEGA:
            return GreekResult(
                name,
                GreekStatus.NOT_SUPPORTED,
                note="Heston has no single volatility parameter; sensitivities to v0, theta, "
                "sigma are model parameters, not a BSM vega",
            )
        T = problem.time_to_expiry
        if T == 0.0:
            if name is GreekName.THETA:
                return GreekResult(name, GreekStatus.NOT_APPLICABLE_AT_EXPIRY, note="at expiry")
            return GreekResult(name, GreekStatus.NOT_SUPPORTED, note="at expiry: use the payoff")

        def p(**changes: Any) -> float:
            return heston_price(problem.with_changes(**changes), cfg)[0]

        S = problem.spot
        if name in (GreekName.DELTA, GreekName.GAMMA):
            h = 1e-3 * S
            up, down = p(spot=S + h), p(spot=S - h)
            if name is GreekName.DELTA:
                return GreekResult(
                    name, GreekStatus.OK, (up - down) / (2 * h), method, note=f"bump {h:g}"
                )
            return GreekResult(
                name, GreekStatus.OK, (up - 2 * base + down) / (h * h), method, note=f"bump {h:g}"
            )
        if name is GreekName.THETA:
            dt = min(1e-4, 0.5 * T)
            # valuation time forward = maturity shorter
            value = (p(time_to_expiry=T - dt) - p(time_to_expiry=T + dt)) / (2 * dt)
            return GreekResult(name, GreekStatus.OK, value, method, note=f"bump {dt:g} years")
        field = "rate" if name is GreekName.RHO else "dividend_yield"
        h = 1e-5
        x = getattr(problem, field)
        value = (p(**{field: x + h}) - p(**{field: x - h})) / (2 * h)
        return GreekResult(name, GreekStatus.OK, value, method, note=f"bump {h:g}")
