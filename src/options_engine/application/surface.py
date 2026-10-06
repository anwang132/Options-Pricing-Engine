"""Quality-aware SSVI surface calibration with immutable fit artifacts (ADR 0015).

Pipeline for one snapshot:
1. Eligibility: accepted quotes with European exercise, standard deliverable and
   no settlement lag. American or unknown exercise is excluded with a reason —
   an American quote is not a European BSM observation.
2. Forward and discount per expiry from put-call parity on mid prices
   (C - P = D*F - D*K, OLS over strikes quoted on both sides); the snapshot's
   carry inputs are the documented fallback. The method is recorded per expiry.
3. One out-of-the-money quote per strike (puts below F, calls at/above F).
   Every 4th strike per expiry (deterministic) is held out of the fit.
4. Price-space least squares: residual = (model - mid) / weight with
   weight = max(half-spread, abs floor, rel floor * mid), so no quote with a
   tiny spread dominates. Parameters are constrained so the SSVI no-arbitrage
   conditions hold (models/ssvi.py). Several deterministic restarts.
5. Diagnostics: in-sample vs held-out residuals and bid/ask containment;
   optional later-snapshot evaluation; sampled static-arbitrage checks on
   grids (reported as "no violations detected on the tested grid"), separate
   from the conditional parametric guarantee; recovery of the generating
   surface when the snapshot is synthetic.
6. The artifact (success or failure) is stored immutably under a content id.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import optimize

from options_engine.adapters.snapshots.store import ObjectStore, default_data_dir
from options_engine.analytics.implied_vol import SOLVED, implied_volatility
from options_engine.application.hashing import canonical_json, sha256_hex, to_jsonable
from options_engine.application.snapshots import SnapshotService, StoredSnapshot
from options_engine.domain.conventions import ExerciseStyle, OptionType, year_fraction
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.quotes import OptionQuote
from options_engine.engines.bsm_analytic import black_scholes_price
from options_engine.models.ssvi import SSVISurface, guarantee_conditions

FIT_CODE_VERSION = "1.3.0"


@dataclass(frozen=True, slots=True)
class SurfaceConfig:
    version: str = "1"
    min_quotes_per_expiry: int = 5
    min_expiries: int = 2
    min_parity_pairs: int = 4
    holdout_every: int = 4
    weight_floor_abs: float = 0.05
    weight_floor_rel: float = 0.005
    restarts: int = 5
    max_nfev_per_start: int = 3000
    seed: int = 7
    arbitrage_k_min: float = -1.5
    arbitrage_k_max: float = 1.5
    arbitrage_k_points: int = 301
    arbitrage_t_points: int = 60


@dataclass
class FitQuote:
    expiry: datetime
    T: float
    F: float
    D: float
    strike: float
    sign: int
    bid: float
    ask: float
    held_out: bool
    contract_id: str | None

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def k(self) -> float:
        return math.log(self.strike / self.F)


def black_price(
    sign: np.ndarray, F: np.ndarray, K: np.ndarray, T: np.ndarray, D: np.ndarray, vol: np.ndarray
) -> np.ndarray:
    """Undiscounted-forward Black price via the BSM kernel with S=F and r=q=-ln(D)/T."""
    rd = -np.log(D) / T
    return black_scholes_price(sign, F, K, T, rd, rd, vol)[0]


# --- data preparation -------------------------------------------------------------------------


def eligible(snap: StoredSnapshot) -> tuple[list[OptionQuote], dict[str, int]]:
    reasons: dict[str, int] = {}

    def excl(r: str) -> None:
        reasons[r] = reasons.get(r, 0) + 1

    out = []
    for q in snap.quotes:
        if not q.accepted:
            excl("quarantined_at_ingestion")
        elif q.exercise_style is not ExerciseStyle.EUROPEAN:
            excl(
                "american_exercise_not_european_observation"
                if q.exercise_style is ExerciseStyle.AMERICAN
                else "exercise_style_unknown"
            )
        elif q.settlement_lag_days not in (0, None) or q.deliverable not in ("standard", None):
            excl("unsupported_contract_terms")
        else:
            out.append(q)
    return out, reasons


def forwards(
    snap: StoredSnapshot, quotes: list[OptionQuote], cfg: SurfaceConfig
) -> dict[datetime, dict[str, Any]]:
    by_exp: dict[datetime, dict[float, dict[str, float]]] = {}
    for q in quotes:
        assert q.expiry is not None
        assert q.strike is not None
        assert q.option_type is not None
        mid = q.mid
        assert mid is not None
        by_exp.setdefault(q.expiry, {}).setdefault(float(q.strike), {})[q.option_type.value] = (
            float(mid)
        )
    carry = snap.manifest.get("carry")
    spot = float(snap.manifest["underlying"]["spot"])
    rate, yld = (
        (None, None) if carry is None else (float(carry["rate"]), float(carry["dividend_yield"]))
    )
    out: dict[datetime, dict[str, Any]] = {}
    for expiry, strikes in sorted(by_exp.items()):
        T = year_fraction(snap.as_of, expiry)
        pairs = [
            (K, v["call"] - v["put"]) for K, v in strikes.items() if "call" in v and "put" in v
        ]
        info: dict[str, Any] = {"T": T, "parity_pairs": len(pairs)}
        if len(pairs) >= cfg.min_parity_pairs:
            K = np.array([p[0] for p in pairs])
            y = np.array([p[1] for p in pairs])
            A = np.column_stack([np.ones_like(K), -K])
            (a, D), *_ = np.linalg.lstsq(A, y, rcond=None)
            resid = y - A @ np.array([a, D])
            if 0.0 < D <= 1.5 and a / D > 0:
                info.update(
                    method="put_call_parity_ols",
                    D=float(D),
                    F=float(a / D),
                    parity_residual_rms=float(np.sqrt(np.mean(resid**2))),
                )
                if len(pairs) > 2:
                    # OLS covariance of (a, D), mapped to (F, D) with F = a / D
                    s2 = float(resid @ resid) / (len(pairs) - 2)
                    cov_ad = s2 * np.linalg.inv(A.T @ A)
                    g = np.array([[1.0 / D, -a / D**2], [0.0, 1.0]])
                    info["cov_F_D"] = (g @ cov_ad @ g.T).tolist()
        if rate is not None and yld is not None:
            carry_forward = spot * math.exp((rate - yld) * T)
            if "method" not in info:
                info.update(method="snapshot_carry_inputs", D=math.exp(-rate * T), F=carry_forward)
            info["F_from_carry_inputs"] = carry_forward
        elif "method" not in info:
            info.update(method="unavailable")
        out[expiry] = info
    return out


def build_fit_quotes(
    quotes: list[OptionQuote], fwd: dict[datetime, dict[str, Any]], cfg: SurfaceConfig
) -> tuple[list[FitQuote], dict[str, int]]:
    reasons: dict[str, int] = {}
    out: list[FitQuote] = []
    by_exp: dict[datetime, list[OptionQuote]] = {}
    for q in quotes:
        assert q.expiry is not None
        by_exp.setdefault(q.expiry, []).append(q)
    for expiry, qs in sorted(by_exp.items()):
        info = fwd[expiry]
        if "F" not in info:
            reasons["no_forward_for_expiry"] = reasons.get("no_forward_for_expiry", 0) + len(qs)
            continue
        F = info["F"]
        chosen = []
        for q in sorted(qs, key=lambda q: (float(q.strike or 0), q.option_type)):
            K = float(q.strike or 0)
            otm = OptionType.PUT if K < F else OptionType.CALL
            if q.option_type is not otm:
                reasons["in_the_money_side_not_used"] = (
                    reasons.get("in_the_money_side_not_used", 0) + 1
                )
                continue
            chosen.append(q)
        for i, q in enumerate(chosen):
            out.append(
                FitQuote(
                    expiry,
                    info["T"],
                    F,
                    info["D"],
                    float(q.strike or 0),
                    1 if q.option_type is OptionType.CALL else -1,
                    float(q.bid or 0),
                    float(q.ask or 0),
                    held_out=(i % cfg.holdout_every == cfg.holdout_every // 2),
                    contract_id=q.contract_id,
                )
            )
    return out, reasons


# --- fitting --------------------------------------------------------------------------------------


def _unpack(x: np.ndarray, expiries: tuple[float, ...]) -> SSVISurface:
    rho, u, gamma, theta1 = x[0], x[1], x[2], x[3]
    thetas = theta1 + np.concatenate([[0.0], np.cumsum(x[4:])])
    return SSVISurface(
        float(rho),
        float(2.0 * u / (1.0 + abs(rho))),
        float(gamma),
        expiries,
        tuple(float(t) for t in thetas),
    )


def _arrays(qs: list[FitQuote]) -> dict[str, np.ndarray]:
    return {
        "sign": np.array([q.sign for q in qs], float),
        "F": np.array([q.F for q in qs]),
        "K": np.array([q.strike for q in qs]),
        "T": np.array([q.T for q in qs]),
        "D": np.array([q.D for q in qs]),
        "k": np.array([q.k for q in qs]),
        "mid": np.array([q.mid for q in qs]),
        "bid": np.array([q.bid for q in qs]),
        "ask": np.array([q.ask for q in qs]),
    }


def model_prices(surface: SSVISurface, a: dict[str, np.ndarray]) -> np.ndarray:
    vol = surface.implied_vol(a["k"], a["T"])
    return black_price(a["sign"], a["F"], a["K"], a["T"], a["D"], vol)


def weights(a: dict[str, np.ndarray], cfg: SurfaceConfig) -> np.ndarray:
    half = 0.5 * (a["ask"] - a["bid"])
    floor = np.maximum(half, cfg.weight_floor_abs)
    return np.asarray(np.maximum(floor, cfg.weight_floor_rel * a["mid"]), dtype=np.float64)


def _stats(surface: SSVISurface, a: dict[str, np.ndarray], cfg: SurfaceConfig) -> dict[str, Any]:
    return quote_stats(model_prices(surface, a) if a["K"].size else a["K"], a, cfg)


def black_iv_array(a: dict[str, np.ndarray], prices: np.ndarray) -> np.ndarray:
    """Black implied vols of ``prices`` at each quote's forward/discount (NaN if unsolvable)."""
    out = np.full(prices.shape, np.nan)
    for i, price in enumerate(prices):
        rd = -math.log(a["D"][i]) / a["T"][i]
        r = implied_volatility(
            float(price), int(a["sign"][i]), a["F"][i], a["K"][i], a["T"][i], rd, rd
        )
        if r.status in SOLVED and r.implied_vol is not None:
            out[i] = r.implied_vol
    return out


def quote_stats(model: np.ndarray, a: dict[str, np.ndarray], cfg: SurfaceConfig) -> dict[str, Any]:
    """Fit statistics of model prices against quotes; any model."""
    if a["K"].size == 0:
        return {"n": 0}
    r = model - a["mid"]
    z = r / weights(a, cfg)
    inside = (model >= a["bid"]) & (model <= a["ask"])
    iv_err = black_iv_array(a, model) - black_iv_array(a, a["mid"])
    solved = np.isfinite(iv_err)
    return {
        "n": int(a["K"].size),
        "price_rmse": float(np.sqrt(np.mean(r**2))),
        "max_abs_price_error": float(np.max(np.abs(r))),
        "weighted_residual_rms": float(np.sqrt(np.mean(z**2))),
        "max_abs_weighted_residual": float(np.max(np.abs(z))),
        "bid_ask_containment": float(np.mean(inside)),
        "iv_rmse_vol_points": float(100 * np.sqrt(np.mean(iv_err[solved] ** 2)))
        if solved.any()
        else None,
        "iv_unsolved": int((~solved).sum()),
    }


def arbitrage_diagnostics(s: SSVISurface, cfg: SurfaceConfig) -> dict[str, Any]:
    k = np.linspace(cfg.arbitrage_k_min, cfg.arbitrage_k_max, cfg.arbitrage_k_points)
    ts = np.unique(
        np.concatenate(
            [
                np.array(s.expiries),
                np.linspace(s.expiries[0] / 4, s.expiries[-1] * 1.5, cfg.arbitrage_t_points),
            ]
        )
    )
    min_g = min(float(np.min(s.butterfly_density(k, float(t)))) for t in ts)
    W = np.array([s.total_variance(k, t) for t in ts])
    calendar_viol = int(np.sum(np.diff(W, axis=0) < -1e-14))
    # Normalised undiscounted call prices c(k) = C/(D F) must be decreasing and convex in K.
    mono_viol = conv_viol = 0
    for t in ts:
        vol = s.implied_vol(k, t)
        K = np.exp(k)
        c = black_price(
            np.ones_like(k), np.ones_like(k), K, np.full_like(k, t), np.ones_like(k), vol
        )
        dc = np.diff(c) / np.diff(K)
        mono_viol += int(np.sum(dc > 1e-12))
        conv_viol += int(np.sum(np.diff(dc) < -1e-9))
    total = calendar_viol + mono_viol + conv_viol + (min_g < -1e-12)
    return {
        "grid": {
            "k": [cfg.arbitrage_k_min, cfg.arbitrage_k_max, cfg.arbitrage_k_points],
            "maturities": int(ts.size),
            "t_range": [float(ts[0]), float(ts[-1])],
        },
        "min_butterfly_density_g": min_g,
        "calendar_violations": calendar_viol,
        "strike_monotonicity_violations": mono_viol,
        "strike_convexity_violations": conv_viol,
        "sampled_violations_total": int(total),
        "statement": (
            "no violations detected on the tested grid"
            if total == 0
            else f"{total} violations detected on the tested grid"
        ),
    }


def truth_recovery(
    s: SSVISurface, snap: StoredSnapshot, k_range: tuple[float, float]
) -> dict[str, Any] | None:
    meta = snap.manifest.get("synthetic_metadata", {}).get("synthetic_truth")
    if not meta or "surface" not in meta:  # no truth, or generated by another model
        return None
    sd = meta["surface"]
    truth = SSVISurface(
        sd["rho"], sd["eta"], sd["gamma"], tuple(sd["expiries"]), tuple(sd["thetas"])
    )
    k = np.linspace(k_range[0], k_range[1], 101)
    errs = [
        float(np.max(np.abs(s.implied_vol(k, t) - truth.implied_vol(k, t)))) for t in s.expiries
    ]
    return {
        "generating_surface": sd,
        "k_range": list(k_range),
        "max_abs_iv_error_by_expiry": errs,
        "max_abs_iv_error": max(errs),
        "param_errors": {
            "rho": s.rho - truth.rho,
            "eta": s.eta - truth.eta,
            "gamma": s.gamma - truth.gamma,
        },
    }


class SurfaceService:
    def __init__(self, data_dir: Path | None = None, config: SurfaceConfig | None = None) -> None:
        root = data_dir or default_data_dir()
        self.snapshots = SnapshotService(root)
        self.store = ObjectStore(root, "fits")
        self.config = config or SurfaceConfig()

    def fit_id(self, snapshot_id: str, later_snapshot_id: str | None) -> str:
        key = canonical_json(
            {
                "snapshot": snapshot_id,
                "later": later_snapshot_id,
                "config": self.config,
                "code": FIT_CODE_VERSION,
            }
        )
        return "fit-" + sha256_hex(key)[:16]

    def fit(
        self, snapshot_id: str, later_snapshot_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Returns (artifact, created). An existing artifact is returned unchanged."""
        fid = self.fit_id(snapshot_id, later_snapshot_id)
        if self.store.exists(fid):
            return self.artifact(fid), False
        snap = self.snapshots.load(snapshot_id)
        later = self.snapshots.load(later_snapshot_id) if later_snapshot_id else None
        artifact = self._fit(fid, snap, later)
        self.store.put(fid, {"artifact.json": json.dumps(to_jsonable(artifact), indent=1).encode()})
        return self.artifact(fid), True

    def _fit(self, fid: str, snap: StoredSnapshot, later: StoredSnapshot | None) -> dict[str, Any]:
        cfg = self.config
        base: dict[str, Any] = {
            "fit_id": fid,
            "created_at": datetime.now(UTC),
            "code_version": FIT_CODE_VERSION,
            "snapshot_id": snap.snapshot_id,
            "snapshot_as_of": snap.as_of,
            "snapshot_synthetic": snap.manifest["synthetic"],
            "underlying": snap.manifest["underlying"]["id"],
            "later_snapshot_id": None if later is None else later.snapshot_id,
            "config": asdict(cfg),
            "model": "SSVI with power-law phi (Gatheral & Jacquier 2014)",
            "objective": "sum of squared (model price - mid) / weight over in-sample OTM quotes",
            "weights": "max(half-spread, weight_floor_abs, weight_floor_rel * mid)",
            "constraints": "|rho|<1, eta=2u/(1+|rho|) with u in (0,1], gamma in [0.01,0.5], "
            "theta_1>0 and non-negative theta increments",
        }
        quotes, excluded = eligible(snap)
        fwd = forwards(snap, quotes, cfg)
        fit_quotes, excluded2 = build_fit_quotes(quotes, fwd, cfg)
        exclusions = {**snap.manifest["quality"]["quarantine_reasons"], **excluded, **excluded2}
        by_exp: dict[float, int] = {}
        for q in fit_quotes:
            if not q.held_out:
                by_exp[q.T] = by_exp.get(q.T, 0) + 1
        usable = sorted(T for T, n in by_exp.items() if n >= cfg.min_quotes_per_expiry)
        dropped = {T: n for T, n in by_exp.items() if n < cfg.min_quotes_per_expiry}
        if dropped:
            exclusions["expiry_below_min_quotes"] = sum(dropped.values())
        fit_quotes = [q for q in fit_quotes if q.T in usable]
        base.update(forwards={e.isoformat(): v for e, v in fwd.items()}, exclusions=exclusions)
        if len(usable) < cfg.min_expiries:
            return {
                **base,
                "status": "failed",
                "failure_reason": "insufficient_data",
                "failure_detail": f"{len(usable)} expiries with >= {cfg.min_quotes_per_expiry} "
                f"in-sample quotes; need {cfg.min_expiries}",
            }
        expiries = tuple(usable)
        ins = [q for q in fit_quotes if not q.held_out]
        out = [q for q in fit_quotes if q.held_out]
        a_in, a_out = _arrays(ins), _arrays(out)
        w_in = weights(a_in, cfg)

        def resid(x: np.ndarray) -> np.ndarray:
            try:
                s = _unpack(x, expiries)
            except DomainError:
                return np.full(a_in["K"].size, 1e6)
            return np.asarray((model_prices(s, a_in) - a_in["mid"]) / w_in)

        starts, lo, hi = _initial_points(ins, expiries, cfg)
        runs, best = _optimize(resid, starts, lo, hi, cfg)
        base.update(
            initializations=[x.tolist() for x in starts],
            optimizer_runs=runs,
            in_sample_quotes=len(ins),
            held_out_quotes=len(out),
        )
        if best is None:
            return {
                **base,
                "status": "failed",
                "failure_reason": "optimizer_did_not_converge",
                "failure_detail": "no restart reported success",
            }
        surface = _unpack(best, expiries)
        artifact = {
            **base,
            "status": "ok",
            "surface": surface.to_dict(),
            "guarantee": {
                "conditions": guarantee_conditions(surface),
                "statement": "Free of static arbitrage for all k and t under the stated "
                "interpolation/extrapolation of theta, by Gatheral & Jacquier (2014) "
                "Theorems 4.1-4.2 with power-law phi (conditions checked above). This is a "
                "property of the fitted parametric surface, not of the market quotes.",
            },
            "arbitrage_diagnostics": arbitrage_diagnostics(surface, cfg),
            "bound_hits": _bound_hits(best, lo, hi, len(expiries)),
            "in_sample": _stats(surface, a_in, cfg),
            "held_out": _stats(surface, a_out, cfg),
            "residuals": _residual_rows(surface, fit_quotes, cfg),
            "truth_recovery": truth_recovery(surface, snap, (-0.3, 0.2)),
        }
        if later is not None:
            artifact["later_snapshot"] = self._evaluate_later(surface, snap, later)
        return artifact

    def _evaluate_later(
        self, surface: SSVISurface, snap: StoredSnapshot, later: StoredSnapshot
    ) -> dict[str, Any]:
        cfg = self.config
        quotes, _ = eligible(later)
        fwd = forwards(later, quotes, cfg)
        fq, _ = build_fit_quotes(quotes, fwd, cfg)
        stats = _stats(surface, _arrays(fq), cfg)
        return {
            "snapshot_id": later.snapshot_id,
            "as_of": later.as_of,
            "method": "surface held fixed in (k = ln(K/F), T) and evaluated at the later "
            "snapshot's forwards and maturities (no refit)",
            "elapsed_days": (later.as_of - snap.as_of).total_seconds() / 86400,
            **stats,
        }

    def artifact(self, fit_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.store.read_json(fit_id, "artifact.json")
        return data

    def surface(self, fit_id: str) -> tuple[SSVISurface, dict[str, Any]]:
        art = self.artifact(fit_id)
        if art["status"] != "ok":
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                f"{fit_id} is a failed fit and has no surface",
                {"failure_reason": art.get("failure_reason")},
            )
        sd = art["surface"]
        return SSVISurface(
            sd["rho"], sd["eta"], sd["gamma"], tuple(sd["expiries"]), tuple(sd["thetas"])
        ), art

    def list(self) -> list[dict[str, Any]]:
        snap_times = {s["snapshot_id"]: s["as_of"] for s in self.snapshots.list()}
        latest_snapshot = max(snap_times.values()) if snap_times else None
        out = []
        for fid in self.store.ids():
            a = self.artifact(fid)
            out.append(
                {
                    "fit_id": fid,
                    "status": a["status"],
                    "created_at": a["created_at"],
                    "snapshot_id": a["snapshot_id"],
                    "snapshot_as_of": a["snapshot_as_of"],
                    "failure_reason": a.get("failure_reason"),
                    "held_out_containment": (a.get("held_out") or {}).get("bid_ask_containment"),
                    "stale": latest_snapshot is not None and a["snapshot_as_of"] < latest_snapshot,
                }
            )
        return sorted(out, key=lambda r: (r["snapshot_as_of"], r["created_at"]))


PARAM_LO = (-0.999, 1e-4, 0.01, 1e-6)  # rho, u, gamma, theta_1
PARAM_HI = (0.999, 1.0, 0.5, 4.0)
THETA_INCREMENT_MAX = 4.0


def _initial_points(
    ins: list[FitQuote], expiries: tuple[float, ...], cfg: SurfaceConfig
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """Deterministic starts: ATM total variance from the quote nearest each forward."""
    th0 = []
    for T in expiries:
        atm = min((q for q in ins if q.T == T), key=lambda q: abs(q.k))
        vol = _black_iv(atm, atm.mid)
        th0.append(max(0.04 * T, 1e-4) if vol is None else vol * vol * T)
    th0 = list(np.maximum.accumulate(np.maximum(th0, 1e-6)))
    n_inc = len(expiries) - 1
    lo = np.array([*PARAM_LO, *[0.0] * n_inc])
    hi = np.array([*PARAM_HI, *[THETA_INCREMENT_MAX] * n_inc])
    rng = np.random.default_rng(cfg.seed)
    starts = []
    for i in range(cfg.restarts):
        rho0, u0, g0 = (
            (-0.5, 0.5, 0.3)
            if i == 0
            else (rng.uniform(-0.9, 0.3), rng.uniform(0.1, 0.9), rng.uniform(0.05, 0.5))
        )
        starts.append(np.clip(np.array([rho0, u0, g0, th0[0], *np.diff(th0)]), lo, hi))
    return starts, lo, hi


def _optimize(
    resid: Any, starts: list[np.ndarray], lo: np.ndarray, hi: np.ndarray, cfg: SurfaceConfig
) -> tuple[list[dict[str, Any]], np.ndarray | None]:
    """Run every start; keep the lowest-cost converged solution."""
    runs: list[dict[str, Any]] = []
    best_x, best_cost = None, math.inf
    for x0 in starts:
        r = optimize.least_squares(
            resid, x0, bounds=(lo, hi), method="trf", x_scale="jac", max_nfev=cfg.max_nfev_per_start
        )
        runs.append(
            {
                "x0": x0.tolist(),
                "status": int(r.status),
                "message": r.message,
                "success": bool(r.success),
                "nfev": int(r.nfev),
                "cost": float(r.cost),
            }
        )
        if r.success and r.cost < best_cost:
            best_x, best_cost = r.x, float(r.cost)
    return runs, best_x


def _bound_hits(x: np.ndarray, lo: np.ndarray, hi: np.ndarray, n_expiries: int) -> list[str]:
    names = ["rho", "u", "gamma", "theta_1"] + [
        f"theta_increment_{i + 2}" for i in range(n_expiries - 1)
    ]
    tol = 1e-6 * (hi - lo)
    hits = []
    for name, v, low, high, t in zip(names, x, lo, hi, tol, strict=True):
        # A zero theta increment (flat ATM variance between expiries) is admissible.
        at_low = v - low < t and not name.startswith("theta_increment")
        if at_low or high - v < t:
            hits.append(name)
    return hits


def _residual_rows(
    surface: SSVISurface, quotes: list[FitQuote], cfg: SurfaceConfig
) -> list[dict[str, Any]]:
    a = _arrays(quotes)
    model = model_prices(surface, a)
    w = weights(a, cfg)
    rows = []
    for q, m, wt in zip(quotes, model, w, strict=True):
        rows.append(
            {
                "expiry": q.expiry,
                "T": q.T,
                "strike": q.strike,
                "k": q.k,
                "type": "call" if q.sign > 0 else "put",
                "bid": q.bid,
                "ask": q.ask,
                "mid": q.mid,
                "model": float(m),
                "residual": float(m - q.mid),
                "weight": float(wt),
                "inside_bid_ask": bool(q.bid <= m <= q.ask),
                "held_out": q.held_out,
                "model_iv": float(surface.implied_vol(q.k, q.T)),
                "iv_bid": _black_iv(q, q.bid),
                "iv_mid": _black_iv(q, q.mid),
                "iv_ask": _black_iv(q, q.ask),
                "contract_id": q.contract_id,
            }
        )
    return rows


def _black_iv(q: FitQuote, price: float) -> float | None:
    """Black implied vol of ``price`` for this quote's forward/discount; None if not solvable."""
    rd = -math.log(q.D) / q.T
    r = implied_volatility(price, q.sign, q.F, q.strike, q.T, rd, rd)
    return r.implied_vol if r.status in SOLVED else None
