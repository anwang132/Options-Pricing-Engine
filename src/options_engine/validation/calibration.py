"""Heston calibration acceptance checks (policy ``[heston_calibration]``, ADR 0020)."""

from __future__ import annotations

import math
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from options_engine.adapters.environment import PROJECT_ROOT
from options_engine.adapters.snapshots.synthetic import EXPIRY_DAYS, MONEYNESS
from options_engine.application.heston_calibration import (
    PARAM_NAMES,
    HestonCalibrationConfig,
    HestonCalibrationService,
    calibrate_arrays,
    model_prices,
)
from options_engine.application.surface import SurfaceConfig, weights
from options_engine.domain.conventions import year_fraction
from options_engine.models.heston import HestonModel, HestonTSModel
from options_engine.validation.checks import CheckResult
from options_engine.validation.policy import load_policy

SNAPSHOT_FIXTURES = PROJECT_ROOT / "fixtures" / "snapshots"


def _at_least(res: CheckResult, case: str, value: float | None, minimum: float) -> None:
    """Pass iff value >= minimum (recorded as a shortfall with zero tolerance)."""
    if value is None:
        res.fail(case, "metric unavailable")
        return
    res.record(case, max(0.0, minimum - value), minimum, 1e-12, 0.0, value=value)


def exact_chain(m: HestonModel | HestonTSModel) -> dict[str, np.ndarray]:
    """The synthetic generator's chain grid, OTM side, priced exactly (bid = ask = model)."""
    spot, r, q = 4500.0, 0.04, 0.015
    as_of = datetime(2026, 9, 28, 20, tzinfo=UTC)
    rows = []
    for days in EXPIRY_DAYS:
        T = year_fraction(as_of, as_of + timedelta(days=days))
        F, D = spot * math.exp((r - q) * T), math.exp(-r * T)
        for mny in MONEYNESS:
            K = round(spot * mny / 5) * 5
            rows.append((1.0 if K >= F else -1.0, F, float(K), T, D))
    arr = np.array(rows)
    a = {"sign": arr[:, 0], "F": arr[:, 1], "K": arr[:, 2], "T": arr[:, 3], "D": arr[:, 4]}
    a["k"] = np.log(a["K"] / a["F"])
    price = model_prices(m, a)
    a.update(mid=price, bid=price, ask=price)
    return a


def check_heston_calibration_exact() -> CheckResult:
    pol = load_policy()
    cp = pol["heston_calibration"]
    ex = cp["exact_recovery"]
    res = CheckResult(
        "heston_calibration_exact_recovery",
        "Calibration to exact Heston prices on the synthetic chain grid (5 expiries, strikes "
        "70-130%, OTM side) recovers the generating parameters",
        tolerance={"max_relative_parameter_error": ex["max_relative_parameter_error"]},
    )
    cfg = HestonCalibrationConfig(restarts=cp["restarts"])
    for name in ex["parameter_sets"]:
        truth = HestonModel(*pol["heston_parameter_sets"][name])
        a = exact_chain(truth)
        _, best = calibrate_arrays(a, weights(a, SurfaceConfig()), cfg)
        if best is None:
            res.fail(name, "optimizer did not converge")
            continue
        for i, p in enumerate(PARAM_NAMES):
            t = getattr(truth, p)
            res.record(
                f"{name}:{p}", float(best.x[i]) - t, t, 0.0, ex["max_relative_parameter_error"]
            )
    return res


def check_heston_calibration_noisy() -> CheckResult:
    pol = load_policy()
    cp = pol["heston_calibration"]
    nz = cp["noisy_snapshot"]
    res = CheckResult(
        "heston_calibration_noisy_snapshot",
        "Calibration to the noisy synthetic Heston snapshot (defects injected, parity forwards): "
        "held-out containment, IV error vs the generating model, parameter errors within "
        f"{nz['se_multiple']:g} standard errors, and next-day v0-only refit",
        tolerance=nz,
    )
    with tempfile.TemporaryDirectory() as tmp:
        svc = HestonCalibrationService(Path(tmp), HestonCalibrationConfig(restarts=cp["restarts"]))
        ids = [
            svc.snapshots.ingest_file(SNAPSHOT_FIXTURES / f)[0]["snapshot_id"]
            for f in nz["fixtures"]
        ]
        art, _ = svc.calibrate(ids[0], ids[1])
    if art["status"] != "ok":
        res.fail("calibration", f"failed: {art.get('failure_reason')}")
        return res
    _at_least(
        res,
        "held_out_bid_ask_containment",
        art["held_out"]["bid_ask_containment"],
        nz["min_holdout_bid_ask_containment"],
    )
    tr = art["truth_recovery"]
    res.record(
        "max_abs_iv_error_vs_truth",
        tr["max_abs_iv_error_vs_truth"],
        0.0,
        nz["max_abs_iv_error_vs_truth"],
        0.0,
    )
    se = art["uncertainty"]["standard_errors"]
    for p in PARAM_NAMES:
        err = tr["parameter_errors"][p]
        res.record(f"{p}_within_se", err, 0.0, nz["se_multiple"] * se[p], 0.0, se=se[p])
    v0_refit = art["later_snapshot"]["v0_refit"]
    _at_least(
        res,
        "day2_v0_refit_held_out_containment",
        v0_refit["held_out"]["bid_ask_containment"],
        nz["min_day2_v0_refit_containment"],
    )
    res.details = {
        "parameters": art["parameters"],
        "standard_errors": se,
        "standard_errors_quote_noise_only": art["uncertainty"]["standard_errors_quote_noise_only"],
        "errors_in_standard_errors": tr["errors_in_standard_errors"],
        "strongly_correlated_pairs": art["uncertainty"]["strongly_correlated_pairs"],
        "held_out": art["held_out"],
        "day2_no_refit_containment": art["later_snapshot"]["no_refit"]["bid_ask_containment"],
        "day2_v0_refit": {
            "v0": v0_refit["v0"],
            "truth_v0": v0_refit.get("truth_v0"),
            "containment": v0_refit["held_out"]["bid_ask_containment"],
        },
    }
    return res
