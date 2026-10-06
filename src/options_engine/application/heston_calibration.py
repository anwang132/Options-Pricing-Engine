"""Heston calibration to a market snapshot, with parameter uncertainty (ADR 0020).

Uses the surface pipeline's data preparation unchanged (eligibility, parity
forwards, one out-of-the-money quote per strike, every 4th strike held out,
spread-based weights), so SSVI and Heston fits of one snapshot are directly
comparable. Then:

* Price-space weighted least squares over (v0, kappa, theta, sigma, rho) with
  box bounds, several deterministic restarts, prices from the vectorised
  pricer (engines/heston_batch.py).
* Parameter uncertainty from the Gauss-Newton approximation at the optimum:
  cov = s^2 (J'J)^-1 with s^2 the residual variance, reported with the
  correlation matrix and the condition number of the scaled J'J. Strongly
  correlated pairs (|corr| > 0.9) are named: the data do not separate them.
* Optional later snapshot: evaluated with all parameters held (no refit), and
  with only the variance state v0 refitted, which is how Heston is used day to
  day (kappa, theta, sigma, rho are structural; v0 is the state).
* Synthetic snapshots generated from Heston carry their truth; the artifact
  then reports parameter errors in units of their standard errors and the
  implied-vol error against the generating model.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import optimize

from options_engine.adapters.snapshots.store import ObjectStore, default_data_dir
from options_engine.application.hashing import canonical_json, sha256_hex, to_jsonable
from options_engine.application.snapshots import SnapshotService, StoredSnapshot
from options_engine.application.surface import (
    FitQuote,
    SurfaceConfig,
    _arrays,
    black_iv_array,
    build_fit_quotes,
    eligible,
    forwards,
    quote_stats,
    weights,
)
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.engines.heston_batch import heston_batch
from options_engine.models.heston import HestonModel, HestonTSModel

CALIBRATION_CODE_VERSION = "1.1.0"
PARAM_NAMES = ("v0", "kappa", "theta", "sigma", "rho")
LOWER = np.array([1e-4, 1e-3, 1e-4, 1e-2, -0.99])
UPPER = np.array([1.0, 20.0, 1.0, 3.0, 0.99])
STRONG_CORRELATION = 0.9
AnyHeston = HestonModel | HestonTSModel


@dataclass(frozen=True, slots=True)
class HestonCalibrationConfig:
    version: str = "1"
    restarts: int = 5
    max_nfev_per_start: int = 400
    seed: int = 7
    surface: SurfaceConfig = field(default_factory=SurfaceConfig)
    # None: constant parameters. Otherwise theta is piecewise constant between these
    # pillars (days from the valuation time); see models/heston.py HestonTSModel.
    term_structure_pillars_days: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class Parameterisation:
    """Parameter vector <-> model, with names, bounds and starting points."""

    names: tuple[str, ...]
    lower: np.ndarray
    upper: np.ndarray
    pillars: tuple[float, ...] | None  # years; None for constant parameters

    def model(self, x: np.ndarray) -> AnyHeston:
        v = [float(t) for t in x]
        if self.pillars is None:
            return HestonModel(v[0], v[1], v[2], v[3], v[4])
        return HestonTSModel(v[0], v[1], v[2], v[3], tuple(v[4:]), self.pillars)

    def values(self, m: AnyHeston) -> dict[str, float]:
        if isinstance(m, HestonTSModel):
            vals = [m.v0, m.kappa, m.sigma, m.rho, *m.thetas]
        else:
            # A constant model expressed in this parameterisation (theta in every bucket).
            vals = (
                [m.v0, m.kappa, m.theta, m.sigma, m.rho]
                if self.pillars is None
                else [m.v0, m.kappa, m.sigma, m.rho, *[m.theta] * (len(self.pillars) + 1)]
            )
        return dict(zip(self.names, vals, strict=True))

    def starts(self, a: dict[str, np.ndarray], cfg: HestonCalibrationConfig) -> list[np.ndarray]:
        """Base start from ATM Black vols, plus seeded draws around it."""
        ivs = black_iv_array(a, a["mid"])
        expiries = np.unique(a["T"])

        def atm_var(T: float) -> float:
            idx = np.flatnonzero((a["T"] == T) & np.isfinite(ivs))
            if idx.size == 0:
                return 0.04
            i = idx[np.argmin(np.abs(np.log(a["K"][idx] / a["F"][idx])))]
            return float(ivs[i] ** 2)

        v_short, v_long = atm_var(expiries[0]), atm_var(expiries[-1])
        if self.pillars is None:
            thetas = [v_long]
        else:
            # Each bucket starts at the ATM variance of the expiry nearest its far end.
            ends = [*self.pillars, expiries[-1]]
            thetas = [atm_var(expiries[np.argmin(np.abs(expiries - e))]) for e in ends]
        rng = np.random.default_rng(cfg.seed)

        def vector(
            v0: float, kappa: float, sigma: float, rho: float, th: list[float]
        ) -> list[float]:
            if self.pillars is None:
                return [v0, kappa, th[0], sigma, rho]
            return [v0, kappa, sigma, rho, *th]

        starts = [np.array(vector(v_short, 2.0, 0.5, -0.6, thetas))]
        for _ in range(cfg.restarts - 1):
            scale = rng.uniform(0.7, 1.3)
            starts.append(
                np.array(
                    vector(
                        v_short * rng.uniform(0.7, 1.3),
                        rng.uniform(0.5, 5.0),
                        rng.uniform(0.2, 1.2),
                        rng.uniform(-0.9, 0.0),
                        [t * scale for t in thetas],
                    )
                )
            )
        return [np.clip(x, self.lower, self.upper) for x in starts]


def parameterisation(cfg: HestonCalibrationConfig) -> Parameterisation:
    if cfg.term_structure_pillars_days is None:
        return Parameterisation(PARAM_NAMES, LOWER, UPPER, None)
    n = len(cfg.term_structure_pillars_days) + 1
    names = ("v0", "kappa", "sigma", "rho", *(f"theta_{i + 1}" for i in range(n)))
    lower = np.array([LOWER[0], LOWER[1], LOWER[3], LOWER[4], *[LOWER[2]] * n])
    upper = np.array([UPPER[0], UPPER[1], UPPER[3], UPPER[4], *[UPPER[2]] * n])
    pillars = tuple(d / 365.0 for d in cfg.term_structure_pillars_days)
    return Parameterisation(names, lower, upper, pillars)


def model_prices(m: AnyHeston, a: dict[str, np.ndarray]) -> np.ndarray:
    """Heston prices for quote arrays (grouped by maturity for the vectorised pricer)."""
    out = np.empty_like(a["K"])
    for T in np.unique(a["T"]):
        idx = a["T"] == T
        r = heston_batch(
            a["sign"][idx], a["F"][idx], a["K"][idx], float(a["D"][idx][0]), T, m.v0, m
        )
        out[idx] = r.price
    return out


def forward_sensitivity_cov(
    x: np.ndarray,
    jac: np.ndarray,
    a: dict[str, np.ndarray],
    fwd_by_T: dict[float, dict[str, Any]],
    residuals: Any,
) -> tuple[np.ndarray, list[float]]:
    """Covariance added to the parameters by uncertain forwards and discounts (delta method).

    Each expiry's (F, D) comes from the put-call-parity regression with covariance
    cov_F_D. The optimum moves with them as d(theta)/d(eta) = -(J'J)^-1 J' dr/d(eta),
    so cov_theta gains G Sigma_eta G'. Expiries whose forward came from carry inputs
    have no estimated covariance and contribute nothing (stated in the artifact).
    """
    r0 = residuals(x, a)
    cols, blocks, used = [], [], []
    for T, info in sorted(fwd_by_T.items()):
        cov = info.get("cov_F_D")
        idx = a["T"] == T
        if cov is None or not idx.any():
            continue
        for key in ("F", "D"):
            h = 1e-6 * float(a[key][idx][0])
            bumped = {k: v.copy() for k, v in a.items()}
            bumped[key][idx] += h
            cols.append((residuals(x, bumped) - r0) / h)
        blocks.append(np.asarray(cov))
        used.append(T)
    p = jac.shape[1]
    if not cols:
        return np.zeros((p, p)), used
    S = np.column_stack(cols)
    sigma = np.zeros((S.shape[1], S.shape[1]))
    for i, b in enumerate(blocks):
        sigma[2 * i : 2 * i + 2, 2 * i : 2 * i + 2] = b
    G = np.linalg.pinv(jac.T @ jac) @ jac.T @ S
    return G @ sigma @ G.T, used


def uncertainty(
    res: optimize.OptimizeResult,
    n: int,
    names: tuple[str, ...],
    extra_cov: np.ndarray | None = None,
) -> dict[str, Any]:
    """Gauss-Newton standard errors and correlations at the optimum.

    ``extra_cov`` (e.g. from uncertain forwards) is added to the quote-noise covariance.
    """
    J = np.asarray(res.jac)
    p = J.shape[1]
    dof = max(n - p, 1)
    s2 = float(2.0 * res.cost / dof)
    jtj = J.T @ J
    scale = np.sqrt(np.maximum(np.diag(jtj), 1e-300))
    scaled = jtj / np.outer(scale, scale)
    cov_quotes = s2 * np.linalg.pinv(jtj)
    cov = cov_quotes + (extra_cov if extra_cov is not None else 0.0)
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    denom = np.outer(se, se)
    corr = np.divide(cov, denom, out=np.zeros_like(cov), where=denom > 0)
    strong = [
        [names[i], names[j], float(corr[i, j])]
        for i in range(p)
        for j in range(i + 1, p)
        if abs(corr[i, j]) > STRONG_CORRELATION
    ]
    se_quotes = np.sqrt(np.maximum(np.diag(cov_quotes), 0.0))
    return {
        "method": "Gauss-Newton: cov = s^2 (J'J)^-1 at the optimum, s^2 = residual variance; "
        "plus forward/discount uncertainty from the parity regressions (delta method)",
        "residual_variance": s2,
        "degrees_of_freedom": dof,
        "standard_errors": dict(zip(names, se.tolist(), strict=True)),
        "standard_errors_quote_noise_only": dict(zip(names, se_quotes.tolist(), strict=True)),
        "correlation": corr.tolist(),
        "condition_number_scaled_jtj": float(np.linalg.cond(scaled)),
        "strongly_correlated_pairs": strong,
    }


def residuals(
    x: np.ndarray, a: dict[str, np.ndarray], w: np.ndarray, spec: Parameterisation | None = None
) -> np.ndarray:
    spec = spec or parameterisation(HestonCalibrationConfig())
    try:
        m = spec.model(x)
    except DomainError:
        return np.full(a["K"].size, 1e6)
    return np.asarray((model_prices(m, a) - a["mid"]) / w)


def calibrate_arrays(
    a: dict[str, np.ndarray], w: np.ndarray, cfg: HestonCalibrationConfig
) -> tuple[list[dict[str, Any]], optimize.OptimizeResult | None]:
    """Weighted least squares from several starts; returns (runs, best result)."""
    spec = parameterisation(cfg)

    def resid(x: np.ndarray) -> np.ndarray:
        return residuals(x, a, w, spec)

    runs: list[dict[str, Any]] = []
    best: optimize.OptimizeResult | None = None
    for x0 in spec.starts(a, cfg):
        r = optimize.least_squares(
            resid,
            x0,
            bounds=(spec.lower, spec.upper),
            method="trf",
            x_scale="jac",
            max_nfev=cfg.max_nfev_per_start,
        )
        runs.append(
            {
                "x0": x0.tolist(),
                "x": r.x.tolist(),
                "status": int(r.status),
                "success": bool(r.success),
                "nfev": int(r.nfev),
                "cost": float(r.cost),
            }
        )
        if r.success and (best is None or r.cost < best.cost):
            best = r
    return runs, best


def refit_v0(
    fixed: AnyHeston,
    a: dict[str, np.ndarray],
    w: np.ndarray,
    fwd_by_T: dict[float, dict[str, Any]] | None = None,
) -> tuple[AnyHeston, dict[str, Any]]:
    """Refit only the variance state v0 with every structural parameter held."""

    def resid_v0(x: np.ndarray, arrays: dict[str, np.ndarray]) -> np.ndarray:
        m = replace(fixed, v0=float(x[0]))
        return np.asarray((model_prices(m, arrays) - arrays["mid"]) / w)

    def fun(x: np.ndarray) -> np.ndarray:
        return resid_v0(x, a)

    r = optimize.least_squares(
        fun, np.array([fixed.v0]), bounds=([LOWER[0]], [UPPER[0]]), method="trf"
    )
    extra, _ = forward_sensitivity_cov(r.x, np.asarray(r.jac), a, fwd_by_T or {}, resid_v0)
    m = replace(fixed, v0=float(r.x[0]))
    return m, {
        "v0": m.v0,
        **uncertainty(r, a["K"].size, ("v0",), extra),
        "success": bool(r.success),
    }


def _truth(snap: StoredSnapshot) -> HestonModel | None:
    meta = snap.manifest.get("synthetic_metadata", {}).get("synthetic_truth") or {}
    h = meta.get("heston")
    return None if h is None else HestonModel(**h)


def truth_comparison(
    m: AnyHeston,
    se: dict[str, float],
    truth: HestonModel,
    a: dict[str, np.ndarray],
    spec: Parameterisation,
) -> dict[str, Any]:
    iv_fit = black_iv_array(a, model_prices(m, a))
    iv_true = black_iv_array(a, model_prices(truth, a))
    err = np.abs(iv_fit - iv_true)
    fitted, generating = spec.values(m), spec.values(truth)
    errors = {n: fitted[n] - generating[n] for n in spec.names}
    return {
        "generating_parameters": generating,
        "parameter_errors": errors,
        "errors_in_standard_errors": {
            n: (errors[n] / se[n]) if se[n] > 0 else None for n in spec.names
        },
        "max_abs_iv_error_vs_truth": float(np.nanmax(err)),
        "iv_points_compared": int(np.isfinite(err).sum()),
    }


def _by_maturity(fwd: dict[datetime, dict[str, Any]]) -> dict[float, dict[str, Any]]:
    return {float(info["T"]): info for info in fwd.values()}


def _prepare(
    snap: StoredSnapshot, cfg: SurfaceConfig
) -> tuple[list[FitQuote], dict[str, int], dict[datetime, dict[str, Any]]]:
    quotes, excluded = eligible(snap)
    fwd = forwards(snap, quotes, cfg)
    fq, excluded2 = build_fit_quotes(quotes, fwd, cfg)
    return fq, {**excluded, **excluded2}, fwd


class HestonCalibrationService:
    def __init__(
        self, data_dir: Path | None = None, config: HestonCalibrationConfig | None = None
    ) -> None:
        root = data_dir or default_data_dir()
        self.snapshots = SnapshotService(root)
        self.store = ObjectStore(root, "heston_fits")
        self.config = config or HestonCalibrationConfig()

    def calibration_id(self, snapshot_id: str, later_snapshot_id: str | None) -> str:
        key = canonical_json(
            {
                "snapshot": snapshot_id,
                "later": later_snapshot_id,
                "config": self.config,
                "code": CALIBRATION_CODE_VERSION,
            }
        )
        return "heston-" + sha256_hex(key)[:16]

    def calibrate(
        self, snapshot_id: str, later_snapshot_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        cid = self.calibration_id(snapshot_id, later_snapshot_id)
        if self.store.exists(cid):
            return self.artifact(cid), False
        snap = self.snapshots.load(snapshot_id)
        later = self.snapshots.load(later_snapshot_id) if later_snapshot_id else None
        art = self._calibrate(cid, snap, later)
        self.store.put(cid, {"artifact.json": json.dumps(to_jsonable(art), indent=1).encode()})
        return self.artifact(cid), True

    def artifact(self, calibration_id: str) -> dict[str, Any]:
        if not self.store.exists(calibration_id):
            raise DomainError(ErrorCode.INVALID_REQUEST, f"unknown calibration {calibration_id}")
        data: dict[str, Any] = self.store.read_json(calibration_id, "artifact.json")
        return data

    def list(self) -> list[dict[str, Any]]:
        out = []
        for cid in self.store.ids():
            a = self.artifact(cid)
            out.append(
                {
                    "calibration_id": cid,
                    "status": a["status"],
                    "created_at": a["created_at"],
                    "snapshot_id": a["snapshot_id"],
                    "later_snapshot_id": a.get("later_snapshot_id"),
                    "held_out_containment": (a.get("held_out") or {}).get("bid_ask_containment"),
                }
            )
        return sorted(out, key=lambda r: r["created_at"])

    def _calibrate(
        self, cid: str, snap: StoredSnapshot, later: StoredSnapshot | None
    ) -> dict[str, Any]:
        cfg, scfg = self.config, self.config.surface
        spec = parameterisation(cfg)
        base: dict[str, Any] = {
            "calibration_id": cid,
            "created_at": datetime.now(UTC),
            "code_version": CALIBRATION_CODE_VERSION,
            "snapshot_id": snap.snapshot_id,
            "snapshot_as_of": snap.as_of,
            "snapshot_synthetic": snap.manifest["synthetic"],
            "underlying": snap.manifest["underlying"]["id"],
            "later_snapshot_id": None if later is None else later.snapshot_id,
            "config": asdict(cfg),
            "model": "Heston (1993), prices by the vectorised Lewis integral"
            + (
                ""
                if spec.pillars is None
                else f"; theta piecewise constant, pillars {cfg.term_structure_pillars_days} days"
            ),
            "objective": "sum of squared (model price - mid) / weight over in-sample OTM quotes",
            "bounds": {
                n: [float(lo), float(hi)]
                for n, lo, hi in zip(spec.names, spec.lower, spec.upper, strict=True)
            },
        }
        fq, exclusions, fwd = _prepare(snap, scfg)
        ins = [q for q in fq if not q.held_out]
        out = [q for q in fq if q.held_out]
        base.update(
            exclusions={**snap.manifest["quality"]["quarantine_reasons"], **exclusions},
            forwards={e.isoformat(): v for e, v in fwd.items()},
            in_sample_quotes=len(ins),
            held_out_quotes=len(out),
        )
        if len(ins) < 10 or len({q.T for q in ins}) < 2:
            return {
                **base,
                "status": "failed",
                "failure_reason": "insufficient_data",
                "failure_detail": "need at least 10 in-sample quotes over 2 expiries",
            }
        a_in, a_out, a_all = _arrays(ins), _arrays(out), _arrays(fq)
        w_in = weights(a_in, scfg)
        runs, best = calibrate_arrays(a_in, w_in, cfg)
        base["optimizer_runs"] = runs
        if best is None:
            return {
                **base,
                "status": "failed",
                "failure_reason": "optimizer_did_not_converge",
                "failure_detail": "no restart reported success",
            }
        m = spec.model(best.x)
        extra, used = forward_sensitivity_cov(
            best.x,
            np.asarray(best.jac),
            a_in,
            _by_maturity(fwd),
            lambda x, arrays: residuals(x, arrays, w_in, spec),
        )
        unc = uncertainty(best, a_in["K"].size, spec.names, extra)
        unc["forward_uncertainty_expiries"] = len(used)
        art: dict[str, Any] = {
            **base,
            "status": "ok",
            "parameters": spec.values(m),
            "term_structure_pillars_days": cfg.term_structure_pillars_days,
            "uncertainty": unc,
            "feller_satisfied": m.feller_satisfied,
            "feller_ratio": 2 * m.kappa * _min_theta(m) / m.sigma**2,
            "bound_hits": [
                n
                for n, x, lo, hi in zip(spec.names, best.x, spec.lower, spec.upper, strict=True)
                if x - lo < 1e-6 * (hi - lo) or hi - x < 1e-6 * (hi - lo)
            ],
            "in_sample": quote_stats(model_prices(m, a_in), a_in, scfg),
            "held_out": quote_stats(model_prices(m, a_out), a_out, scfg) if out else {"n": 0},
            "residuals": _residual_rows(m, fq, a_all),
        }
        truth = _truth(snap)
        if truth is not None:
            art["truth_recovery"] = truth_comparison(m, unc["standard_errors"], truth, a_all, spec)
        if later is not None:
            art["later_snapshot"] = self._evaluate_later(m, snap, later)
        return art

    def _evaluate_later(
        self, m: AnyHeston, snap: StoredSnapshot, later: StoredSnapshot
    ) -> dict[str, Any]:
        scfg = self.config.surface
        fq, _, fwd = _prepare(later, scfg)
        ins = [q for q in fq if not q.held_out]
        out = [q for q in fq if q.held_out]
        a_all, a_in, a_out = _arrays(fq), _arrays(ins), _arrays(out)
        refit, info = refit_v0(m, a_in, weights(a_in, scfg), _by_maturity(fwd))
        result: dict[str, Any] = {
            "snapshot_id": later.snapshot_id,
            "as_of": later.as_of,
            "elapsed_days": (later.as_of - snap.as_of).total_seconds() / 86400,
            "no_refit": {
                "method": "all parameters held; later forwards and maturities",
                **quote_stats(model_prices(m, a_all), a_all, scfg),
            },
            "v0_refit": {
                "method": "structural parameters held; v0 refitted on the later in-sample "
                "quotes; statistics on the later held-out quotes",
                **info,
                "held_out": quote_stats(model_prices(refit, a_out), a_out, scfg),
            },
        }
        truth = _truth(later)
        if truth is not None:
            se = info["standard_errors"]["v0"]
            result["v0_refit"]["truth_v0"] = truth.v0
            result["v0_refit"]["v0_error_in_standard_errors"] = (
                (refit.v0 - truth.v0) / se if se > 0 else None
            )
        return result


def _min_theta(m: AnyHeston) -> float:
    return min(m.thetas) if isinstance(m, HestonTSModel) else m.theta


def _residual_rows(
    m: AnyHeston, quotes: list[FitQuote], a: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    model = model_prices(m, a)
    iv_model = black_iv_array(a, model)
    iv_mid, iv_bid, iv_ask = (black_iv_array(a, a[k]) for k in ("mid", "bid", "ask"))

    def f(x: float) -> float | None:
        return None if not math.isfinite(x) else float(x)

    return [
        {
            "expiry": q.expiry,
            "T": q.T,
            "strike": q.strike,
            "k": q.k,
            "type": "call" if q.sign > 0 else "put",
            "bid": q.bid,
            "ask": q.ask,
            "mid": q.mid,
            "model": float(model[i]),
            "inside_bid_ask": bool(q.bid <= model[i] <= q.ask),
            "held_out": q.held_out,
            "model_iv": f(iv_model[i]),
            "iv_bid": f(iv_bid[i]),
            "iv_mid": f(iv_mid[i]),
            "iv_ask": f(iv_ask[i]),
        }
        for i, q in enumerate(quotes)
    ]
