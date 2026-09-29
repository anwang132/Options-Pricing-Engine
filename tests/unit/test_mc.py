from __future__ import annotations

import math
import tracemalloc

import numpy as np
import pytest

from options_engine.domain.conventions import ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError
from options_engine.domain.numerics import ControlVariate, MonteCarloConfig
from options_engine.domain.results import GreekStatus
from options_engine.engines.mc_terminal import MonteCarloTerminalEngine, RunningMoments, simulate
from tests.conftest import problem

MC = MonteCarloTerminalEngine()


def test_reproducible_for_fixed_seed():
    cfg = MonteCarloConfig(paths=20_000, seed=7)
    assert MC.price(problem(), cfg, ()).price == MC.price(problem(), cfg, ()).price


def test_different_seeds_differ():
    a = MC.price(problem(), MonteCarloConfig(paths=20_000, seed=1), ()).price
    b = MC.price(problem(), MonteCarloConfig(paths=20_000, seed=2), ()).price
    assert a != b


@pytest.mark.parametrize("antithetic", [True, False])
def test_result_independent_of_chunk_size(antithetic):
    prices = {
        chunk: MC.price(
            problem(),
            MonteCarloConfig(paths=50_000, seed=3, antithetic=antithetic, chunk_size=chunk),
            (),
        ).price
        for chunk in (1_000, 7_778, 65_536)
    }
    vals = list(prices.values())
    assert max(vals) - min(vals) <= 1e-12 * abs(vals[0])


def test_antithetic_standard_error_uses_pair_averages():
    p = problem()
    cfg = MonteCarloConfig(paths=10_000, seed=11, antithetic=True)
    out = MC.price(p, cfg, ())
    # Recompute independently from the same stream.
    main, _ = np.random.SeedSequence(11).spawn(2)
    z = np.random.Generator(np.random.PCG64(main)).standard_normal(5_000)
    T, r, q, v = 1.0, 0.03, 0.01, 0.2
    drift = (r - q - 0.5 * v * v) * T

    def pay(zz):
        return math.exp(-r * T) * np.maximum(100 * np.exp(drift + v * zz) - 100, 0.0)

    pairs = 0.5 * (pay(z) + pay(-z))
    assert out.price == pytest.approx(pairs.mean(), rel=1e-12)
    assert out.uncertainty.standard_error == pytest.approx(
        pairs.std(ddof=1) / math.sqrt(5_000), rel=1e-10
    )
    assert out.uncertainty.independent_observations == 5_000


def test_control_variate_pilot_is_independent_of_main_stream():
    _, diag = simulate(
        problem(),
        MonteCarloConfig(paths=10_000, control_variate=ControlVariate.TERMINAL_UNDERLYING),
    )
    assert "child 1 pilot" in diag["seed_policy"]
    assert diag["control_variate_beta"] is not None


def test_gamma_and_theta_not_supported_rather_than_zero():
    out = MC.price(problem(), MonteCarloConfig(paths=2_000), tuple(GreekName))
    for g in (GreekName.GAMMA, GreekName.THETA, GreekName.DIVIDEND_RHO):
        assert out.greeks[g].status is GreekStatus.NOT_SUPPORTED
        assert out.greeks[g].value is None
    assert out.greeks[GreekName.DELTA].standard_error > 0


def test_pathwise_greeks_consistent_with_analytic():
    from options_engine.domain.numerics import AnalyticConfig
    from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine

    # K = 105: for K = 100 here ln(F/K) = sigma^2 T / 2, so each antithetic pair has
    # exactly one ITM path and pathwise rho is deterministic (SE ~ rounding only).
    p = problem(strike=105.0)
    ref = BlackScholesAnalyticEngine().price(p, AnalyticConfig(), tuple(GreekName)).greeks
    out = MC.price(p, MonteCarloConfig(paths=400_000, seed=5), tuple(GreekName)).greeks
    for g in (GreekName.DELTA, GreekName.VEGA, GreekName.RHO):
        assert abs(out[g].value - ref[g].value) < 5 * out[g].standard_error + 1e-9, g


def test_degenerate_antithetic_rho_has_rounding_level_se():
    out = MC.price(problem(), MonteCarloConfig(paths=100_000, seed=5), (GreekName.RHO,))
    rho = out.greeks[GreekName.RHO]
    assert rho.standard_error < 1e-10
    assert rho.value == pytest.approx(100 * math.exp(-0.03) * 0.5, rel=1e-12)


def test_rejects_american_and_degenerate_inputs():
    with pytest.raises(DomainError):
        MC.price(problem(exercise_style=ExerciseStyle.AMERICAN), MonteCarloConfig(paths=100), ())
    with pytest.raises(DomainError):
        MC.price(problem(volatility=0.0), MonteCarloConfig(paths=100), ())


def test_memory_bounded_by_chunk_not_paths():
    def peak(paths: int) -> int:
        tracemalloc.start()
        MC.price(problem(), MonteCarloConfig(paths=paths, chunk_size=16_384), ())
        _, pk = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return pk

    small, large = peak(100_000), peak(1_000_000)
    assert large < 1.5 * small  # 10x the paths, roughly the same peak memory


def test_running_moments_matches_numpy():
    rng = np.random.default_rng(0)
    data = rng.normal(size=(10_001, 3)) * [1, 10, 0.1] + [5, -2, 1e3]
    m = RunningMoments()
    for chunk in np.array_split(data, 17):
        m.update(chunk)
    np.testing.assert_allclose(m.mean, data.mean(axis=0), rtol=1e-13)
    np.testing.assert_allclose(m.variance(), data.var(axis=0, ddof=1), rtol=1e-11)
