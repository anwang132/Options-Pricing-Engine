"""Discrete delta-hedging experiments (ADR 0021).

A trader sells one European option at t = 0 and hedges it in the underlying at
N equally spaced times; dividends are reinvested and cash earns r. All P&L is
discounted to t = 0:

    P&L = V0 - e^{-rT} payoff + sum_i Delta_i (e^{-r t_(i+1)} S_(i+1) e^{q dt} - e^{-r t_i} S_i)

Under the model the option was priced in, with continuous rebalancing, P&L = 0.
What remains measures discrete rebalancing, model misspecification and
unhedgeable risk.

Paths: GBM sampled exactly; Heston by Andersen's (2008) quadratic-exponential
scheme for the variance with his central discretisation of log S. Paths are
simulated once on the finest grid and every coarser rebalancing frequency uses a
subset of the same points (common random numbers across N).

Strategies:
* ``bsm``: Black-Scholes delta at a fixed hedge volatility (sticky implied vol);
* ``heston``: the Heston delta dC/dS at the path's current variance;
* ``heston_mv``: the minimum-variance delta dC/dS + (rho sigma / S) dC/dv, which
  also hedges the part of the variance move that is correlated with spot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import special

from options_engine.domain.conventions import OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import HestonConfig
from options_engine.engines.heston_batch import heston_batch
from options_engine.engines.heston_fourier import heston_call
from options_engine.models.heston import HestonModel, HestonTSModel

MAX_PATH_STEPS = 20_000_000  # paths * finest grid steps (simulation memory/time)
MAX_HESTON_DELTA_EVALUATIONS = 2_000_000  # paths * sum(N) for Fourier-delta strategies
QE_PSI_CRITICAL = 1.5
STRATEGIES = ("bsm", "heston", "heston_mv")


@dataclass(frozen=True, slots=True)
class HedgeSetup:
    option_type: OptionType
    spot: float
    strike: float
    time: float
    rate: float
    dividend_yield: float
    paths: int
    rebalances: tuple[int, ...]
    seed: int
    real_vol: float | None = None  # GBM world
    heston: HestonModel | None = None  # Heston world
    hedge_vol: float | None = None  # for "bsm"; default: the model's own price/implied vol
    strategies: tuple[str, ...] = ("bsm",)
    drift: float | None = None  # real-world drift of S; default r (risk-neutral)
    heston_steps_per_year: int = 200
    histogram_bins: int = 40

    def __post_init__(self) -> None:
        if (self.real_vol is None) == (self.heston is None):
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "give exactly one of real_vol (GBM) or heston"
            )
        if self.time <= 0 or self.spot <= 0 or self.strike <= 0:
            raise DomainError(ErrorCode.INVALID_REQUEST, "spot, strike and time must be > 0")
        if not self.rebalances or min(self.rebalances) < 1:
            raise DomainError(ErrorCode.INVALID_REQUEST, "rebalances must be positive integers")
        finest = max(self.rebalances)
        if any(finest % n for n in self.rebalances):
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                "every rebalancing count must divide the largest (paths share one grid)",
            )
        unknown = set(self.strategies) - set(STRATEGIES)
        if unknown or not self.strategies:
            raise DomainError(ErrorCode.INVALID_REQUEST, f"unknown strategies {sorted(unknown)}")
        if self.heston is None and set(self.strategies) - {"bsm"}:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION, "Heston deltas need a Heston world"
            )
        if self.paths < 100 or self.paths * self.grid_steps > MAX_PATH_STEPS:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED,
                f"paths * grid steps must be in [100 * steps, {MAX_PATH_STEPS:,}]",
            )
        fourier = [s for s in self.strategies if s != "bsm"]
        if fourier and self.paths * sum(self.rebalances) * len(fourier) > (
            MAX_HESTON_DELTA_EVALUATIONS
        ):
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED,
                f"paths * sum(rebalances) * Heston strategies exceeds "
                f"{MAX_HESTON_DELTA_EVALUATIONS:,}",
            )

    @property
    def substeps(self) -> int:
        if self.heston is None:
            return 1
        return max(1, math.ceil(self.heston_steps_per_year * self.time / max(self.rebalances)))

    @property
    def grid_steps(self) -> int:
        return max(self.rebalances) * self.substeps


@dataclass
class Paths:
    times: np.ndarray  # (steps + 1,)
    spot: np.ndarray  # (paths, steps + 1)
    variance: np.ndarray | None = None  # (paths, steps + 1) in the Heston world
    info: dict[str, Any] = field(default_factory=dict)


def gbm_paths(
    s0: float,
    drift: float,
    q: float,
    vol: float,
    T: float,
    steps: int,
    paths: int,
    rng: np.random.Generator,
) -> Paths:
    dt = T / steps
    z = rng.standard_normal((paths, steps))
    log_inc = (drift - q - 0.5 * vol * vol) * dt + vol * math.sqrt(dt) * z
    log_s = np.concatenate([np.zeros((paths, 1)), np.cumsum(log_inc, axis=1)], axis=1)
    return Paths(
        np.linspace(0.0, T, steps + 1), s0 * np.exp(log_s), info={"scheme": "exact GBM increments"}
    )


def heston_qe_paths(
    s0: float,
    drift: float,
    q: float,
    m: HestonModel | HestonTSModel,
    T: float,
    steps: int,
    paths: int,
    rng: np.random.Generator,
    times: np.ndarray | None = None,
    keep_paths: bool = True,
) -> Paths:
    """Andersen (2008) QE variance scheme with the central log-S discretisation.

    ``times`` (optional) is an explicit grid from 0 to T, e.g. one containing the pillars of
    a term-structure model; theta is taken at each step's start (exact within a bucket).
    With ``keep_paths=False`` only the start and end of each path are returned (memory
    O(paths) instead of O(paths * steps)).
    """
    if m.kappa <= 0 or m.sigma <= 0:
        raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "QE needs kappa > 0 and sigma > 0")
    grid = np.linspace(0.0, T, steps + 1) if times is None else np.asarray(times, float)
    k, s, rho = m.kappa, m.sigma, m.rho
    v = np.full(paths, m.v0)
    x = np.zeros(paths)
    n = grid.size - 1
    cols = n + 1 if keep_paths else 2
    V = np.empty((paths, cols))
    X = np.empty((paths, cols))
    V[:, 0], X[:, 0] = v, x
    g1 = g2 = 0.5
    for i in range(n):
        dt = float(grid[i + 1] - grid[i])
        th = m.theta_at(float(grid[i]))
        e = math.exp(-k * dt)
        c1 = s * s * e * (1 - e) / k
        c2 = th * s * s * (1 - e) ** 2 / (2 * k)
        k0 = -rho * k * th * dt / s
        k1 = g1 * dt * (k * rho / s - 0.5) - rho / s
        k2 = g2 * dt * (k * rho / s - 0.5) + rho / s
        k3 = g1 * dt * (1 - rho * rho)
        k4 = g2 * dt * (1 - rho * rho)
        mean = th + (v - th) * e
        var = v * c1 + c2
        psi = var / (mean * mean)
        u = rng.uniform(size=paths)
        zv = special.ndtri(np.clip(u, 1e-300, 1 - 1e-16))
        v_next = np.empty(paths)
        quad = psi <= QE_PSI_CRITICAL
        if quad.any():
            inv = 2.0 / psi[quad]
            b2 = inv - 1 + np.sqrt(inv) * np.sqrt(inv - 1)
            a = mean[quad] / (1 + b2)
            v_next[quad] = a * (np.sqrt(b2) + zv[quad]) ** 2
        exp_ = ~quad
        if exp_.any():
            p = (psi[exp_] - 1) / (psi[exp_] + 1)
            beta = (1 - p) / mean[exp_]
            ue = u[exp_]
            v_next[exp_] = np.where(
                ue <= p, 0.0, np.log((1 - p) / np.maximum(1 - ue, 1e-300)) / beta
            )
        zs = rng.standard_normal(paths)
        x = (
            x
            + (drift - q) * dt
            + k0
            + k1 * v
            + k2 * v_next
            + np.sqrt(np.maximum(k3 * v + k4 * v_next, 0.0)) * zs
        )
        v = v_next
        if keep_paths:
            V[:, i + 1], X[:, i + 1] = v, x
    if not keep_paths:
        V[:, 1], X[:, 1] = v, x
    return Paths(
        grid if keep_paths else grid[[0, -1]],
        s0 * np.exp(X),
        V,
        info={
            "scheme": "Andersen QE (psi_c = 1.5), central log-S discretisation, "
            "no martingale correction"
        },
    )


def _bsm(
    sign: float, S: np.ndarray, K: float, tau: float, r: float, q: float, vol: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(price, delta, gamma) of a European option, vectorised over S."""
    sd = vol * math.sqrt(tau)
    d1 = (np.log(S / K) + (r - q) * tau) / sd + 0.5 * sd
    d2 = d1 - sd
    dq, dr = math.exp(-q * tau), math.exp(-r * tau)
    price = sign * (S * dq * special.ndtr(sign * d1) - K * dr * special.ndtr(sign * d2))
    delta = sign * dq * special.ndtr(sign * d1)
    gamma = dq * np.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sd)
    return price, delta, gamma


def _heston_greeks(
    sign: float,
    S: np.ndarray,
    v: np.ndarray,
    K: float,
    tau: float,
    r: float,
    q: float,
    m: HestonModel,
) -> tuple[np.ndarray, np.ndarray]:
    """(dC/dS, dC/dv) at each (S, v) from the vectorised Fourier pricer."""
    F = S * math.exp((r - q) * tau)
    res = heston_batch(sign, F, K, math.exp(-r * tau), tau, v, m)
    return res.d_forward * F / S, res.d_variance


def heston_price0(setup: HedgeSetup) -> float:
    m = setup.heston
    assert m is not None
    T, r, q = setup.time, setup.rate, setup.dividend_yield
    F, D = setup.spot * math.exp((r - q) * T), math.exp(-r * T)
    call = heston_call(F, setup.strike, D, T, m, HestonConfig())[0]
    return call if setup.option_type is OptionType.CALL else call - D * (F - setup.strike)


def implied_vol_of(setup: HedgeSetup, price: float) -> float:
    from options_engine.analytics.implied_vol import SOLVED, implied_volatility

    r = implied_volatility(
        price,
        setup.option_type.sign,
        setup.spot,
        setup.strike,
        setup.time,
        setup.rate,
        setup.dividend_yield,
    )
    if r.status not in SOLVED or r.implied_vol is None:
        raise DomainError(ErrorCode.NUMERICAL_FAILURE, "could not invert the model price")
    return r.implied_vol


def _stats(pnl: np.ndarray) -> dict[str, float]:
    n = pnl.size
    q = np.quantile(pnl, [0.01, 0.05, 0.5, 0.95, 0.99])
    return {
        "mean": float(pnl.mean()),
        "std": float(pnl.std(ddof=1)),
        "se_mean": float(pnl.std(ddof=1) / math.sqrt(n)),
        "q01": float(q[0]),
        "q05": float(q[1]),
        "median": float(q[2]),
        "q95": float(q[3]),
        "q99": float(q[4]),
    }


def run(setup: HedgeSetup) -> dict[str, Any]:
    """Simulate once, hedge at every requested frequency with every strategy."""
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(setup.seed)))
    T, r, q, K = setup.time, setup.rate, setup.dividend_yield, setup.strike
    sign = float(setup.option_type.sign)
    drift = setup.rate if setup.drift is None else setup.drift
    steps = setup.grid_steps
    if setup.heston is None:
        assert setup.real_vol is not None
        paths = gbm_paths(setup.spot, drift, q, setup.real_vol, T, steps, setup.paths, rng)
        model_price = float(_bsm(sign, np.array([setup.spot]), K, T, r, q, setup.real_vol)[0][0])
        model_vol: float = setup.real_vol
    else:
        paths = heston_qe_paths(setup.spot, drift, q, setup.heston, T, steps, setup.paths, rng)
        model_price = heston_price0(setup)
        model_vol = implied_vol_of(setup, model_price)
    hedge_vol = setup.hedge_vol if setup.hedge_vol is not None else model_vol
    S_T = paths.spot[:, -1]
    payoff_pv = math.exp(-r * T) * np.maximum(sign * (S_T - K), 0.0)
    rows: list[dict[str, Any]] = []
    theory: list[dict[str, Any]] = []
    pnl_at: dict[tuple[str, int], np.ndarray] = {}
    for n in sorted(setup.rebalances):
        stride = steps // n
        idx = np.arange(0, steps + 1, stride)
        t = paths.times[idx]
        S = paths.spot[:, idx]
        dt = T / n
        disc = np.exp(-r * t)
        gains_unit = disc[1:] * S[:, 1:] * math.exp(q * dt) - disc[:-1] * S[:, :-1]
        for strat in setup.strategies:
            deltas = np.empty((setup.paths, n))
            gamma_term = np.zeros(setup.paths)
            lo_var = np.zeros(setup.paths)
            for i in range(n):
                tau = T - t[i]
                if strat == "bsm":
                    _, d, g = _bsm(sign, S[:, i], K, tau, r, q, hedge_vol)
                    deltas[:, i] = d
                    s2g = disc[i] * g * S[:, i] ** 2
                    if setup.real_vol is not None:
                        gamma_term += 0.5 * s2g * (hedge_vol**2 - setup.real_vol**2) * dt
                    lo_var += (disc[i] * g) ** 2 * S[:, i] ** 4
                else:
                    assert paths.variance is not None
                    assert setup.heston is not None
                    v = paths.variance[:, idx[i]]
                    d_s, d_v = _heston_greeks(sign, S[:, i], v, K, tau, r, q, setup.heston)
                    if strat == "heston_mv":
                        d_s = d_s + setup.heston.rho * setup.heston.sigma / S[:, i] * d_v
                    deltas[:, i] = d_s
            v0 = (
                model_price
                if strat != "bsm"
                else float(_bsm(sign, np.array([setup.spot]), K, T, r, q, hedge_vol)[0][0])
            )
            pnl = v0 - payoff_pv + np.sum(deltas * gains_unit, axis=1)
            pnl_at[(strat, n)] = pnl
            row: dict[str, Any] = {"strategy": strat, "rebalances": n, **_stats(pnl)}
            row["std_times_sqrt_n"] = row["std"] * math.sqrt(n)
            if strat == "bsm" and setup.real_vol is not None:
                pred = math.sqrt(0.5 * setup.real_vol**4 * dt * dt * float(lo_var.mean()))
                row["leading_order_std"] = pred
                row["vol_mismatch_identity_mean"] = float(gamma_term.mean())
                row["pnl_minus_identity_mean"] = float((pnl - gamma_term).mean())
                row["pnl_minus_identity_se"] = float(
                    (pnl - gamma_term).std(ddof=1) / math.sqrt(setup.paths)
                )
                theory.append({"rebalances": n, "leading_order_std": pred})
            rows.append(row)
    finest = max(setup.rebalances)
    histograms = {}
    for strat in setup.strategies:
        pnl = pnl_at[(strat, finest)]
        counts, edges = np.histogram(pnl, bins=setup.histogram_bins)
        histograms[strat] = {"edges": edges.tolist(), "counts": counts.tolist()}
    return {
        "world": "gbm" if setup.heston is None else "heston",
        "model_price": model_price,
        "model_implied_vol": model_vol,
        "hedge_vol": hedge_vol,
        "drift": drift,
        "paths": setup.paths,
        "grid_steps": steps,
        "simulation": paths.info,
        "rows": rows,
        "theory": theory,
        "histograms": histograms,
        "histogram_rebalances": finest,
        "slope": {s: _slope(rows, s) for s in setup.strategies},
        "pnl": pnl_at,
    }


def _slope(rows: list[dict[str, Any]], strategy: str) -> float | None:
    pts = [
        (math.log(r["rebalances"]), math.log(r["std"]))
        for r in rows
        if r["strategy"] == strategy and r["std"] > 0
    ]
    if len(pts) < 2:
        return None
    x, y = np.array(pts).T
    return float(np.polyfit(x, y, 1)[0])
