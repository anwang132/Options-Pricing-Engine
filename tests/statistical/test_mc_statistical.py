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
