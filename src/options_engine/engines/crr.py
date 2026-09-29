"""Cox-Ross-Rubinstein binomial tree (European and American exercise).

* u = exp(sigma*sqrt(dt)), d = 1/u, p = (exp((r-q)dt) - d) / (u - d), evaluated
  with expm1/sinh for small steps. p must lie strictly inside (0, 1); invalid
  probabilities are never clipped (ADR 0006).
* Rolling one-dimensional arrays: O(N) memory, O(N^2) time.
* Cash dividends use the escrowed model: the tree runs on S* = S - PV(divs);
  the exercise value at a node uses S* + PV(dividends still to be paid).
* Delta, gamma and theta are read from tree nodes at steps 1 and 2; vega,
  rho and dividend rho use central bump-and-reprice at the same step count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from options_engine.domain.conventions import DividendTreatment, ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import CRRConfig
from options_engine.domain.results import EngineOutput, GreekResult, GreekStatus
from options_engine.engines.base import EngineCapabilities, PricingProblem
from options_engine.models.black_scholes import BLACK_SCHOLES


@dataclass(frozen=True, slots=True)
class TreeResult:
    price: float
    steps: int
    probability: float
    # Option values and escrowed underlying at steps 1 and 2 (for Greeks).
    level1: tuple[np.ndarray, np.ndarray] | None
    level2: tuple[np.ndarray, np.ndarray] | None
    dt: float


def up_probability(problem: PricingProblem, steps: int) -> float:
    dt = problem.time_to_expiry / steps
    lnu = problem.volatility * math.sqrt(dt)
    carry = (problem.rate - problem.dividend_yield) * dt
    return (math.expm1(carry) - math.expm1(-lnu)) / (2.0 * math.sinh(lnu))


def minimum_valid_steps(problem: PricingProblem) -> int:
    """Smallest N with |r-q| sqrt(T/N) < sigma, i.e. p in (0, 1)."""
    carry = abs(problem.rate - problem.dividend_yield)
    if carry == 0.0:
        return 1
    n = math.floor(problem.time_to_expiry * carry * carry / problem.volatility**2) + 1
    while not 0.0 < up_probability(problem, n) < 1.0:  # guard float rounding at the edge
        n += 1
    return n


def _remaining_dividend_pv(problem: PricingProblem, t: float) -> float:
    return math.fsum(
        a * math.exp(-problem.rate * (td - t)) for td, a in problem.dividends if td > t
    )


def build_tree(
    problem: PricingProblem,
    steps: int,
    boundary: list[tuple[float, float | None]] | None = None,
) -> TreeResult:
    """Backward induction. Caller guarantees T > 0, sigma > 0.

    If ``boundary`` is given (American exercise), one (time, spot) pair per step
    is appended: the node spot where immediate exercise is strictly better than
    continuation and closest to the continuation region (largest such spot for a
    put, smallest for a call); ``None`` when no node exercises at that step.
    Resolution is one node spacing, sigma*sqrt(dt) in log-spot.
    """
    T, vol, r = problem.time_to_expiry, problem.volatility, problem.rate
    sign = problem.option_type.sign
    american = problem.exercise_style is ExerciseStyle.AMERICAN
    dt = T / steps
    lnu = vol * math.sqrt(dt)
    p = up_probability(problem, steps)
    if not 0.0 < p < 1.0:
        raise DomainError(
            ErrorCode.INVALID_TREE_PROBABILITY,
            "risk-neutral up probability outside (0, 1); increase steps or enable refinement",
            {"probability": p, "steps": steps, "minimum_valid_steps": minimum_valid_steps(problem)},
        )
    s_star = problem.escrowed_spot()
    K = problem.strike
    disc = math.exp(-r * dt)
    pu, pd = disc * p, disc * (1.0 - p)
    # Powers u^k for k = -N..N; level i uses k = -i, -i+2, ..., i.
    powers = s_star * np.exp(lnu * np.arange(-steps, steps + 1, dtype=np.float64))
    values = np.maximum(sign * (powers[0::2] - K), 0.0)
    level1 = level2 = None
    has_divs = bool(problem.dividends)
    for i in range(steps - 1, -1, -1):
        values = pu * values[1:] + pd * values[:-1]
        nodes = powers[steps - i : steps + i + 1 : 2]
        if american:
            spot_nodes = nodes + _remaining_dividend_pv(problem, i * dt) if has_divs else nodes
            exercise = sign * (spot_nodes - K)
            if boundary is not None:
                exercised = spot_nodes[(exercise > values) & (exercise > 0.0)]
                edge = None
                if exercised.size:
                    edge = float(exercised.max() if sign < 0 else exercised.min())
                boundary.append((i * dt, edge))
            np.maximum(values, exercise, out=values)
        if i == 2:
            level2 = (values.copy(), nodes.copy())
        elif i == 1:
            level1 = (values.copy(), nodes.copy())
    return TreeResult(float(values[0]), steps, p, level1, level2, dt)


class CRREngine:
    engine_id = "crr_tree"
    version = "1.0.0"
    description = "Cox-Ross-Rubinstein binomial tree (European and American)"
    config_type: type = CRRConfig
    capabilities = EngineCapabilities(
        exercise_styles=frozenset({ExerciseStyle.EUROPEAN, ExerciseStyle.AMERICAN}),
        dividend_treatments=frozenset(
            {DividendTreatment.CONTINUOUS_YIELD, DividendTreatment.ESCROWED_CASH}
        ),
        model_families=frozenset({BLACK_SCHOLES}),
        greeks={
            GreekName.DELTA: "tree_nodes",
            GreekName.GAMMA: "tree_nodes",
            GreekName.THETA: "tree_nodes",
            GreekName.VEGA: "central_bump_reprice",
            GreekName.RHO: "central_bump_reprice",
            GreekName.DIVIDEND_RHO: "central_bump_reprice",
        },
        stochastic=False,
        batching=False,
        diagnostics=("steps_used", "up_probability", "odd_even_spread", "early_exercise_premium"),
        zero_volatility=False,
    )

    def _resolve_steps(self, problem: PricingProblem, config: CRRConfig) -> tuple[int, bool]:
        steps = config.steps
        if 0.0 < up_probability(problem, steps) < 1.0:
            return steps, False
        needed = minimum_valid_steps(problem)
        if not config.allow_step_refinement:
            raise DomainError(
                ErrorCode.INVALID_TREE_PROBABILITY,
                f"{steps} steps give an up probability outside (0, 1); at least {needed} "
                "steps are required (or enable allow_step_refinement)",
                {"steps": steps, "minimum_valid_steps": needed},
            )
        if needed > config.max_steps:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED,
                f"a valid tree needs {needed} steps, above max_steps={config.max_steps}",
                {"minimum_valid_steps": needed, "max_steps": config.max_steps},
            )
        return needed, True

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput:
        cfg: CRRConfig = config
        if problem.volatility <= 0.0:
            raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "CRR requires volatility > 0")
        if problem.time_to_expiry <= 0.0:
            raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "CRR requires time to expiry > 0")
        steps, refined = self._resolve_steps(problem, cfg)
        tree = build_tree(problem, steps)
        diagnostics: dict[str, Any] = {
            "steps_requested": cfg.steps,
            "steps_used": steps,
            "step_refinement_applied": refined,
            "up_probability": tree.probability,
            "dt_years": tree.dt,
        }
        if cfg.odd_even_diagnostic and steps + 1 <= cfg.max_steps:
            alt = build_tree(problem, steps + 1).price
            diagnostics["price_steps_plus_one"] = alt
            diagnostics["odd_even_spread"] = abs(alt - tree.price)
            diagnostics["odd_even_average"] = 0.5 * (alt + tree.price)
        if cfg.early_exercise_diagnostic and problem.exercise_style is ExerciseStyle.AMERICAN:
            european = build_tree(
                problem.with_changes(exercise_style=ExerciseStyle.EUROPEAN), steps
            )
            diagnostics["european_same_tree"] = european.price
            diagnostics["early_exercise_premium"] = tree.price - european.price
        out = {g: self._greek(g, problem, cfg, tree) for g in greeks}
        return EngineOutput(tree.price, out, diagnostics)

    def _greek(
        self, name: GreekName, problem: PricingProblem, cfg: CRRConfig, tree: TreeResult
    ) -> GreekResult:
        method = self.capabilities.greeks[name]
        if name in (GreekName.DELTA, GreekName.GAMMA, GreekName.THETA):
            if tree.level2 is None or tree.level1 is None:
                return GreekResult(name, GreekStatus.NOT_SUPPORTED, note="needs at least 2 steps")
            (v1, s1), (v2, s2) = tree.level1, tree.level2
            delta = float((v1[1] - v1[0]) / (s1[1] - s1[0]))
            if name is GreekName.DELTA:
                return GreekResult(name, GreekStatus.OK, delta, method)
            if name is GreekName.GAMMA:
                up = (v2[2] - v2[1]) / (s2[2] - s2[1])
                down = (v2[1] - v2[0]) / (s2[1] - s2[0])
                return GreekResult(
                    name, GreekStatus.OK, float((up - down) / (0.5 * (s2[2] - s2[0]))), method
                )
            # Theta holding S* fixed, then map to holding S fixed: dS*/dt = -r PV(divs).
            theta = float((v2[1] - tree.price) / (2.0 * tree.dt))
            theta -= delta * problem.rate * problem.dividend_pv()
            return GreekResult(name, GreekStatus.OK, theta, method)
        field, h = {
            GreekName.VEGA: ("volatility", cfg.vega_bump),
            GreekName.RHO: ("rate", cfg.rate_bump),
            GreekName.DIVIDEND_RHO: ("dividend_yield", cfg.rate_bump),
        }[name]
        base = getattr(problem, field)
        lo = base - h
        if field == "volatility" and lo <= 0.0:
            lo = base
        try:
            up = build_tree(problem.with_changes(**{field: base + h}), tree.steps).price
            down = (
                tree.price
                if lo == base
                else build_tree(problem.with_changes(**{field: lo}), tree.steps).price
            )
        except DomainError as exc:
            return GreekResult(name, GreekStatus.FAILED, note=f"bumped tree invalid: {exc.message}")
        note = f"bump {h:g}" + (" (forward difference at sigma floor)" if lo == base else "")
        return GreekResult(name, GreekStatus.OK, (up - down) / (base + h - lo), method, note=note)
