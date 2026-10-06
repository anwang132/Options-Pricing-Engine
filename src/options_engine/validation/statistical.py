"""Monte Carlo statistical validation across independent replications.

For each (case, method) with R replications of n paths:

* Coverage: K = #{|estimate - reference| <= z * SE}. Under a correct method,
  K ~ approximately Binomial(R, confidence). Accept iff K lies in the central
  (1 - alpha) binomial region.
* Bias: z = (mean(estimates) - reference) / (sd(estimates)/sqrt(R)), accept iff
  |z| <= Phi^{-1}(1 - alpha/2).
* SE calibration: ratio = var(estimates) / mean(SE^2). Under H0,
  (R-1)*ratio ~ approx chi^2_{R-1}; accept iff inside the central (1 - alpha) region.
  This catches over- as well as under-stated standard errors.

alpha = family_alpha / (number of tests run) (Bonferroni), fixed in the policy
before any run. The reference is the closed-form BSM price, validated
independently against mpmath and QuantLib.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import special, stats

from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.numerics import ControlVariate, MonteCarloConfig
from options_engine.engines.base import PricingProblem
from options_engine.engines.mc_terminal import MonteCarloTerminalEngine
from options_engine.validation.checks import analytic_price
from options_engine.validation.policy import load_policy

METHODS: dict[str, tuple[bool, ControlVariate]] = {
    "plain": (False, ControlVariate.NONE),
    "antithetic": (True, ControlVariate.NONE),
    "control_variate": (False, ControlVariate.TERMINAL_UNDERLYING),
    "antithetic_control_variate": (True, ControlVariate.TERMINAL_UNDERLYING),
}

# (problem, config) -> (estimate, standard error)
Estimator = Callable[[PricingProblem, MonteCarloConfig], tuple[float, float]]

_ENGINE = MonteCarloTerminalEngine()


def engine_estimator(problem: PricingProblem, cfg: MonteCarloConfig) -> tuple[float, float]:
    out = _ENGINE.price(problem, cfg, ())
    assert out.uncertainty is not None
    return out.price, out.uncertainty.standard_error


@dataclass(frozen=True)
class ReplicationSummary:
    case: str
    method: str
    replications: int
    paths: int
    reference: float
    mean_estimate: float
    empirical_sd: float
    rms_reported_se: float
    coverage_count: int
    coverage_rate: float
    coverage_region: tuple[int, int]
    bias_z: float
    bias_z_critical: float
    se_ratio: float
    se_ratio_region: tuple[float, float]
    alpha_per_test: float
    passed_coverage: bool
    passed_bias: bool
    passed_se_calibration: bool
    mean_seconds: float
    variance_per_evaluation: float

    @property
    def passed(self) -> bool:
        return self.passed_coverage and self.passed_bias and self.passed_se_calibration

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def case_problem(case: dict[str, Any]) -> PricingProblem:
    return PricingProblem(
        OptionType(case["option_type"]),
        ExerciseStyle.EUROPEAN,
        case["spot"],
        case["strike"],
        case["time"],
        case["rate"],
        case["dividend_yield"],
        case["vol"],
    )


def replication_seeds(master_seed: int, replications: int) -> list[int]:
    state = np.random.SeedSequence(master_seed).generate_state(replications, dtype=np.uint32)
    return [int(s) for s in state]


def run_replications(
    case: dict[str, Any],
    method: str,
    replications: int,
    paths: int,
    master_seed: int,
    confidence: float,
    alpha: float,
    estimator: Estimator = engine_estimator,
) -> ReplicationSummary:
    antithetic, cv = METHODS[method]
    problem = case_problem(case)
    reference = analytic_price(problem)
    z = float(special.ndtri(0.5 + 0.5 * confidence))
    estimates, ses, times = [], [], []
    for seed in replication_seeds(master_seed, replications):
        cfg = MonteCarloConfig(paths=paths, seed=seed, antithetic=antithetic, control_variate=cv)
        t0 = time.perf_counter()
        est, se = estimator(problem, cfg)
        times.append(time.perf_counter() - t0)
        estimates.append(est)
        ses.append(se)
    e = np.asarray(estimates)
    s = np.asarray(ses)
    covered = int(np.sum(np.abs(e - reference) <= z * s))
    lo = int(stats.binom.ppf(alpha / 2, replications, confidence))
    hi = int(stats.binom.isf(alpha / 2, replications, confidence))
    sd = float(np.std(e, ddof=1))
    bias_z = (float(e.mean()) - reference) / (sd / math.sqrt(replications)) if sd > 0 else math.inf
    z_crit = float(special.ndtri(1 - alpha / 2))
    mean_se2 = float(np.mean(s**2))
    ratio = sd**2 / mean_se2 if mean_se2 > 0 else math.inf
    dof = replications - 1
    r_lo = float(stats.chi2.ppf(alpha / 2, dof)) / dof
    r_hi = float(stats.chi2.isf(alpha / 2, dof)) / dof
    return ReplicationSummary(
        case=case["name"],
        method=method,
        replications=replications,
        paths=paths,
        reference=reference,
        mean_estimate=float(e.mean()),
        empirical_sd=sd,
        rms_reported_se=math.sqrt(mean_se2),
        coverage_count=covered,
        coverage_rate=covered / replications,
        coverage_region=(lo, hi),
        bias_z=bias_z,
        bias_z_critical=z_crit,
        se_ratio=ratio,
        se_ratio_region=(r_lo, r_hi),
        alpha_per_test=alpha,
        passed_coverage=lo <= covered <= hi,
        passed_bias=abs(bias_z) <= z_crit,
        passed_se_calibration=r_lo <= ratio <= r_hi,
        mean_seconds=float(np.mean(times)),
        variance_per_evaluation=sd**2 * paths,
    )


def run_suite(
    replications: int | None = None,
    paths: int | None = None,
    methods: list[str] | None = None,
    case_names: list[str] | None = None,
    estimator: Estimator = engine_estimator,
) -> list[ReplicationSummary]:
    pol = load_policy()["statistical"]
    reps = replications or pol["replications"]
    n = paths or pol["paths_per_replication"]
    methods = methods or pol["methods"]
    cases = [c for c in pol["cases"] if case_names is None or c["name"] in case_names]
    n_tests = 3 * len(cases) * len(methods)
    alpha = pol["family_alpha"] / n_tests
    return [
        run_replications(
            c, m, reps, n, pol["master_seed"], pol["confidence_level"], alpha, estimator
        )
        for c in cases
        for m in methods
    ]
