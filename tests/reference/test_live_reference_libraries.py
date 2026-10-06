"""Recompute a sample of fixtures with the pinned reference libraries (slow tier).

Run: uv run --group reference pytest -m reference
Detects fixture drift and confirms the committed values came from these libraries.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from options_engine.validation import checks

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        importlib.util.find_spec("QuantLib") is None or importlib.util.find_spec("mpmath") is None,
        reason="reference dependency group not installed",
    ),
]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_quantlib_european_fixture_reproduces():
    import generate_reference_fixtures as gen

    fresh = gen.build_quantlib_european()["cases"]
    committed = checks.load_fixture("bsm_european_quantlib_v1.json")["cases"]
    assert len(fresh) == len(committed)
    for a, b in zip(fresh, committed, strict=True):
        assert a["price"] == pytest.approx(b["price"], rel=1e-14, abs=1e-15)


def test_mpmath_fixture_sample_reproduces():
    import generate_reference_fixtures as gen

    committed = checks.load_fixture("bsm_european_mpmath_v1.json")["cases"][::25]
    for c in committed:
        w = 1 if c["option_type"] == "call" else -1
        fresh = gen.mp_price(
            w, c["spot"], c["strike"], c["time"], c["rate"], c["dividend_yield"], c["vol"]
        )
        assert gen._s(fresh) == c["price"]
