"""Reproducible performance measurements.

Rules: warm-up calls are timed separately from steady state; every figure is a
distribution over repetitions (median, p10, p90); comparisons are made at a
stated equal accuracy or an explicitly equal budget.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from options_engine.application.portfolio import PortfolioService, Position, ScenarioSpec
from options_engine.application.surface import SurfaceService
from options_engine.domain.contracts import VanillaContract
from options_engine.domain.conventions import ExerciseStyle, GreekName, OptionType
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import (
    AnalyticConfig,
    ControlVariate,
    CRRConfig,
    MonteCarloConfig,
)
from options_engine.engines.base import PricingProblem
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine, black_scholes_price
from options_engine.engines.crr import CRREngine, build_tree
from options_engine.engines.mc_terminal import MonteCarloTerminalEngine
from options_engine.validation.policy import FIXTURE_DIR

ATM = PricingProblem(OptionType.CALL, ExerciseStyle.EUROPEAN, 100.0, 100.0, 1.0, 0.03, 0.01, 0.2)


def timing(fn: Callable[[], Any], repeats: int, warmup: int = 1) -> dict[str, Any]:
    first_start = time.perf_counter()
    fn()
    first = time.perf_counter() - first_start
    for _ in range(max(warmup - 1, 0)):
        fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    arr = np.asarray(samples)
    return {
        "first_call_seconds": first,
        "repeats": repeats,
        "median_seconds": float(np.median(arr)),
        "p10_seconds": float(np.quantile(arr, 0.1)),
        "p90_seconds": float(np.quantile(arr, 0.9)),
        "min_seconds": float(arr.min()),
    }


def peak_memory(fn: Callable[[], Any]) -> int:
    """Peak traced Python/NumPy allocation (bytes) during one call."""
    tracemalloc.start()
    try:
        fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return int(peak)


def cold_start(runs: int = 3) -> dict[str, Any]:
    """Fresh-interpreter import + first analytic price (no JIT in this project)."""
    code = (
        "import time;t=time.perf_counter();"
        "from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine;"
        "from options_engine.engines.base import PricingProblem;"
        "from options_engine.domain.conventions import OptionType, ExerciseStyle;"
        "from options_engine.domain.numerics import AnalyticConfig;"
        "t1=time.perf_counter();"
        "BlackScholesAnalyticEngine().price(PricingProblem(OptionType.CALL,"
        "ExerciseStyle.EUROPEAN,100.,100.,1.,.03,.01,.2),AnalyticConfig(),());"
        "t2=time.perf_counter();print(t1-t,t2-t1)"
    )
    imports, firsts = [], []
    for _ in range(runs):
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        a, b = (float(v) for v in out.stdout.split())
        imports.append(a)
        firsts.append(b)
    return {
        "runs": runs,
        "import_seconds_median": float(np.median(imports)),
        "first_price_seconds_median": float(np.median(firsts)),
        "note": "No JIT compilation is used; cold cost is module import plus first-call setup.",
    }


def time_stepped_mc(
    problem: PricingProblem, paths: int, steps: int, seed: int, chunk: int = 16_384
) -> float:
    """Naive alternative kept only for benchmarking: exact log-increments over time steps.

    Same terminal distribution as the engine (so the same accuracy per path),
    but it draws and accumulates `steps` normals per path.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    dt = problem.time_to_expiry / steps
    drift = (problem.rate - problem.dividend_yield - 0.5 * problem.volatility**2) * dt
    vol = problem.volatility * math.sqrt(dt)
    total, remaining = 0.0, paths
    while remaining:
        n = min(chunk, remaining)
        log_s = np.full(n, math.log(problem.spot))
        for _ in range(steps):
            log_s += drift + vol * rng.standard_normal(n)
        total += float(np.maximum(np.exp(log_s) - problem.strike, 0.0).sum())
        remaining -= n
    return math.exp(-problem.rate * problem.time_to_expiry) * total / paths


def run_benchmarks(quick: bool = False) -> dict[str, Any]:
    reps = 5 if quick else 30
    bsm = BlackScholesAnalyticEngine()
    crr = CRREngine()
    mc = MonteCarloTerminalEngine()
    out: dict[str, Any] = {"cold_start": cold_start(2 if quick else 5)}

    out["bsm_scalar_price_and_greeks"] = timing(
        lambda: bsm.price(ATM, AnalyticConfig(), tuple(GreekName)), repeats=200 if quick else 2000
    )
    rng = np.random.default_rng(0)
    n = 1_000_000
    args = (
        rng.choice([-1.0, 1.0], n),
        np.full(n, 100.0),
        rng.uniform(60, 160, n),
        rng.uniform(0.02, 3.0, n),
        np.full(n, 0.03),
        np.full(n, 0.01),
        rng.uniform(0.05, 0.8, n),
    )
    batch = timing(lambda: black_scholes_price(*args), repeats=3 if quick else 10)
    batch["options_per_call"] = n
    batch["options_per_second_median"] = n / batch["median_seconds"]
    out["bsm_batch_price_1m"] = batch

    tree_rows = []
    for steps in (500, 1000, 2000, 5000):
        for exercise in (ExerciseStyle.EUROPEAN, ExerciseStyle.AMERICAN):
            p = ATM.with_changes(exercise_style=exercise, option_type=OptionType.PUT)
            t = timing(partial(build_tree, p, steps), repeats=max(3, reps // (steps // 500)))
            t.update(
                {
                    "steps": steps,
                    "exercise": exercise.value,
                    "peak_bytes": peak_memory(partial(build_tree, p, steps)),
                }
            )
            tree_rows.append(t)
    out["crr_single_tree"] = tree_rows
    out["crr_engine_price_all_greeks_1000"] = timing(
        lambda: crr.price(ATM, CRRConfig(steps=1000), tuple(GreekName)), repeats=reps
    )

    mc_rows = []
    for paths in (100_000, 1_000_000):
        cfg = MonteCarloConfig(paths=paths, chunk_size=131_072)
        t = timing(partial(mc.price, ATM, cfg, ()), repeats=max(3, reps // 3))
        t.update(
            {
                "paths": paths,
                "chunk_size": cfg.chunk_size,
                "peak_bytes": peak_memory(partial(mc.price, ATM, cfg, ())),
            }
        )
        mc_rows.append(t)
    out["mc_terminal"] = mc_rows

    out["release_b"] = release_b_benchmarks(quick)
    out["improvements"] = improvements(quick)
    out["error_vs_runtime"] = error_vs_runtime(quick)
    return out


def improvements(quick: bool) -> list[dict[str, Any]]:
    """Before/after comparisons at comparable accuracy."""
    rows: list[dict[str, Any]] = []
    # (a) Exact terminal sampling vs 252-step path simulation: same distribution and
    # path count, hence the same sampling accuracy.
    paths = 50_000 if quick else 200_000
    stepped = timing(lambda: time_stepped_mc(ATM, paths, 252, 1), repeats=2 if quick else 5)
    terminal = timing(
        lambda: MonteCarloTerminalEngine().price(
            ATM, MonteCarloConfig(paths=paths, antithetic=False), ()
        ),
        repeats=5 if quick else 20,
    )
    rows.append(
        {
            "name": "exact_terminal_vs_time_stepped",
            "comparison": f"{paths} paths each; identical terminal distribution => equal "
            "sampling accuracy per path",
            "before": {"method": "252-step log-Euler (exact increments)", **stepped},
            "after": {"method": "exact terminal sampling (engine)", **terminal},
            "speedup_median": stepped["median_seconds"] / terminal["median_seconds"],
        }
    )
    # (b) Variance reduction at equal target CI half-width (95%, 0.01 on a price ~9).
    target = 0.01
    z = 1.959963984540054
    pilot = 20_000
    variants = {
        "plain": MonteCarloConfig(paths=pilot, antithetic=False),
        "antithetic+control_variate": MonteCarloConfig(
            paths=pilot, antithetic=True, control_variate=ControlVariate.TERMINAL_UNDERLYING
        ),
    }
    sized: dict[str, dict[str, Any]] = {}
    for name, cfg in variants.items():
        se = MonteCarloTerminalEngine().price(ATM, cfg, ()).uncertainty
        assert se is not None
        # Scale the payoff-evaluation budget so the CI half-width hits the target.
        needed = math.ceil(pilot * (z * se.standard_error / target) ** 2 / 2) * 2
        run_cfg = MonteCarloConfig(
            paths=needed, antithetic=cfg.antithetic, control_variate=cfg.control_variate, seed=99
        )
        t = timing(
            partial(MonteCarloTerminalEngine().price, ATM, run_cfg, ()),
            repeats=3 if quick else 10,
        )
        achieved = MonteCarloTerminalEngine().price(ATM, run_cfg, ()).uncertainty
        assert achieved is not None
        sized[name] = {"paths": needed, "achieved_ci_half_width": z * achieved.standard_error, **t}
    rows.append(
        {
            "name": "variance_reduction_equal_accuracy",
            "comparison": f"path counts sized from an independent pilot so the 95% CI "
            f"half-width is ~{target}; ATM call",
            "before": {"method": "plain", **sized["plain"]},
            "after": {
                "method": "antithetic + control variate",
                **sized["antithetic+control_variate"],
            },
            "speedup_median": sized["plain"]["median_seconds"]
            / sized["antithetic+control_variate"]["median_seconds"],
        }
    )
    return rows


def error_vs_runtime(quick: bool) -> dict[str, list[dict[str, float]]]:
    ref = BlackScholesAnalyticEngine().price(ATM, AnalyticConfig(), ()).price
    reps = 3 if quick else 7
    tree = []
    for n in (50, 100, 200, 400, 800, 1600, 3200):
        t = timing(partial(build_tree, ATM, n), repeats=reps)
        tree.append(
            {
                "setting": n,
                "seconds": t["median_seconds"],
                "abs_error": abs(build_tree(ATM, n).price - ref),
            }
        )
    mc_curves: dict[str, list[dict[str, float]]] = {}
    for label, anti, cv in (
        ("mc_plain", False, ControlVariate.NONE),
        ("mc_antithetic_cv", True, ControlVariate.TERMINAL_UNDERLYING),
    ):
        rows = []
        for paths in (10_000, 40_000, 160_000, 640_000):
            cfg = MonteCarloConfig(paths=paths, antithetic=anti, control_variate=cv)
            t = timing(partial(MonteCarloTerminalEngine().price, ATM, cfg, ()), repeats=reps)
            u = MonteCarloTerminalEngine().price(ATM, cfg, ()).uncertainty
            assert u is not None
            # For MC the error measure is the 95% CI half-width (sampling uncertainty).
            rows.append(
                {
                    "setting": paths,
                    "seconds": t["median_seconds"],
                    "abs_error": 1.959963984540054 * u.standard_error,
                }
            )
        mc_curves[label] = rows
    return {"crr_tree_abs_error": tree, **{f"{k}_ci_half_width": v for k, v in mc_curves.items()}}


def release_b_benchmarks(quick: bool) -> dict[str, Any]:
    """Surface fit and portfolio scenario timings, incl. worst cases under work limits."""
    raw = (FIXTURE_DIR.parent / "snapshots" / "synthetic_day1.json").read_bytes()

    def fit_once() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = SurfaceService(Path(tmp))
            svc.fit(svc.snapshots.ingest(raw)[0]["snapshot_id"])

    out: dict[str, Any] = {"surface_fit_incl_ingest": timing(fit_once, repeats=2 if quick else 5)}
    as_of = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    val = ValuationContext(as_of)
    mkt = MarketSnapshot(spot=Decimal("100"), rate=Decimal("0.03"))

    def book(n: int, style: ExerciseStyle) -> list[Position]:
        return [
            Position(
                f"{style.value[:2]}{i}",
                VanillaContract(
                    "SYNTH",
                    "USD",
                    Decimal(80 + i),
                    OptionType.PUT,
                    style,
                    as_of + timedelta(days=365),
                    Decimal(100),
                ),
                Decimal(1),
                0.25,
            )
            for i in range(n)
        ]

    typical = book(10, ExerciseStyle.EUROPEAN) + book(5, ExerciseStyle.AMERICAN)
    spec = ScenarioSpec(
        spot_shocks_pct=(-10.0, -5.0, 0.0, 5.0, 10.0),
        vol_shocks_pts=(-5.0, 0.0, 5.0),
        time_roll_days=(0.0, 7.0, 30.0),
    )
    t = timing(partial(PortfolioService().run, typical, val, mkt, spec), repeats=2 if quick else 5)
    t["description"] = "10 European + 5 American (400 steps) positions x 45 scenarios"
    out["portfolio_typical"] = t
    if not quick:
        worst: dict[str, Any] = {}
        grid20 = tuple(float(x) for x in range(-10, 10))
        cases = {
            "american_2000_steps_1000_repricings": (
                book(50, ExerciseStyle.AMERICAN),
                ScenarioSpec(grid20, (0.0,), american_steps=2000),
            ),
            "american_400_steps_10000_repricings": (
                book(50, ExerciseStyle.AMERICAN),
                ScenarioSpec(grid20, tuple(float(x) for x in range(10))),
            ),
            "european_50000_repricings": (
                book(50, ExerciseStyle.EUROPEAN),
                ScenarioSpec(grid20, tuple(float(x) for x in range(50))),
            ),
        }
        for name, (positions, sc) in cases.items():
            t0 = time.perf_counter()
            PortfolioService().run(positions, val, mkt, sc)
            worst[name] = time.perf_counter() - t0
        out["portfolio_worst_case_seconds"] = worst
    return out
