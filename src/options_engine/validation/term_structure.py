"""Checks for the term-structure models (policy ``[svi_slices]``, ``[heston_term_structure]``)."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

from options_engine.application import hedging
from options_engine.application.heston_calibration import (
    HestonCalibrationConfig,
    calibrate_arrays,
    parameterisation,
)
from options_engine.application.surface import SurfaceConfig, weights
from options_engine.application.svi_slices import SviConfig, SviSliceService
from options_engine.engines.heston_batch import heston_batch
from options_engine.models.heston import HestonTSModel
from options_engine.validation.calibration import SNAPSHOT_FIXTURES, _at_least, exact_chain
from options_engine.validation.checks import CheckResult, _heston_problem, load_fixture
from options_engine.validation.policy import load_policy


def _pillars() -> tuple[float, ...]:
    return tuple(d / 365.0 for d in load_policy()["heston_term_structure"]["pillars_days"])


def _ts_truth() -> HestonTSModel:
    mp = load_policy()["heston_term_structure"]["mc_parameters"]
    return HestonTSModel(
        mp["v0"], mp["kappa"], mp["sigma"], mp["rho"], tuple(mp["thetas"]), _pillars()
    )


def check_svi_slices_synthetic() -> CheckResult:
    sp = load_policy()["svi_slices"]
    res = CheckResult(
        "svi_slices_synthetic",
        "Per-expiry raw-SVI fits on the SSVI- and Heston-generated synthetic snapshots: held-out "
        "containment and sampled butterfly/calendar diagnostics",
        tolerance={
            k: sp[k]
            for k in (
                "min_holdout_bid_ask_containment",
                "max_butterfly_violations",
                "max_calendar_violations",
            )
        },
    )
    details = {}
    with tempfile.TemporaryDirectory() as tmp:
        svc = SviSliceService(Path(tmp), SviConfig(restarts=sp["restarts"]))
        for name in sp["fixtures"]:
            sid = svc.snapshots.ingest_file(SNAPSHOT_FIXTURES / name)[0]["snapshot_id"]
            art, _ = svc.fit(sid)
            if art["status"] != "ok":
                res.fail(name, f"fit failed: {art.get('failure_reason')}")
                continue
            arb = art["arbitrage_diagnostics"]
            _at_least(
                res,
                f"{name}:held_out_containment",
                art["held_out"]["bid_ask_containment"],
                sp["min_holdout_bid_ask_containment"],
            )
            res.record(
                f"{name}:butterfly",
                arb["butterfly_violations"],
                0.0,
                sp["max_butterfly_violations"] + 1e-12,
                0.0,
            )
            res.record(
                f"{name}:calendar",
                arb["calendar_violations"],
                0.0,
                sp["max_calendar_violations"] + 1e-12,
                0.0,
            )
            details[name] = {
                "held_out": art["held_out"]["bid_ask_containment"],
                "slices": len(art["slices"]),
                "min_butterfly_g": arb["min_butterfly_g"],
            }
    res.details = details
    return res


def check_heston_ts_reduction() -> CheckResult:
    ts = load_policy()["heston_term_structure"]
    fx = load_fixture("heston_quantlib_v1.json")
    res = CheckResult(
        "heston_ts_reduces_to_constant",
        "Term-structure Heston with every theta_i equal reproduces constant-parameter Heston "
        "(all 200 QuantLib fixture cases, vectorised pricer)",
        tolerance={"atol": ts["reduction_atol"]},
    )
    for c in fx["cases"]:
        p = _heston_problem(c)
        m = p.heston
        assert m is not None
        T = p.time_to_expiry
        D, F = math.exp(-p.rate * T), p.spot * math.exp((p.rate - p.dividend_yield) * T)
        tsm = HestonTSModel(
            m.v0, m.kappa, m.sigma, m.rho, (m.theta,) * (len(_pillars()) + 1), _pillars()
        )
        a = float(heston_batch(p.option_type.sign, F, p.strike, D, T, m.v0, m).price)
        b = float(heston_batch(p.option_type.sign, F, p.strike, D, T, m.v0, tsm).price)
        res.record(
            f"{c['parameter_set']}:{c['option_type']}:K{c['strike']}:d{c['days']}",
            b - a,
            a,
            ts["reduction_atol"],
            0.0,
        )
    return res


def check_heston_ts_monte_carlo() -> CheckResult:
    ts = load_policy()["heston_term_structure"]
    res = CheckResult(
        "heston_ts_vs_monte_carlo",
        "Piecewise-theta characteristic function vs QE Monte Carlo with theta(t) switching at "
        f"the pillars ({ts['mc_paths']:,} paths, {ts['mc_steps_per_year']} steps/year, grid "
        "containing the pillars)",
        tolerance={"z": ts["mc_z"], "price_atol": ts["mc_price_atol"]},
    )
    m = _ts_truth()
    s0, r, q = 100.0, 0.03, 0.01
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(20261001)))
    for T in ts["mc_times"]:
        n = math.ceil(ts["mc_steps_per_year"] * T)
        grid = np.unique(
            np.concatenate([np.linspace(0, T, n + 1), [p for p in m.pillars if p < T]])
        )
        paths = hedging.heston_qe_paths(
            s0, r, q, m, T, n, ts["mc_paths"], rng, times=grid, keep_paths=False
        )
        s_t = paths.spot[:, -1]
        D, F = math.exp(-r * T), s0 * math.exp((r - q) * T)
        for K in ts["mc_strikes"]:
            pay = D * np.maximum(s_t - K, 0.0)
            mc, se = float(pay.mean()), float(pay.std(ddof=1) / math.sqrt(pay.size))
            ref = float(heston_batch(1.0, F, K, D, T, m.v0, m).price)
            res.record(
                f"T{T:g}:K{K:g}",
                mc - ref,
                ref,
                ts["mc_z"] * se + ts["mc_price_atol"],
                0.0,
                mc=mc,
                se=se,
            )
    return res


def check_heston_ts_exact_recovery() -> CheckResult:
    ts = load_policy()["heston_term_structure"]
    tol = ts["exact_recovery_max_relative_error"]
    res = CheckResult(
        "heston_ts_exact_recovery",
        "Term-structure Heston calibrated to its own exact prices on the synthetic chain grid "
        "recovers all nine parameters",
        tolerance={"max_relative_error": tol},
    )
    truth = _ts_truth()
    cfg = HestonCalibrationConfig(
        restarts=load_policy()["heston_calibration"]["restarts"],
        term_structure_pillars_days=tuple(ts["pillars_days"]),
    )
    spec = parameterisation(cfg)
    a = exact_chain(truth)
    _, best = calibrate_arrays(a, weights(a, SurfaceConfig()), cfg)
    if best is None:
        res.fail("calibration", "optimizer did not converge")
        return res
    fitted, true = spec.values(spec.model(best.x)), spec.values(truth)
    for n in spec.names:
        res.record(n, fitted[n] - true[n], true[n], 0.0, tol, fitted=fitted[n])
    return res
