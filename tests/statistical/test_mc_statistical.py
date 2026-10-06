"""Monte Carlo statistical validation (slow tier): uv run pytest -m statistical

Acceptance rules come from config/validation_policy.toml and were fixed before
the first run (docs/progress.md records the hash).
"""

from __future__ import annotations

import pytest

from options_engine.validation.statistical import run_suite

pytestmark = pytest.mark.statistical


@pytest.fixture(scope="module")
def suite():
    return run_suite()


def test_all_coverage_bias_and_se_tests_pass(suite):
    failures = [r.to_dict() for r in suite if not r.passed]
    assert not failures, failures


def test_variance_reduction_is_real_at_equal_evaluation_budget(suite):
    by = {(r.case, r.method): r.variance_per_evaluation for r in suite}
    for case in {r.case for r in suite}:
        assert by[(case, "antithetic_control_variate")] < by[(case, "plain")]


def test_lsm_matches_bermudan_tree_within_predeclared_bounds():
    from options_engine.validation.lsm import check_lsm_vs_bermudan

    result = check_lsm_vs_bermudan()
    assert result.passed, result.failures


def test_lsm_standard_error_is_calibrated():
    from options_engine.validation.lsm import check_lsm_se_calibration

    result = check_lsm_se_calibration()
    assert result.passed, result.details


@pytest.mark.parametrize(
    "check_name",
    [
        "lsm.check_lsm_upper_bound",
        "heston_batch.check_heston_batch_sweep",
        "heston_batch.check_heston_batch_greeks",
        "hedging.check_hedging_gbm",
        "hedging.check_heston_simulator",
        "hedging.check_heston_min_variance_hedge",
        "term_structure.check_heston_ts_monte_carlo",
    ],
)
def test_slow_predeclared_checks(check_name):
    import importlib

    module, fn = check_name.split(".")
    result = getattr(importlib.import_module(f"options_engine.validation.{module}"), fn)()
    assert result.passed, result.summary()["failures"]
