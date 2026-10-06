"""Numerical configuration: method settings, tolerances and work limits.

Work limits are hard caps enforced before computation. They bound runtime and
memory; a request exceeding them fails with ``work_limit_exceeded``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from options_engine.domain.errors import DomainError, ErrorCode

MAX_TREE_STEPS = 20_000
MAX_MC_PATHS = 20_000_000
MAX_MC_CHUNK = 1_048_576
MAX_MC_PILOT_PATHS = 1_000_000


def _limit(name: str, value: int, low: int, high: int) -> None:
    if value < low:
        raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, f"{name} must be >= {low}")
    if value > high:
        raise DomainError(
            ErrorCode.WORK_LIMIT_EXCEEDED,
            f"{name}={value} exceeds the work limit {high}",
            {"field": name, "limit": high, "requested": value},
        )


@dataclass(frozen=True, slots=True)
class AnalyticConfig:
    method: str = "analytic"


@dataclass(frozen=True, slots=True)
class CRRConfig:
    """Cox-Ross-Rubinstein tree settings.

    ``allow_step_refinement``: if the requested step count gives a risk-neutral
    probability outside (0, 1), increase steps up to ``max_steps`` instead of
    failing. The step count actually used is always reported.
    ``odd_even_diagnostic``: also price with ``steps + 1`` and report the
    spread as a heuristic (not a bound) indicator of discretisation error.
    ``early_exercise_diagnostic``: for American exercise, also price the
    European contract on the same tree and report the premium. Both
    diagnostics cost one extra tree; bulk repricing turns them off.
    """

    steps: int = 1000
    allow_step_refinement: bool = False
    max_steps: int = MAX_TREE_STEPS
    odd_even_diagnostic: bool = True
    early_exercise_diagnostic: bool = True
    vega_bump: float = 1e-3
    rate_bump: float = 1e-4
    method: str = "crr"

    def __post_init__(self) -> None:
        _limit("max_steps", self.max_steps, 1, MAX_TREE_STEPS)
        _limit("steps", self.steps, 1, self.max_steps)
        if not (0 < self.vega_bump <= 0.05 and 0 < self.rate_bump <= 0.01):
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "bump sizes out of range")


class ControlVariate(StrEnum):
    NONE = "none"
    # Discounted terminal underlying; its expectation S*exp(-qT) is the
    # model forward, not the option price being estimated.
    TERMINAL_UNDERLYING = "terminal_underlying"


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    """Terminal-distribution Monte Carlo settings.

    ``paths`` counts payoff evaluations. With antithetic sampling each normal
    draw produces two evaluations, so ``paths`` must be even and the number of
    independent observations is ``paths / 2``.
    The random stream is NumPy ``PCG64`` seeded from ``SeedSequence(seed)``;
    child 0 drives the estimator and child 1 the control-variate pilot. Draws
    are consumed sequentially, so results do not depend on ``chunk_size``.
    """

    paths: int = 200_000
    seed: int = 20260928
    antithetic: bool = True
    control_variate: ControlVariate = ControlVariate.NONE
    pilot_paths: int = 20_000
    chunk_size: int = 131_072
    confidence_level: float = 0.95
    method: str = "mc_terminal"

    def __post_init__(self) -> None:
        _limit("paths", self.paths, 2, MAX_MC_PATHS)
        _limit("chunk_size", self.chunk_size, 2, MAX_MC_CHUNK)
        if self.control_variate is not ControlVariate.NONE:
            _limit("pilot_paths", self.pilot_paths, 100, MAX_MC_PILOT_PATHS)
        if self.antithetic and self.paths % 2:
            raise DomainError(
                ErrorCode.INVALID_NUMERICAL_CONFIG,
                "antithetic sampling requires an even path count",
            )
        if self.seed < 0:
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "seed must be non-negative")
        if not 0.5 <= self.confidence_level < 1.0:
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "confidence_level in [0.5, 1)")


@dataclass(frozen=True, slots=True)
class HestonConfig:
    """Quadrature settings for the Heston Fourier integral (scipy.integrate.quad on [0, inf))."""

    epsabs: float = 1e-12
    epsrel: float = 1e-10
    limit: int = 1000
    method: str = "heston_lewis"

    def __post_init__(self) -> None:
        if not (0 < self.epsabs <= 1e-4 and 0 < self.epsrel <= 1e-4):
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "Heston tolerances in (0, 1e-4]")
        _limit("limit", self.limit, 50, 20_000)


MAX_LSM_WORK = 50_000_000  # (paths + regression_paths) * exercise_dates
MAX_LSM_DUAL_WORK = 400_000_000  # outer * inner * exercise_dates^2 / 2 (inner path-steps)


@dataclass(frozen=True, slots=True)
class LSMConfig:
    """Longstaff-Schwartz settings (ADR 0018).

    ``regression_paths`` fit the continuation-value regressions; ``paths`` are an
    independent set that applies the fitted rule, so the price is a low-biased
    estimate of the Bermudan price on ``exercise_dates`` equally spaced dates with
    a valid standard error. Both counts are payoff evaluations (even when antithetic).
    SeedSequence(seed).spawn(4): child 0 regression set, child 1 pricing set, children 2
    and 3 the outer and inner paths of the optional Andersen-Broadie upper bound.
    """

    paths: int = 100_000
    regression_paths: int = 50_000
    exercise_dates: int = 50
    basis_degree: int = 3
    seed: int = 20260928
    antithetic: bool = True
    control_variate: bool = True
    confidence_level: float = 0.95
    upper_bound: bool = False
    outer_paths: int = 1_000
    inner_paths: int = 200
    method: str = "lsm"

    def __post_init__(self) -> None:
        _limit("paths", self.paths, 2, MAX_MC_PATHS)
        _limit("regression_paths", self.regression_paths, 1_000, MAX_MC_PATHS)
        _limit("exercise_dates", self.exercise_dates, 1, 1_000)
        if not 1 <= self.basis_degree <= 6:
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "basis_degree in [1, 6]")
        work = (self.paths + self.regression_paths) * self.exercise_dates
        if work > MAX_LSM_WORK:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED,
                "(paths + regression_paths) * exercise_dates exceeds the LSM work limit",
                {"work": work, "limit": MAX_LSM_WORK},
            )
        if self.antithetic and (self.paths % 2 or self.regression_paths % 2):
            raise DomainError(
                ErrorCode.INVALID_NUMERICAL_CONFIG,
                "antithetic sampling requires even path counts",
            )
        if self.seed < 0:
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "seed must be non-negative")
        if not 0.5 <= self.confidence_level < 1.0:
            raise DomainError(ErrorCode.INVALID_NUMERICAL_CONFIG, "confidence_level in [0.5, 1)")
        if self.upper_bound:
            _limit("outer_paths", self.outer_paths, 100, 100_000)
            _limit("inner_paths", self.inner_paths, 10, 10_000)
            dual = self.outer_paths * self.inner_paths * self.exercise_dates**2 // 2
            if dual > MAX_LSM_DUAL_WORK:
                raise DomainError(
                    ErrorCode.WORK_LIMIT_EXCEEDED,
                    "outer_paths * inner_paths * exercise_dates^2 / 2 exceeds the "
                    "upper-bound work limit",
                    {"work": dual, "limit": MAX_LSM_DUAL_WORK},
                )


NumericalConfig = AnalyticConfig | CRRConfig | MonteCarloConfig | HestonConfig | LSMConfig
