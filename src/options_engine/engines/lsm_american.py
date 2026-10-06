"""Longstaff-Schwartz least-squares Monte Carlo for American exercise (ADR 0018).

The contract is priced as a Bermudan option exercisable at t_j = j T / M,
j = 0..M, under GBM on the escrowed spot S* (cash dividends add back the PV of
those still to be paid when computing the exercise value, as in the CRR tree).

* Paths are generated backwards with a Brownian bridge,
  W(t_j) = (j / (j+1)) W(t_{j+1}) + sqrt(dt j / (j+1)) Z_j, so memory is
  O(paths) rather than O(paths * dates) and the backward induction needs no
  stored path matrix.
* Regression set: at each date, the discounted realised cash flow of
  in-the-money paths is regressed on polynomials in S/K plus the closed-form
  European value of the remaining contract; exercise where the intrinsic value
  beats the fitted continuation value.
* Pricing set: an independent set (its own SeedSequence child) applies the
  fitted rule. Its mean is a lower bound, in expectation, on the Bermudan
  price, and its standard error is valid because the paths are i.i.d. given
  the rule. The in-sample regression-set estimate is reported, not used.
* Control variate (optional): the discounted European payoff of the same path,
  whose mean is the closed-form BSM price on S*. Beta comes from the
  regression set, so the pricing estimator stays unbiased.
* Antithetic pairs are averaged into one observation before any statistic.
* Optional Andersen-Broadie (2004) dual upper bound (ADR 0022): outer paths
  apply the fitted rule; at each in-the-money date, inner simulations estimate
  the value the rule itself achieves from there; the martingale built from those
  estimates gives U = E[max_k (h_k - M_k)] >= the Bermudan price. Together with
  the lower estimate this brackets the price, and U - L measures how far the
  fitted rule is from optimal.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import special

from options_engine.domain.conventions import DividendTreatment, ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import LSMConfig
from options_engine.domain.results import (
    EngineOutput,
    GreekResult,
    GreekStatus,
    StochasticUncertainty,
)
from options_engine.engines.base import EngineCapabilities, PricingProblem
from options_engine.engines.bsm_analytic import black_scholes_price
from options_engine.models.black_scholes import BLACK_SCHOLES

RNG_NAME = "numpy.random.PCG64"
MIN_ITM_PER_COEFFICIENT = 20  # fewer in-the-money paths than this per coefficient: no exercise


def _basis(x: np.ndarray, degree: int, european: np.ndarray) -> np.ndarray:
    """Polynomials in x = S/K plus the European value of the remaining contract (per K)."""
    return np.column_stack([np.vander(x, degree + 1, increasing=True), european]).astype(float)


def _european_over_strike(
    sign: int, x: np.ndarray, tau: float, r: float, q: float, vol: float
) -> np.ndarray:
    """Plain BSM value / K at moneyness x = S*/K (tau > 0). A regressor only, so the
    cancellation-safe kernel used for reported prices is not needed here."""
    sd = vol * math.sqrt(tau)
    d1 = (np.log(x) + (r - q) * tau) / sd + 0.5 * sd
    fwd = x * math.exp(-q * tau)
    disc = math.exp(-r * tau)
    return np.asarray(
        sign * (fwd * special.ndtr(sign * d1) - disc * special.ndtr(sign * (d1 - sd)))
    )


def _normals(rng: np.random.Generator, n: int, antithetic: bool) -> np.ndarray:
    if not antithetic:
        return rng.standard_normal(n)
    z = rng.standard_normal(n // 2)
    return np.concatenate([z, -z])


def _pair(values: np.ndarray, antithetic: bool) -> np.ndarray:
    """One independent observation per draw (antithetic pairs averaged)."""
    if not antithetic:
        return values
    half = values.size // 2
    return np.asarray(0.5 * (values[:half] + values[half:]))


def backward_pass(
    problem: PricingProblem,
    cfg: LSMConfig,
    n_paths: int,
    rng: np.random.Generator,
    coefficients: list[np.ndarray | None] | None = None,
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray | None], dict[str, Any]]:
    """Discounted cash flows at t = 0 and discounted European payoffs, per path.

    Without ``coefficients`` the pass fits them (regression set); with them it
    applies the given rule (pricing set). Exercise at t = 0 is decided by the caller.
    """
    T, vol, r, q = problem.time_to_expiry, problem.volatility, problem.rate, problem.dividend_yield
    K, sign, M = problem.strike, problem.option_type.sign, cfg.exercise_dates
    dt = T / M
    drift = r - q - 0.5 * vol * vol
    log_s0 = math.log(problem.escrowed_spot())
    has_divs = bool(problem.dividends)

    def escrowed(w: np.ndarray, t: float) -> np.ndarray:
        return np.exp(log_s0 + drift * t + vol * w)

    def dividend_pv(t: float) -> float:
        return _dividend_pv(problem, t) if has_divs else 0.0

    w = math.sqrt(T) * _normals(rng, n_paths, cfg.antithetic)
    cash = np.maximum(sign * (escrowed(w, T) - K), 0.0)
    european = cash * math.exp(-r * T)
    fitting = coefficients is None
    fitted: list[np.ndarray | None] = [None] * (M + 1)
    rule = fitted if fitting else coefficients
    assert rule is not None
    step_disc = math.exp(-r * dt)
    exercised = np.zeros(n_paths, dtype=bool)
    min_itm = MIN_ITM_PER_COEFFICIENT * (cfg.basis_degree + 2)
    for j in range(M - 1, 0, -1):
        w = (j / (j + 1)) * w + math.sqrt(dt * j / (j + 1)) * _normals(rng, n_paths, cfg.antithetic)
        cash *= step_disc
        t = j * dt
        s_star = escrowed(w, t)
        intrinsic = sign * (s_star + dividend_pv(t) - K)
        itm = np.flatnonzero(intrinsic > 0.0)
        if itm.size == 0 or (itm.size < min_itm if fitting else rule[j] is None):
            continue
        # European value of the remaining contract: the continuation value if no
        # further early exercise were allowed, a strong regressor near dividends.
        euro = _european_over_strike(sign, s_star[itm] / K, T - t, r, q, vol)
        basis = _basis(intrinsic[itm] * sign / K + 1.0, cfg.basis_degree, euro)  # x = S/K
        if fitting:
            fitted[j] = np.linalg.lstsq(basis, cash[itm], rcond=None)[0]
        beta = rule[j]
        assert beta is not None
        continuation = basis @ beta
        stop = itm[intrinsic[itm] > continuation]
        cash[stop] = intrinsic[stop]
        exercised[stop] = True
    cash *= step_disc
    stats = {
        "fraction_exercised_before_expiry": float(exercised.mean()),
        "dates_without_regression": sum(1 for j in range(1, M) if rule[j] is None),
    }
    return cash, european, fitted, stats


def _dividend_pv(problem: PricingProblem, t: float) -> float:
    return math.fsum(
        a * math.exp(-problem.rate * (td - t)) for td, a in problem.dividends if td > t
    )


def dual_upper_bound(
    problem: PricingProblem,
    cfg: LSMConfig,
    rule: list[np.ndarray | None],
    lower: float,
    lower_se: float,
    outer_ss: np.random.SeedSequence,
    inner_ss: np.random.SeedSequence,
) -> dict[str, Any]:
    """Andersen-Broadie upper bound for the Bermudan price under the fitted rule.

    With L_j the (estimated) value of following the rule from date j and C_j its
    continuation estimate, M_k = sum_{j<k} (L_{j+1} - C_j) telescopes to
    L_k - L_0 + sum_{j<k exercised} (h_j - C_j), so
    h_k - M_k = L_0 + (h_k - L_k) + sum_{j<k exercised} (C_j - h_j).
    Inner simulations are needed only at in-the-money dates. At out-of-the-money
    dates the term h_k - L_k = -C_k <= 0 is replaced by 0, which can only raise
    the bound (it stays valid, slightly conservative).
    """
    T, vol, r, q = problem.time_to_expiry, problem.volatility, problem.rate, problem.dividend_yield
    K, sign, M = problem.strike, problem.option_type.sign, cfg.exercise_dates
    dt = T / M
    drift = r - q - 0.5 * vol * vol
    step_sd = vol * math.sqrt(dt)
    n_out, n_in = cfg.outer_paths, cfg.inner_paths
    t = np.arange(M + 1) * dt
    pv = np.array([_dividend_pv(problem, float(x)) for x in t])
    disc = np.exp(-r * t)

    rng_out = np.random.Generator(np.random.PCG64(outer_ss))
    log_s = math.log(problem.escrowed_spot()) + np.concatenate(
        [
            np.zeros((n_out, 1)),
            np.cumsum(drift * dt + step_sd * rng_out.standard_normal((n_out, M)), axis=1),
        ],
        axis=1,
    )
    s_star = np.exp(log_s)
    intrinsic = sign * (s_star + pv - K)
    itm = intrinsic > 0.0
    h = np.maximum(intrinsic, 0.0) * disc  # discounted to t = 0

    def continuation(j: int, s: np.ndarray) -> np.ndarray:
        beta = rule[j]
        assert beta is not None
        x = (s + pv[j]) / K
        euro = _european_over_strike(sign, s / K, T - t[j], r, q, vol)
        return np.asarray(_basis(x, cfg.basis_degree, euro) @ beta)

    exercise = np.zeros((n_out, M + 1), dtype=bool)
    exercise[:, M] = itm[:, M]
    for j in range(1, M):
        if rule[j] is None:
            continue
        rows = np.flatnonzero(itm[:, j])
        if rows.size:
            cont = continuation(j, s_star[rows, j])
            exercise[rows, j] = intrinsic[rows, j] > cont

    # Inner estimates of the rule's value from each in-the-money (path, date), t = 0 money.
    rng_in = np.random.Generator(np.random.PCG64(inner_ss))
    c_hat = np.zeros((n_out, M + 1))
    chunk = max(1, 2_000_000 // n_in)
    inner_steps = 0
    beta_by_date: list[float] = []
    for j in range(1, M):
        rows_all = np.flatnonzero(itm[:, j])
        values, controls, means = [], [], []
        for start in range(0, rows_all.size, chunk):
            rows = rows_all[start : start + chunk]
            s = np.repeat(s_star[rows, j][:, None], n_in, axis=1)
            value = np.zeros_like(s)
            alive = np.ones(s.shape, dtype=bool)
            for m in range(j + 1, M + 1):
                s = s * np.exp(drift * dt + step_sd * rng_in.standard_normal(s.shape))
                inner_steps += s.size
                intr = sign * (s + pv[m] - K)
                cand = alive & (intr > 0.0)
                if m == M:
                    value[cand] = intr[cand] * disc[m]
                    break
                if rule[m] is None or not cand.any():
                    continue
                cont = np.full(s.shape, np.inf)
                cont[cand] = continuation(m, s[cand])
                stop = cand & (intr > cont)
                value[stop] = intr[stop] * disc[m]
                alive &= ~stop
            # Control: the discounted European payoff of the same inner path, whose
            # conditional mean is the closed-form price from (S*_j, t_j).
            values.append(value)
            controls.append(np.maximum(sign * (s - K), 0.0) * disc[M])
            means.append(
                K * disc[j] * _european_over_strike(sign, s_star[rows, j] / K, T - t[j], r, q, vol)
            )
        if not values:
            beta_by_date.append(0.0)
            continue
        y, x = np.concatenate(values), np.concatenate(controls)
        xc = x - x.mean(axis=1, keepdims=True)
        var_x = float((xc * xc).sum())
        beta = float((xc * (y - y.mean(axis=1, keepdims=True))).sum()) / var_x if var_x else 0.0
        beta_by_date.append(beta)
        c_hat[rows_all, j] = y.mean(axis=1) - beta * (x.mean(axis=1) - np.concatenate(means))

    terms = np.where(itm & ~exercise, h - c_hat, 0.0)  # h_k - L_k (0 when exercised or OTM)
    terms[:, 0] = 0.0
    gains = np.where(exercise, c_hat - h, 0.0)
    gains[:, M] = 0.0
    running = np.concatenate([np.zeros((n_out, 1)), np.cumsum(gains[:, :-1], axis=1)], axis=1)
    g = np.max((terms + running)[:, 1:], axis=1)
    per_path = np.maximum(h[:, 0], lower + g)
    upper = float(per_path.mean())
    upper_se = math.sqrt(float(per_path.var(ddof=1)) / n_out + lower_se**2)
    z = float(special.ndtri(0.5 + 0.5 * cfg.confidence_level))
    return {
        "upper_bound": upper,
        "upper_bound_se": upper_se,
        "duality_gap": upper - lower,
        "bermudan_interval": [lower - z * lower_se, upper + z * upper_se],
        "interval_confidence_note": f"lower estimate - {z:.3g} SE to upper bound + {z:.3g} SE",
        "outer_paths": n_out,
        "inner_paths": n_in,
        "inner_path_steps": inner_steps,
        "upper_bound_method": "Andersen-Broadie (2004) duality with inner simulation at "
        "in-the-money dates; out-of-the-money terms bounded by 0 (conservative); inner "
        "estimates use the European payoff as a control variate (pooled beta per date)",
        "inner_control_variate_beta_range": [
            min(beta_by_date, default=0.0),
            max(beta_by_date, default=0.0),
        ],
    }


def lsm_price(
    problem: PricingProblem, cfg: LSMConfig
) -> tuple[float, StochasticUncertainty | None, dict[str, Any]]:
    reg_ss, price_ss, outer_ss, inner_ss = np.random.SeedSequence(cfg.seed).spawn(4)
    reg_rng = np.random.Generator(np.random.PCG64(reg_ss))
    y_reg, x_reg, coefficients, reg_stats = backward_pass(
        problem, cfg, cfg.regression_paths, reg_rng
    )
    y_reg, x_reg = _pair(y_reg, cfg.antithetic), _pair(x_reg, cfg.antithetic)
    in_sample = float(y_reg.mean())

    sign, K = problem.option_type.sign, problem.strike
    s_star = problem.escrowed_spot()
    T, r, q, vol = problem.time_to_expiry, problem.rate, problem.dividend_yield, problem.volatility
    european_mean = float(black_scholes_price(sign, s_star, K, T, r, q, vol)[0][()])
    beta = 0.0
    if cfg.control_variate:
        var_x = float(np.var(x_reg, ddof=1))
        beta = float(np.cov(y_reg, x_reg, ddof=1)[0, 1]) / var_x if var_x > 0 else 0.0

    diagnostics: dict[str, Any] = {
        "rng": RNG_NAME,
        "seed": cfg.seed,
        "seed_policy": "SeedSequence(seed).spawn(4): regression set, pricing set, and the "
        "upper bound's outer and inner paths",
        "exercise_dates": cfg.exercise_dates,
        "basis": f"polynomials in S/K up to degree {cfg.basis_degree} plus the European value "
        "of the remaining contract; in-the-money paths only",
        "regression_paths": cfg.regression_paths,
        "pricing_paths": cfg.paths,
        "antithetic": cfg.antithetic,
        "control_variate": "discounted European payoff" if cfg.control_variate else "none",
        "control_variate_beta": beta if cfg.control_variate else None,
        "in_sample_estimate": in_sample,
        "dates_without_regression": reg_stats["dates_without_regression"],
        "european_closed_form": european_mean,
        "path_generation": "Brownian bridge, backwards in time (O(paths) memory)",
        "bias_note": "low-biased for the Bermudan price on these dates (suboptimal fitted rule); "
        "the Bermudan price is itself at most the American price",
    }
    intrinsic_now = sign * (problem.spot - K)
    if intrinsic_now > 0.0 and intrinsic_now >= in_sample:
        diagnostics.update(exercise_now=True, fraction_exercised_before_expiry=1.0)
        if cfg.upper_bound:
            diagnostics["upper_bound_note"] = "not computed: the rule exercises immediately"
        return intrinsic_now, None, diagnostics

    price_rng = np.random.Generator(np.random.PCG64(price_ss))
    y, x, _, stats = backward_pass(problem, cfg, cfg.paths, price_rng, coefficients)
    y, x = _pair(y, cfg.antithetic), _pair(x, cfg.antithetic)
    raw_var = float(np.var(y, ddof=1))
    if cfg.control_variate:
        y = y - beta * (x - european_mean)
    n = y.size
    price = float(y.mean())
    se = float(np.std(y, ddof=1) / math.sqrt(n))
    z = float(special.ndtri(0.5 + 0.5 * cfg.confidence_level))
    cv_var = float(np.var(y, ddof=1))
    diagnostics.update(
        exercise_now=False,
        fraction_exercised_before_expiry=stats["fraction_exercised_before_expiry"],
        independent_observations=n,
        variance_reduction_factor=raw_var / cv_var if cfg.control_variate and cv_var > 0 else None,
        early_exercise_premium_estimate=price - european_mean,
    )
    if cfg.upper_bound:
        diagnostics.update(
            dual_upper_bound(problem, cfg, coefficients, price, se, outer_ss, inner_ss)
        )
    return (
        price,
        StochasticUncertainty(se, cfg.confidence_level, price - z * se, price + z * se, n),
        diagnostics,
    )


class LongstaffSchwartzEngine:
    engine_id = "lsm_american"
    version = "1.0.0"
    description = "Longstaff-Schwartz least-squares Monte Carlo (American, as Bermudan)"
    config_type: type = LSMConfig
    capabilities = EngineCapabilities(
        exercise_styles=frozenset({ExerciseStyle.AMERICAN}),
        dividend_treatments=frozenset(
            {DividendTreatment.CONTINUOUS_YIELD, DividendTreatment.ESCROWED_CASH}
        ),
        model_families=frozenset({BLACK_SCHOLES}),
        greeks={},
        stochastic=True,
        batching=False,
        diagnostics=("standard_error", "in_sample_estimate", "variance_reduction_factor"),
        zero_volatility=False,
    )

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput:
        cfg: LSMConfig = config
        if problem.exercise_style is not ExerciseStyle.AMERICAN:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION,
                "Longstaff-Schwartz is for early exercise; use a European engine",
            )
        if problem.volatility <= 0.0 or problem.time_to_expiry <= 0.0:
            raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "LSM requires sigma > 0 and T > 0")
        price, uncertainty, diagnostics = lsm_price(problem, cfg)
        note = (
            "not estimated by the LSM engine (regression Greeks are biased and noisy); "
            "use crr_tree for American Greeks"
        )
        out = {g: GreekResult(g, GreekStatus.NOT_SUPPORTED, note=note) for g in greeks}
        return EngineOutput(price, out, diagnostics, uncertainty)
