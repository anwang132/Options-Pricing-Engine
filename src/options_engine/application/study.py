"""Two-day model study on market snapshots: SSVI vs Heston (ADR 0023).

Day 1 is fitted by both models; day 2 is priced without refitting (and, for
Heston, with only the variance state refitted). The metrics are exactly those
fixed in advance in ``[real_data_study]`` of the validation policy, and models
are compared only on held-out and next-day metrics, never in-sample. There are
no pass/fail thresholds: real data has no known truth.

Output (default under the git-ignored data directory, because imported market
data may be licensed): report.md, report.json and one smile plot per day-1
expiry showing quotes, both fits and the day-2 quotes.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from options_engine.application.hashing import to_jsonable
from options_engine.application.heston_calibration import (
    HestonCalibrationConfig,
    HestonCalibrationService,
)
from options_engine.application.surface import SurfaceService
from options_engine.application.svi_slices import SviSliceService
from options_engine.domain.errors import DomainError, ErrorCode

STUDY_VERSION = "1"


def _metric(value: Any) -> float | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


MODEL_LABELS = {
    "ssvi": "SSVI",
    "heston": "Heston",
    "svi_slices": "SVI slices",
    "heston_ts": "Heston (theta term structure)",
}


def metrics(artifacts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The predeclared metrics for each model (None where a model has no such metric)."""

    def row(art: dict[str, Any]) -> dict[str, Any]:
        later = art.get("later_snapshot") or {}
        no_refit = later.get("no_refit", later)  # surface fits store later stats at top level
        v0 = later.get("v0_refit")
        return {
            "in_sample_weighted_rmse": art["in_sample"]["weighted_residual_rms"],
            "held_out_bid_ask_containment": art["held_out"].get("bid_ask_containment"),
            "held_out_iv_rmse_vol_points": art["held_out"].get("iv_rmse_vol_points"),
            "day2_bid_ask_containment_no_refit": no_refit.get("bid_ask_containment"),
            "day2_iv_rmse_vol_points_no_refit": no_refit.get("iv_rmse_vol_points"),
            "day2_bid_ask_containment_heston_v0_refit": None
            if v0 is None
            else v0["held_out"].get("bid_ask_containment"),
        }

    return {model: {k: _metric(v) for k, v in row(art).items()} for model, art in artifacts.items()}


def run_study(
    data_dir: Path | None,
    day1: str,
    day2: str,
    out: Path,
    predeclared: dict[str, Any],
    ts_pillars_days: tuple[int, ...],
) -> dict[str, Any]:
    surf = SurfaceService(data_dir)
    fits: dict[str, dict[str, Any]] = {}
    for model in predeclared["models"]:
        if model == "ssvi":
            fits[model] = surf.fit(day1, day2)[0]
        elif model == "heston":
            fits[model] = HestonCalibrationService(data_dir).calibrate(day1, day2)[0]
        elif model == "heston_ts":
            cfg = HestonCalibrationConfig(term_structure_pillars_days=ts_pillars_days)
            fits[model] = HestonCalibrationService(data_dir, cfg).calibrate(day1, day2)[0]
        elif model == "svi_slices":
            fits[model] = SviSliceService(data_dir).fit(day1, day2)[0]
        else:
            raise DomainError(ErrorCode.INVALID_REQUEST, f"unknown study model '{model}'")
    failed = {n: a.get("failure_reason") for n, a in fits.items() if a["status"] != "ok"}
    if failed:
        raise DomainError(ErrorCode.NUMERICAL_FAILURE, "a model failed to fit day 1", failed)
    table = metrics(fits)
    unknown = set(next(iter(table.values()))) - set(predeclared["metrics"])
    if unknown:  # guard: only predeclared metrics may appear in the study
        raise DomainError(ErrorCode.INVALID_REQUEST, f"undeclared metrics {sorted(unknown)}")
    manifest = surf.snapshots.manifest(day1)
    heston = fits.get("heston", {})
    report = {
        "study_version": STUDY_VERSION,
        "created_at": datetime.now(UTC),
        "day1_snapshot": day1,
        "day2_snapshot": day2,
        "underlying": manifest["underlying"]["id"],
        "synthetic": manifest["synthetic"],
        "source": manifest.get("source"),
        "predeclared": predeclared,
        "artifact_ids": {m: a.get("fit_id") or a.get("calibration_id") for m, a in fits.items()},
        "parameters": {
            m: {
                "values": a["parameters"],
                "standard_errors": a["uncertainty"]["standard_errors"],
                "strongly_correlated_pairs": a["uncertainty"]["strongly_correlated_pairs"],
            }
            for m, a in fits.items()
            if "parameters" in a
        },
        "svi_arbitrage": (fits.get("svi_slices") or {}).get("arbitrage_diagnostics"),
        "metrics": table,
        "comparison": {
            m: {model: table[model][m] for model in table}
            for m in predeclared["comparison_metrics"]
        },
    }
    if heston:
        report["heston_parameters"] = heston["parameters"]
    out.mkdir(parents=True, exist_ok=True)
    report["plots"] = _plots(fits, out)
    (out / "report.json").write_text(json.dumps(to_jsonable(report), indent=1) + "\n")
    (out / "report.md").write_text(_markdown(report))
    return report


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _markdown(r: dict[str, Any]) -> str:
    models = list(r["metrics"])
    names = list(r["metrics"][models[0]])
    lines = [
        "# Two-day model study",
        "",
        f"- Underlying: {r['underlying']}; synthetic data: {r['synthetic']}",
        f"- Source: {r['source']}",
        f"- Day 1 snapshot `{r['day1_snapshot']}`, day 2 snapshot `{r['day2_snapshot']}`",
        "- Artifacts: "
        + ", ".join(f"{MODEL_LABELS.get(m, m)} `{i}`" for m, i in r["artifact_ids"].items()),
        "- Metrics were fixed before this study ran (`[real_data_study]` in the validation "
        "policy); models are compared on held-out and next-day metrics only.",
        "",
        "| metric | " + " | ".join(MODEL_LABELS.get(m, m) for m in models) + " |",
        "|---" * (len(models) + 1) + "|",
        *[f"| {n} | " + " | ".join(_fmt(r["metrics"][m][n]) for m in models) + " |" for n in names],
        "",
    ]
    for model, p in r["parameters"].items():
        lines += [
            f"## {MODEL_LABELS.get(model, model)} parameters (SEs include forward uncertainty)",
            "",
            "| parameter | estimate | SE |",
            "|---|---|---|",
            *[f"| {k} | {v:.5g} | {p['standard_errors'][k]:.2g} |" for k, v in p["values"].items()],
            "",
            "Strongly correlated pairs (|corr| > 0.9): "
            + (
                ", ".join(f"{a}/{b} ({c:+.2f})" for a, b, c in p["strongly_correlated_pairs"])
                or "none"
            ),
            "",
        ]
    if r.get("svi_arbitrage"):
        lines += [f"SVI slices, arbitrage diagnostics: {r['svi_arbitrage']['statement']}.", ""]
    lines += ["## Plots", "", *[f"![{p}]({p})" for p in r["plots"]], ""]
    return "\n".join(lines)


def _plots(fits: dict[str, dict[str, Any]], out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from options_engine.models.ssvi import SSVISurface

    colours = {
        "ssvi": "#2a78d6",
        "heston": "#eb6834",
        "svi_slices": "#1baf7a",
        "heston_ts": "#eda100",
    }
    curves: dict[str, dict[tuple[float, float], float | None]] = {}
    for model, art in fits.items():
        if model != "ssvi":
            curves[model] = {(r["T"], r["strike"]): r["model_iv"] for r in art["residuals"]}
    base = next(
        iter(fits[m]["residuals"] for m in ("heston", "heston_ts", "svi_slices") if m in fits)
    )
    surface = None
    if "ssvi" in fits:
        sd = fits["ssvi"]["surface"]
        surface = SSVISurface(
            sd["rho"], sd["eta"], sd["gamma"], tuple(sd["expiries"]), tuple(sd["thetas"])
        )
    by_T: dict[float, list[dict[str, Any]]] = {}
    for row in base:
        by_T.setdefault(row["T"], []).append(row)
    names = []
    for i, (T, rows) in enumerate(sorted(by_T.items())):
        rows.sort(key=lambda r: r["k"])
        k = [r["k"] for r in rows]
        fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=110)
        ax.plot(
            k,
            [100 * (r["iv_mid"] or math.nan) for r in rows],
            "o",
            ms=3,
            color="#52514e",
            label="day-1 mid",
        )
        if surface is not None:
            ax.plot(
                k,
                [100 * float(surface.implied_vol(x, T)) for x in k],
                color=colours["ssvi"],
                label=MODEL_LABELS["ssvi"],
            )
        for model, ivs in curves.items():
            ax.plot(
                k,
                [100 * (ivs.get((T, r["strike"])) or math.nan) for r in rows],
                color=colours.get(model),
                label=MODEL_LABELS.get(model, model),
            )
        ax.set_xlabel("k = ln(K/F)")
        ax.set_ylabel("implied vol (%)")
        ax.set_title(f"T = {T:.3f} years")
        ax.legend(frameon=False, fontsize=7)
        fig.tight_layout()
        name = f"smile_{i + 1}.png"
        fig.savefig(out / name)
        plt.close(fig)
        names.append(name)
    return names
