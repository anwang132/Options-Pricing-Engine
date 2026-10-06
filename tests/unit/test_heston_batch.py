"""Vectorised Heston pricer: agreement with the adaptive pricer, derivatives, routing."""

from __future__ import annotations

import math

import numpy as np
import pytest

from options_engine.domain.numerics import HestonConfig
from options_engine.engines.heston_batch import heston_batch, node_set
from options_engine.engines.heston_fourier import heston_call
from options_engine.models.heston import HestonModel

M = HestonModel(0.04, 1.5, 0.04, 0.3, -0.7)
TIGHT = HestonConfig(epsabs=1e-13, epsrel=1e-12, limit=5000)


@pytest.mark.parametrize("tau", [0.02, 0.5, 2.0])
def test_matches_adaptive_pricer_across_strikes(tau):
    F, D = 100.0, math.exp(-0.03 * tau)
    K = np.linspace(70, 140, 15)
    got = heston_batch(1.0, F, K, D, tau, M.v0, M).price
    ref = np.array([heston_call(F, k, D, tau, M, TIGHT)[0] for k in K])
    assert np.max(np.abs(got - ref)) < 1e-8


def test_put_call_parity_and_shared_variance_derivative():
    F, K, D, tau = 100.0, 95.0, 0.99, 0.5
    call = heston_batch(1.0, F, K, D, tau, 0.05, M)
    put = heston_batch(-1.0, F, K, D, tau, 0.05, M)
    assert call.price - put.price == pytest.approx(D * (F - K), abs=1e-12)
    assert call.d_forward - put.d_forward == pytest.approx(D, abs=1e-12)
    assert call.d_variance == pytest.approx(put.d_variance, abs=1e-12)


def test_derivatives_match_finite_differences():
    F, K, D, tau, v = 100.0, 105.0, 0.98, 0.75, 0.03
    r = heston_batch(1.0, F, K, D, tau, v, M)
    h = 1e-4
    dF = (
        heston_batch(1.0, F + h, K, D, tau, v, M).price
        - heston_batch(1.0, F - h, K, D, tau, v, M).price
    ) / (2 * h)
    dv = (
        heston_batch(1.0, F, K, D, tau, v + 1e-6, M).price
        - heston_batch(1.0, F, K, D, tau, v - 1e-6, M).price
    ) / 2e-6
    assert float(r.d_forward) == pytest.approx(float(dF), rel=1e-6)
    assert float(r.d_variance) == pytest.approx(float(dv), rel=1e-5)


def test_vectorised_over_variance_states_equals_one_at_a_time():
    v = np.array([0.001, 0.02, 0.04, 0.2])
    together = heston_batch(1.0, 100.0, 100.0, 0.99, 0.3, v, M).price
    alone = [float(heston_batch(1.0, 100.0, 100.0, 0.99, 0.3, x, M).price) for x in v]
    assert together == pytest.approx(alone, abs=1e-12)


def test_routing_to_deterministic_limit_is_flagged():
    r = heston_batch(
        1.0, 100.0, [100.0, 100.0], 1.0, 1e-4, [0.04, 0.04], M, min_total_variance=1e-5
    )
    assert not r.fourier.any()  # I = 4e-6 < 1e-5
    far = heston_batch(1.0, 100.0, 1000.0, 1.0, 0.5, 0.04, M)  # ln(F/K) = -16 sd
    assert not bool(far.fourier) and float(far.price) < 1e-12


def test_node_set_resolves_oscillation_for_larger_moneyness():
    narrow, _ = node_set(0.5, M, 0.04, 0.05)
    wide, _ = node_set(0.5, M, 0.04, 1.0)
    assert wide.size > narrow.size
