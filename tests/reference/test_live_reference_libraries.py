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
from options_engine.validation.policy import load_policy, within

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        importlib.util.find_spec("QuantLib") is None or importlib.util.find_spec("mpmath") is None,
        reason="reference dependency group not installed",
    ),
]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_quantlib_european_fixture_reproduces():
    """Prices must match the committed fixture within the predeclared tolerance.

    The tolerance is absolute (policy `fixture_reproduction.quantlib_price`): the
    price is a difference of two terms of order the spot, so near the money one
    ulp of the cumulative normal is worth ~2.7e-14 *relative*, and the deep tails
    price at 1e-45. Platform libm differences are bounded in price units, not in
    relative terms.
    """
    import generate_reference_fixtures as gen

    tol = load_policy()["tolerance"]["fixture_reproduction"]["quantlib_price"]
    fresh = gen.build_quantlib_european()["cases"]
    committed = checks.load_fixture("bsm_european_quantlib_v1.json")["cases"]
    assert len(fresh) == len(committed)
    drift = [
        f"{b['option_type']} K={b['strike']:g} days={b['days']} vol={b['vol']:g} "
        f"r={b['rate']:g} q={b['dividend_yield']:g}: {a['price']!r} vs {b['price']!r} "
        f"(delta {a['price'] - b['price']:.3g})"
        for a, b in zip(fresh, committed, strict=True)
        if not within(a["price"] - b["price"], b["price"], tol["atol"], tol["rtol"])
    ]
    assert not drift, "fixture drift in {} of {} cases:\n{}".format(
        len(drift), len(committed), "\n".join(drift[:10])
    )


def test_mpmath_fixture_sample_reproduces():
    import generate_reference_fixtures as gen

    committed = checks.load_fixture("bsm_european_mpmath_v1.json")["cases"][::25]
    for c in committed:
        w = 1 if c["option_type"] == "call" else -1
        fresh = gen.mp_price(
            w, c["spot"], c["strike"], c["time"], c["rate"], c["dividend_yield"], c["vol"]
        )
        assert gen._s(fresh) == c["price"]
