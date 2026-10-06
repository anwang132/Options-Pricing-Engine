"""Per-expiry raw-SVI smiles with arbitrage diagnostics (ADR 0024).

Each expiry gets its own five-parameter smile in total implied variance,

    w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + s^2)),   k = ln(K/F),

fitted in price space on exactly the quotes, forwards, weights and held-out
strikes of the SSVI pipeline (application/surface.py). The minimum of w is
parameterised directly (w_min >= 0, a = w_min - b s sqrt(1 - rho^2)), so total
variance can never go negative.

Unlike SSVI, independent slices carry no arbitrage guarantee, so both kinds are
checked on grids and reported, never silently repaired:
* butterfly: Gatheral's density factor g(k) >= 0 on each slice;
* calendar: w(k, T) non-decreasing in T between consecutive slices.

Between slices, total variance is interpolated linearly in T at fixed k (before the
first slice it scales with T; after the last it keeps the last slice's implied vol).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
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
    black_price,
    build_fit_quotes,
    eligible,
    forwards,
    quote_stats,
    weights,
)
from options_engine.domain.errors import DomainError, ErrorCode

SVI_CODE_VERSION = "1.1.0"
LOWER = np.array([0.0, 0.0, -0.999, -2.0, 1e-4])  # w_min, b, rho, m, s
UPPER = np.array([4.0, 10.0, 0.999, 2.0, 3.0])
G_TOLERANCE = -1e-9


@dataclass(frozen=True, slots=True)
class SviConfig:
    version: str = "1"
    restarts: int = 5
    max_nfev_per_start: int = 2000
    seed: int = 11
    butterfly_grid_points: int = 201
    calendar_k_range: tuple[float, float] = (-1.0, 1.0)
    surface: SurfaceConfig = field(default_factory=SurfaceConfig)


def raw_params(x: np.ndarray) -> tuple[float, float, float, float, float]:
    """(a, b, rho, m, s) from the fitted vector (w_min, b, rho, m, s)."""
    w_min, b, rho, m, s = (float(v) for v in x)
    return w_min - b * s * math.sqrt(1.0 - rho * rho), b, rho, m, s


def total_variance(p: tuple[float, ...], k: np.ndarray) -> np.ndarray:
    a, b, rho, m, s = p
    d = k - m
    return np.asarray(a + b * (rho * d + np.sqrt(d * d + s * s)))


def butterfly_g(p: tuple[float, ...], k: np.ndarray) -> np.ndarray:
    """Gatheral's g(k); the risk-neutral density is non-negative iff g >= 0."""
    a, b, rho, m, s = p
    d = k - m
    root = np.sqrt(d * d + s * s)
    w = a + b * (rho * d + root)
    w1 = b * (rho + d / root)
    w2 = b * s * s / root**3
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray((1 - k * w1 / (2 * w)) ** 2 - w1 * w1 / 4 * (1 / w + 0.25) + w2 / 2)


@dataclass(frozen=True, slots=True)
class SviSlices:
    expiries: tuple[float, ...]
    params: tuple[tuple[float, float, float, float, float], ...]

    def total_variance(self, k: np.ndarray, T: float) -> np.ndarray:
        Ts = self.expiries
        if Ts[0] >= T:
            return total_variance(self.params[0], k) * T / Ts[0]
        if Ts[-1] <= T:
            return total_variance(self.params[-1], k) * T / Ts[-1]
        j = int(np.searchsorted(Ts, T)) - 1
        t0, t1 = Ts[j], Ts[j + 1]
        lam = (T - t0) / (t1 - t0)
        return (1 - lam) * total_variance(self.params[j], k) + lam * total_variance(
            self.params[j + 1], k
        )

    def prices(self, a: dict[str, np.ndarray]) -> np.ndarray:
        out = np.empty_like(a["K"])
        for T in np.unique(a["T"]):
            idx = a["T"] == T
            w = np.maximum(self.total_variance(a["k"][idx], float(T)), 1e-14)
            out[idx] = black_price(
                a["sign"][idx], a["F"][idx], a["K"][idx], a["T"][idx], a["D"][idx], np.sqrt(w / T)
            )
        return out


def _fit_slice(
    a: dict[str, np.ndarray], w: np.ndarray, T: float, cfg: SviConfig
) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    ivs = black_iv_array(a, a["mid"])
    ok = np.isfinite(ivs)
    atm = float(ivs[ok][np.argmin(np.abs(a["k"][ok]))] ** 2 * T) if ok.any() else 0.04 * T
    rng = np.random.default_rng(cfg.seed)
    starts = [np.array([0.8 * atm, 0.1, -0.5, 0.0, 0.1])]
    for _ in range(cfg.restarts - 1):
        starts.append(
            np.array(
                [
                    atm * rng.uniform(0.3, 1.0),
                    rng.uniform(0.01, 1.0),
                    rng.uniform(-0.95, 0.5),
                    rng.uniform(-0.2, 0.2),
                    rng.uniform(0.01, 0.5),
                ]
            )
        )

    def resid(x: np.ndarray) -> np.ndarray:
        p = raw_params(x)
        tv = np.maximum(total_variance(p, a["k"]), 1e-14)
        model = black_price(a["sign"], a["F"], a["K"], a["T"], a["D"], np.sqrt(tv / T))
        return np.asarray((model - a["mid"]) / w)

    runs: list[dict[str, Any]] = []
    best = None
    for x0 in starts:
        r = optimize.least_squares(
            resid,
            np.clip(x0, LOWER, UPPER),
            bounds=(LOWER, UPPER),
            method="trf",
            x_scale="jac",
            max_nfev=cfg.max_nfev_per_start,
        )
        runs.append({"success": bool(r.success), "cost": float(r.cost), "nfev": int(r.nfev)})
        if r.success and (best is None or r.cost < best.cost):
            best = r
    return (None if best is None else best.x), runs


def arbitrage_diagnostics(
    s: SviSlices, k_ranges: list[tuple[float, float]], cfg: SviConfig
) -> dict[str, Any]:
    butterfly = 0
    worst_g = math.inf
    for p, (lo, hi) in zip(s.params, k_ranges, strict=True):
        k = np.linspace(lo - 0.25, hi + 0.25, cfg.butterfly_grid_points)
        g = butterfly_g(p, k)
        butterfly += int(np.sum(g < G_TOLERANCE))
        worst_g = min(worst_g, float(np.nanmin(g)))
    k = np.linspace(*cfg.calendar_k_range, cfg.butterfly_grid_points)
    calendar = calendar_quoted = 0
    pairs = list(zip(s.params, s.params[1:], k_ranges, k_ranges[1:], strict=False))
    for p0, p1, r0, r1 in pairs:
        calendar += int(np.sum(total_variance(p1, k) - total_variance(p0, k) < -1e-12))
        lo, hi = max(r0[0], r1[0]), min(r0[1], r1[1])
        if hi > lo:  # strikes quoted on both expiries
            kq = np.linspace(lo, hi, cfg.butterfly_grid_points)
            calendar_quoted += int(np.sum(total_variance(p1, kq) - total_variance(p0, kq) < -1e-12))
    total = butterfly + calendar
    return {
        "butterfly_violations": butterfly,
        "min_butterfly_g": worst_g,
        "calendar_violations": calendar,
        "calendar_violations_within_quoted_range": calendar_quoted,
        "grids": {
            "butterfly": "each slice's quoted k range +-0.25",
            "calendar": f"k in {list(cfg.calendar_k_range)}; also only where both expiries "
            "are quoted",
            "points": cfg.butterfly_grid_points,
        },
        "statement": "no violations detected on the tested grids"
        if total == 0
        else f"{butterfly} butterfly and {calendar} calendar violations on the tested grids "
        f"({calendar_quoted} calendar violations where both expiries are quoted; independent "
        "slices carry no arbitrage guarantee)",
    }


class SviSliceService:
    def __init__(self, data_dir: Path | None = None, config: SviConfig | None = None) -> None:
        root = data_dir or default_data_dir()
        self.snapshots = SnapshotService(root)
        self.store = ObjectStore(root, "svi_fits")
        self.config = config or SviConfig()

    def fit_id(self, snapshot_id: str, later_snapshot_id: str | None) -> str:
        key = canonical_json(
            {
                "snapshot": snapshot_id,
                "later": later_snapshot_id,
                "config": self.config,
                "code": SVI_CODE_VERSION,
            }
        )
        return "svi-" + sha256_hex(key)[:16]

    def fit(
        self, snapshot_id: str, later_snapshot_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        fid = self.fit_id(snapshot_id, later_snapshot_id)
        if self.store.exists(fid):
            return self.artifact(fid), False
        snap = self.snapshots.load(snapshot_id)
        later = self.snapshots.load(later_snapshot_id) if later_snapshot_id else None
        art = self._fit(fid, snap, later)
        self.store.put(fid, {"artifact.json": json.dumps(to_jsonable(art), indent=1).encode()})
        return self.artifact(fid), True

    def artifact(self, fit_id: str) -> dict[str, Any]:
        if not self.store.exists(fit_id):
            raise DomainError(ErrorCode.INVALID_REQUEST, f"unknown SVI fit {fit_id}")
        data: dict[str, Any] = self.store.read_json(fit_id, "artifact.json")
        return data

    def _fit(self, fid: str, snap: StoredSnapshot, later: StoredSnapshot | None) -> dict[str, Any]:
        cfg, scfg = self.config, self.config.surface
        quotes, excluded = eligible(snap)
        fwd = forwards(snap, quotes, scfg)
        fq, excluded2 = build_fit_quotes(quotes, fwd, scfg)
        base: dict[str, Any] = {
            "fit_id": fid,
            "created_at": datetime.now(UTC),
            "code_version": SVI_CODE_VERSION,
            "snapshot_id": snap.snapshot_id,
            "snapshot_as_of": snap.as_of,
            "later_snapshot_id": None if later is None else later.snapshot_id,
            "config": asdict(cfg),
            "model": "raw SVI per expiry (Gatheral 2004), total variance linear in T between "
            "slices",
            "exclusions": {
                **snap.manifest["quality"]["quarantine_reasons"],
                **excluded,
                **excluded2,
            },
        }
        by_T: dict[float, list[FitQuote]] = {}
        for q in fq:
            by_T.setdefault(q.T, []).append(q)
        expiries, params, k_ranges, slice_rows = [], [], [], []
        for T, qs in sorted(by_T.items()):
            ins = [q for q in qs if not q.held_out]
            if len(ins) < scfg.min_quotes_per_expiry:
                base["exclusions"]["expiry_below_min_quotes"] = base["exclusions"].get(
                    "expiry_below_min_quotes", 0
                ) + len(qs)
                continue
            a = _arrays(ins)
            x, runs = _fit_slice(a, weights(a, scfg), T, cfg)
            slice_rows.append(
                {
                    "T": T,
                    "expiry": qs[0].expiry,
                    "in_sample": len(ins),
                    "runs": runs,
                    "success": x is not None,
                }
            )
            if x is None:
                continue
            p = raw_params(x)
            slice_rows[-1]["raw"] = dict(zip(("a", "b", "rho", "m", "s"), p, strict=True))
            expiries.append(T)
            params.append(p)
            k_ranges.append((float(a["k"].min()), float(a["k"].max())))
        if len(expiries) < 1:
            return {
                **base,
                "status": "failed",
                "failure_reason": "insufficient_data",
                "failure_detail": "no expiry could be fitted",
                "slices": slice_rows,
            }
        model = SviSlices(tuple(expiries), tuple(params))
        used = [q for q in fq if q.T in set(expiries)]
        ins_all = [q for q in used if not q.held_out]
        out_all = [q for q in used if q.held_out]
        a_in, a_out, a_all = _arrays(ins_all), _arrays(out_all), _arrays(used)
        art: dict[str, Any] = {
            **base,
            "status": "ok",
            "slices": slice_rows,
            "arbitrage_diagnostics": arbitrage_diagnostics(model, k_ranges, cfg),
            "in_sample": quote_stats(model.prices(a_in), a_in, scfg),
            "held_out": quote_stats(model.prices(a_out), a_out, scfg) if out_all else {"n": 0},
            "residuals": _residual_rows(model, used, a_all),
        }
        if later is not None:
            lq, _ = eligible(later)
            lf = forwards(later, lq, scfg)
            later_fq, _ = build_fit_quotes(lq, lf, scfg)
            a_l = _arrays(later_fq)
            art["later_snapshot"] = {
                "snapshot_id": later.snapshot_id,
                "elapsed_days": (later.as_of - snap.as_of).total_seconds() / 86400,
                "method": "slices held fixed in (k, T), interpolated in total variance at the "
                "later maturities (no refit)",
                **quote_stats(model.prices(a_l), a_l, scfg),
            }
        return art


def _residual_rows(
    s: SviSlices, quotes: list[FitQuote], a: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    model = s.prices(a)
    iv_model = black_iv_array(a, model)
    iv_mid = black_iv_array(a, a["mid"])

    def f(x: float) -> float | None:
        return None if not math.isfinite(x) else float(x)

    return [
        {
            "T": q.T,
            "strike": q.strike,
            "k": q.k,
            "mid": q.mid,
            "model": float(model[i]),
            "inside_bid_ask": bool(q.bid <= model[i] <= q.ask),
            "held_out": q.held_out,
            "model_iv": f(iv_model[i]),
            "iv_mid": f(iv_mid[i]),
        }
        for i, q in enumerate(quotes)
    ]
