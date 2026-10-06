"""Delta-hedging simulator: setup validation, zero-mean hedging error, Heston paths."""

from __future__ import annotations

import math

import numpy as np
import pytest

from options_engine.application import hedging
from options_engine.domain.conventions import OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.models.heston import HestonModel

BASE = {
    "option_type": OptionType.CALL,
    "spot": 100.0,
    "strike": 100.0,
    "time": 0.5,
    "rate": 0.03,
    "dividend_yield": 0.01,
    "seed": 5,
}


def test_gbm_hedging_error_is_zero_mean_and_shrinks():
    out = hedging.run(hedging.HedgeSetup(**BASE, paths=4000, rebalances=(8, 32, 128), real_vol=0.2))
    rows = {r["rebalances"]: r for r in out["rows"]}
    for r in rows.values():
        assert abs(r["mean"]) < 4 * r["se_mean"] + 0.01
    assert rows[8]["std"] > rows[32]["std"] > rows[128]["std"]
    assert -0.6 < out["slope"]["bsm"] < -0.4
    assert rows[128]["std"] / rows[128]["leading_order_std"] == pytest.approx(1.0, abs=0.1)


def test_put_and_drift_do_not_bias_the_hedge():
    setup = dict(BASE, option_type=OptionType.PUT)
    out = hedging.run(
        hedging.HedgeSetup(**setup, paths=4000, rebalances=(64,), real_vol=0.2, drift=0.15)
    )
    r = out["rows"][0]
    assert abs(r["mean"]) < 4 * r["se_mean"] + 0.01  # delta hedging removes the drift


def test_seed_reproducible():
    a = hedging.run(hedging.HedgeSetup(**BASE, paths=500, rebalances=(16,), real_vol=0.2))
    b = hedging.run(hedging.HedgeSetup(**BASE, paths=500, rebalances=(16,), real_vol=0.2))
    assert np.array_equal(a["pnl"][("bsm", 16)], b["pnl"][("bsm", 16)])


def test_heston_qe_paths_are_martingale_and_nonnegative():
    m = HestonModel(0.09, 0.5, 0.04, 0.9, -0.9)  # Feller violated
    rng = np.random.default_rng(1)
    p = hedging.heston_qe_paths(100.0, 0.03, 0.0, m, 1.0, 100, 40_000, rng)
    assert p.variance is not None and (p.variance >= 0).all()
    disc = math.exp(-0.03) * p.spot[:, -1]
    assert abs(disc.mean() - 100.0) < 4 * disc.std() / math.sqrt(disc.size) + 0.02


def test_min_variance_delta_beats_heston_delta():
    m = HestonModel(0.04, 1.5, 0.04, 0.3, -0.7)
    out = hedging.run(
        hedging.HedgeSetup(
            **BASE, paths=2000, rebalances=(16,), heston=m, strategies=("heston", "heston_mv")
        )
    )
    std = {r["strategy"]: r["std"] for r in out["rows"]}
    assert std["heston_mv"] < std["heston"]


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        (
            {"real_vol": 0.2, "heston": HestonModel(0.04, 1, 0.04, 0.3, -0.5)},
            ErrorCode.INVALID_REQUEST,
        ),
        ({"real_vol": 0.2, "rebalances": (16, 24)}, ErrorCode.INVALID_REQUEST),
        ({"real_vol": 0.2, "strategies": ("heston",)}, ErrorCode.UNSUPPORTED_COMBINATION),
        ({"real_vol": 0.2, "strategies": ("magic",)}, ErrorCode.INVALID_REQUEST),
        ({"real_vol": 0.2, "paths": 100_000, "rebalances": (512,)}, ErrorCode.WORK_LIMIT_EXCEEDED),
    ],
)
def test_setup_validation(kw, code):
    args = {**BASE, "paths": 1000, "rebalances": (16,), **kw}
    with pytest.raises(DomainError) as e:
        hedging.HedgeSetup(**args)
    assert e.value.code is code
