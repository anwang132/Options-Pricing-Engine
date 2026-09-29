"""Deterministic reference checks against committed fixtures (no network, no QuantLib needed)."""

from __future__ import annotations

import pytest

from options_engine.validation import checks


@pytest.mark.parametrize(
    "check",
    [
        checks.check_mpmath_prices,
        checks.check_normalized_grid,
        checks.check_mpmath_greeks,
        checks.check_published,
        checks.check_identities,
        checks.check_fd_sweep,
        checks.check_crr_vs_bsm,
        checks.check_iv_round_trip,
        checks.check_iv_failures,
        checks.check_boundaries,
        checks.check_american_identities,
        checks.check_surface_gate,
    ],
    ids=lambda f: f.__name__,
)
def test_check_passes(check):
    result = check()
    assert result.passed, result.summary()["failures"]


def test_quantlib_european_fixture():
    prices, greeks = checks.check_quantlib_european()
    assert prices.passed, prices.failures
    assert greeks.passed, greeks.failures


def test_american_vs_quantlib_fd():
    result = checks.check_american_vs_quantlib()
    assert result.passed, result.failures


def test_fixture_provenance_recorded():
    for name in (
        "bsm_european_mpmath_v1.json",
        "bsm_european_quantlib_v1.json",
        "american_quantlib_fd_v1.json",
        "bsm_published_examples_v1.json",
        "normalized_black_grid_v1.json",
    ):
        fx = checks.load_fixture(name)
        assert fx["libraries"]["QuantLib"] == "1.43"
        assert fx["libraries"]["mpmath"] == "1.4.1"
        assert len(fx["generator_sha256"]) == 64
