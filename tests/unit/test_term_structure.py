"""Term-structure Heston and per-expiry SVI slices."""

from __future__ import annotations

import math

import numpy as np
import pytest

from options_engine.application.svi_slices import SviSlices, butterfly_g, raw_params, total_variance
from options_engine.domain.errors import DomainError
from options_engine.engines.heston_batch import heston_batch
from options_engine.models.heston import HestonModel, HestonTSModel

PILLARS = (7 / 365, 30 / 365, 91 / 365, 182 / 365)


def test_equal_thetas_reproduce_constant_heston():
    m = HestonModel(0.04, 1.5, 0.05, 0.6, -0.7)
    ts = HestonTSModel(0.04, 1.5, 0.6, -0.7, (0.05,) * 5, PILLARS)
    K = np.linspace(70, 140, 9)
    for T in (0.01, 0.3, 1.5):
        a = heston_batch(1.0, 100.0, K, 0.98, T, 0.04, m).price
        b = heston_batch(1.0, 100.0, K, 0.98, T, 0.04, ts).price
        assert np.max(np.abs(a - b)) < 1e-12
        assert ts.integrated_variance(T) == pytest.approx(m.integrated_variance(T), abs=1e-15)


def test_integrated_variance_matches_quadrature_of_expected_variance():
    ts = HestonTSModel(0.03, 2.0, 0.4, -0.5, (0.02, 0.03, 0.05, 0.06, 0.08), PILLARS)
    T, n = 1.0, 200_000
    t = (np.arange(n) + 0.5) * T / n
    # E[v_t] solves dE/dt = kappa (theta(t) - E); integrate it with small explicit steps.
    e, total = ts.v0, 0.0
    for ti in t:
        e += ts.kappa * (ts.theta_at(float(ti)) - e) * (T / n)
        total += e * T / n
    assert ts.integrated_variance(T) == pytest.approx(total, rel=1e-4)


def test_only_the_buckets_before_expiry_matter():
    a = HestonTSModel(0.04, 1.5, 0.3, -0.7, (0.02, 0.03, 0.05, 0.06, 0.08), PILLARS)
    b = HestonTSModel(0.04, 1.5, 0.3, -0.7, (0.02, 0.03, 0.05, 0.6, 0.8), PILLARS)
    T = 60 / 365  # inside the third bucket
    pa = heston_batch(-1.0, 100.0, 95.0, 0.99, T, 0.04, a).price
    pb = heston_batch(-1.0, 100.0, 95.0, 0.99, T, 0.04, b).price
    assert float(pa) == float(pb)


def test_ts_model_validation():
    with pytest.raises(DomainError):
        HestonTSModel(0.04, 1.5, 0.3, -0.7, (0.02, 0.03), PILLARS)
    with pytest.raises(DomainError):
        HestonTSModel(0.04, 1.5, 0.3, -0.7, (0.02, 0.03, 0.05), (0.5, 0.1))
    with pytest.raises(DomainError):
        HestonTSModel(0.04, 1.5, 0.3, -1.0, (0.02,) * 5, PILLARS)


def test_svi_parameterisation_keeps_variance_non_negative():
    rng = np.random.default_rng(0)
    k = np.linspace(-3, 3, 601)
    for _ in range(200):
        x = np.array(
            [
                rng.uniform(0, 0.1),
                rng.uniform(0, 2),
                rng.uniform(-0.99, 0.99),
                rng.uniform(-1, 1),
                rng.uniform(1e-3, 1),
            ]
        )
        p = raw_params(x)
        assert total_variance(p, k).min() >= x[0] - 1e-12


def test_butterfly_g_matches_flat_smile_and_detects_arbitrage():
    flat = (0.04, 0.0, 0.0, 0.0, 0.1)  # w = 0.04 everywhere: g = 1 - 0 + 0 at all k
    assert np.allclose(butterfly_g(flat, np.linspace(-1, 1, 11)), 1.0)
    steep = raw_params(np.array([0.0005, 3.0, -0.95, 0.0, 0.01]))  # extreme skew, tiny floor
    assert butterfly_g(steep, np.linspace(-0.5, 0.5, 201)).min() < 0


def test_slice_interpolation_is_linear_in_total_variance():
    p1, p2 = (0.01, 0.1, -0.5, 0.0, 0.1), (0.04, 0.12, -0.4, 0.0, 0.15)
    s = SviSlices((0.25, 1.0), (p1, p2))
    k = np.linspace(-0.5, 0.5, 5)
    mid = s.total_variance(k, 0.625)
    assert np.allclose(mid, 0.5 * (total_variance(p1, k) + total_variance(p2, k)))
    assert np.allclose(s.total_variance(k, 0.125), total_variance(p1, k) / 2)
    assert math.isclose(
        float(s.total_variance(np.array([0.0]), 2.0)[0]),
        2 * float(total_variance(p2, np.array([0.0]))[0]),
    )
