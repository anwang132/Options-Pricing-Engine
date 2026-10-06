"""Monte Carlo on the exact terminal GBM distribution (European payoffs).

S_T = S* exp((r - q - sigma^2/2) T + sigma sqrt(T) Z), Z ~ N(0, 1). No time
stepping: the vanilla payoff depends only on S_T (ADR 0007).

Statistics:
* The unit of independent observation is one draw Z (plain) or one
  antithetic pair (Z, -Z) averaged. Standard errors are computed from those
  independent observations, never from the 2n correlated payoffs.
* Moments stream through fixed-size chunks with Chan's parallel update, so
  memory is O(chunk_size) regardless of the path count.
* Control variate (optional): X = D*S_T with known mean S* exp(-qT). The
  coefficient beta is estimated on an independent pilot stream, so the main
  estimator stays unbiased and its SE formula remains valid.
* Pathwise Greeks: delta, vega, rho differentiate the discounted payoff along
  each path (valid for the Lipschitz vanilla payoff). Gamma and theta have no
  estimator here: differentiating the payoff indicator is not a valid
  gamma estimator, and they are reported as not_supported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import special

from options_engine.domain.conventions import DividendTreatment, ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import ControlVariate, MonteCarloConfig
from options_engine.domain.results import (
    EngineOutput,
    GreekResult,
    GreekStatus,
    StochasticUncertainty,
)
from options_engine.engines.base import EngineCapabilities, PricingProblem
from options_engine.models.black_scholes import BLACK_SCHOLES

RNG_NAME = "numpy.random.PCG64"


@dataclass
class RunningMoments:
    """Streaming mean/variance for several columns (Chan et al. parallel update)."""

    count: int = 0
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    m2: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def update(self, batch: np.ndarray) -> None:
        """``batch`` has shape (n, k): n observations of k quantities."""
        n = batch.shape[0]
        if n == 0:
            return
        b_mean = batch.mean(axis=0)
        b_m2 = ((batch - b_mean) ** 2).sum(axis=0)
        if self.count == 0:
            self.count, self.mean, self.m2 = n, b_mean, b_m2
            return
        total = self.count + n
        delta = b_mean - self.mean
        self.mean = self.mean + delta * (n / total)
        self.m2 = self.m2 + b_m2 + delta**2 * (self.count * n / total)
        self.count = total

    def variance(self) -> np.ndarray:
        return self.m2 / (self.count - 1)

    def standard_error(self) -> np.ndarray:
        return np.sqrt(self.variance() / self.count)


def _observations(
    z: np.ndarray, problem: PricingProblem, s_star: float, antithetic: bool
) -> np.ndarray:
    """Per-observation columns: [payoff, control, delta_pw, vega_pw, rho_pw]."""
    T, r, q, vol = problem.time_to_expiry, problem.rate, problem.dividend_yield, problem.volatility
    sign, K = problem.option_type.sign, problem.strike
    D = math.exp(-r * T)
    sqrt_t = math.sqrt(T)
    drift = (r - q - 0.5 * vol * vol) * T
    dpv_dr = math.fsum(a * t * math.exp(-r * t) for t, a in problem.dividends)

    def columns(zz: np.ndarray) -> np.ndarray:
        s_t = s_star * np.exp(drift + vol * sqrt_t * zz)
        payoff = np.maximum(sign * (s_t - K), 0.0)
        itm = (sign * (s_t - K) > 0.0).astype(np.float64)
        ds_dspot = s_t / s_star
        delta = D * sign * itm * ds_dspot
        vega = D * sign * itm * s_t * (sqrt_t * zz - vol * T)
        # d/dr: discounting, drift, and the escrowed spot's dependence on r.
        rho = -T * D * payoff + D * sign * itm * (T * s_t + ds_dspot * dpv_dr)
        return np.asarray(np.column_stack((D * payoff, D * s_t, delta, vega, rho)), np.float64)

    if antithetic:
        return np.asarray(0.5 * (columns(z) + columns(-z)), dtype=np.float64)
    return columns(z)


def _pilot_beta(
    problem: PricingProblem, s_star: float, cfg: MonteCarloConfig, ss: np.random.SeedSequence
) -> tuple[float, float]:
    rng = np.random.Generator(np.random.PCG64(ss))
    n = cfg.pilot_paths // 2 if cfg.antithetic else cfg.pilot_paths
    obs = _observations(rng.standard_normal(n), problem, s_star, cfg.antithetic)
    y, x = obs[:, 0], obs[:, 1]
    var_x = float(np.var(x, ddof=1))
    if var_x == 0.0:
        return 0.0, 0.0
    cov = float(np.cov(y, x, ddof=1)[0, 1])
    var_y = float(np.var(y, ddof=1))
    corr = cov / math.sqrt(var_x * var_y) if var_y > 0 else 0.0
    return cov / var_x, corr


def simulate(
    problem: PricingProblem, cfg: MonteCarloConfig
) -> tuple[RunningMoments, dict[str, Any]]:
    s_star = problem.escrowed_spot()
    main_ss, pilot_ss = np.random.SeedSequence(cfg.seed).spawn(2)
    rng = np.random.Generator(np.random.PCG64(main_ss))
    draws = cfg.paths // 2 if cfg.antithetic else cfg.paths
    beta, corr = 0.0, None
    use_cv = cfg.control_variate is ControlVariate.TERMINAL_UNDERLYING
    if use_cv:
        beta, corr = _pilot_beta(problem, s_star, cfg, pilot_ss)
    control_mean = s_star * math.exp(-problem.dividend_yield * problem.time_to_expiry)
    moments = RunningMoments()
    remaining = draws
    chunk_draws = cfg.chunk_size // 2 if cfg.antithetic else cfg.chunk_size
    while remaining > 0:
        n = min(chunk_draws, remaining)
        obs = _observations(rng.standard_normal(n), problem, s_star, cfg.antithetic)
        if use_cv:
            obs[:, 0] -= beta * (obs[:, 1] - control_mean)
        moments.update(obs)
        remaining -= n
    diagnostics: dict[str, Any] = {
        "rng": RNG_NAME,
        "seed": cfg.seed,
        "seed_policy": "SeedSequence(seed).spawn(2): child 0 main stream, child 1 pilot",
        "chunk_size": cfg.chunk_size,
        "chunk_policy": "sequential draws from one stream; results independent of chunk size",
        "payoff_evaluations": cfg.paths,
        "normal_draws": draws,
        "independent_observations": moments.count,
        "antithetic": cfg.antithetic,
        "control_variate": cfg.control_variate.value,
        "control_variate_beta": beta if use_cv else None,
        "pilot_correlation": corr,
        "pilot_paths": cfg.pilot_paths if use_cv else 0,
        "sampling": "exact terminal GBM",
    }
    return moments, diagnostics


class MonteCarloTerminalEngine:
    engine_id = "mc_terminal_gbm"
    version = "1.0.0"
    description = "Monte Carlo on the exact terminal GBM distribution (European)"
    config_type: type = MonteCarloConfig
    capabilities = EngineCapabilities(
        exercise_styles=frozenset({ExerciseStyle.EUROPEAN}),
        dividend_treatments=frozenset(
            {DividendTreatment.CONTINUOUS_YIELD, DividendTreatment.ESCROWED_CASH}
        ),
        model_families=frozenset({BLACK_SCHOLES}),
        greeks={GreekName.DELTA: "pathwise", GreekName.VEGA: "pathwise", GreekName.RHO: "pathwise"},
        stochastic=True,
        batching=False,
        diagnostics=("standard_error", "confidence_interval", "control_variate_beta"),
        zero_volatility=False,
    )

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput:
        cfg: MonteCarloConfig = config
        if problem.exercise_style is not ExerciseStyle.EUROPEAN:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION, "terminal MC prices European exercise only"
            )
        if problem.volatility <= 0.0 or problem.time_to_expiry <= 0.0:
            raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "MC requires sigma > 0 and T > 0")
        moments, diagnostics = simulate(problem, cfg)
        mean, se = moments.mean, moments.standard_error()
        z = float(special.ndtri(0.5 + 0.5 * cfg.confidence_level))
        price, price_se = float(mean[0]), float(se[0])
        uncertainty = StochasticUncertainty(
            standard_error=price_se,
            confidence_level=cfg.confidence_level,
            ci_low=price - z * price_se,
            ci_high=price + z * price_se,
            independent_observations=moments.count,
        )
        column = {GreekName.DELTA: 2, GreekName.VEGA: 3, GreekName.RHO: 4}
        out: dict[GreekName, GreekResult] = {}
        for g in greeks:
            if g in column:
                c = column[g]
                out[g] = GreekResult(g, GreekStatus.OK, float(mean[c]), "pathwise", float(se[c]))
            else:
                reason = (
                    "differentiating the payoff indicator pathwise is not a valid gamma estimator"
                    if g is GreekName.GAMMA
                    else "no estimator implemented for this Greek in the terminal MC engine"
                )
                out[g] = GreekResult(g, GreekStatus.NOT_SUPPORTED, note=reason)
        return EngineOutput(price, out, diagnostics, uncertainty)
