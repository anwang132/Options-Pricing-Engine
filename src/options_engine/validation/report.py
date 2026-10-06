"""Single-command validation report: ``options-engine validate --out reports/latest``.

Writes report.json (machine-readable, complete), report.md (readable summary)
and plots/*.svg. Exit code 0 only if every gate passes.
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from options_engine.adapters.environment import environment_info
from options_engine.application.hashing import to_jsonable
from options_engine.domain.conventions import OptionType
from options_engine.validation import (
    calibration,
    checks,
    hedging,
    heston_batch,
    lsm,
    term_structure,
)
from options_engine.validation.benchmarks import run_benchmarks
from options_engine.validation.policy import load_policy, policy_sha256
from options_engine.validation.statistical import run_suite

# Reference categorical palette (fixed order), ink and grid tokens (light surface).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def _deterministic() -> list[checks.CheckResult]:
    prices, greeks = checks.check_quantlib_european()
    return [
        checks.check_mpmath_prices(),
        checks.check_normalized_grid(),
        checks.check_mpmath_greeks(),
        prices,
        greeks,
        checks.check_published(),
        checks.check_identities(),
        checks.check_boundaries(),
        checks.check_fd_sweep(),
        checks.check_iv_round_trip(),
        checks.check_iv_failures(),
        checks.check_crr_vs_bsm(),
        checks.check_american_identities(),
        checks.check_american_vs_quantlib(),
        checks.check_surface_gate(),
        checks.check_heston_vs_quantlib(),
        checks.check_heston_limit(),
        checks.check_heston_parity(),
        checks.check_heston_integration_settings(),
        lsm.check_lsm_vs_bermudan(),
        lsm.check_lsm_se_calibration(),
        lsm.check_lsm_upper_bound(),
        heston_batch.check_heston_batch_vs_quantlib(),
        heston_batch.check_heston_batch_sweep(),
        heston_batch.check_heston_batch_greeks(),
        calibration.check_heston_calibration_exact(),
        calibration.check_heston_calibration_noisy(),
        hedging.check_hedging_gbm(),
        hedging.check_heston_simulator(),
        hedging.check_heston_min_variance_hedge(),
        term_structure.check_heston_ts_reduction(),
        term_structure.check_heston_ts_monte_carlo(),
        term_structure.check_heston_ts_exact_recovery(),
        term_structure.check_svi_slices_synthetic(),
    ]


def _convergence() -> dict[str, Any]:
    from options_engine.validation.policy import StressCase

    cases = {
        "atm_call_S100_K100_T1_v0.2_r0.03_q0.01": StressCase(
            "study", OptionType.CALL, 100, 100, 1, 0.2, 0.03, 0.01
        ),
        "otm_put_S100_K90_T0.5_v0.25_r0.03_q0": StressCase(
            "study", OptionType.PUT, 100, 90, 0.5, 0.25, 0.03, 0.0
        ),
    }
    steps = sorted({round(10 * 1.25**i) + d for i in range(26) for d in (0, 1)})
    k = load_policy()["tolerance"]["crr_vs_bsm"]["k"]
    out = {}
    for name, c in cases.items():
        rows = checks.crr_convergence_study(c.problem(), steps)
        for r in rows:
            r["bound"] = k * c.spot * c.vol * math.sqrt(c.time) / float(r["steps"])
        out[name] = rows
    return out


def _plot_all(report: dict[str, Any], plots: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK_2,
            "xtick.color": INK_2,
            "ytick.color": INK_2,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 1,
            "grid.linestyle": "-",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "legend.frameon": False,
            "svg.fonttype": "none",
        }
    )
    plots.mkdir(parents=True, exist_ok=True)
    written = []

    # 1. CRR convergence: small multiples, odd vs even steps.
    conv = report["convergence"]["crr"]
    fig, axes = plt.subplots(1, len(conv), figsize=(11, 4), sharey=True)
    for ax, (name, rows) in zip(axes, conv.items(), strict=True):
        for i, parity in enumerate(("even", "odd")):
            pts = [r for r in rows if (int(r["steps"]) % 2 == 1) == (parity == "odd")]
            ax.loglog(
                [p["steps"] for p in pts],
                [max(abs(p["error"]), 1e-16) for p in pts],
                color=SERIES[i],
                lw=2,
                marker="o",
                ms=4,
                label=f"{parity} N",
            )
        ax.loglog(
            [r["steps"] for r in rows],
            [r["bound"] for r in rows],
            color=INK_2,
            lw=1,
            label="policy bound k·S·σ·√T/N",
        )
        ax.set_title(name.replace("_", " "), fontsize=9, color=INK)
        ax.set_xlabel("tree steps N")
    axes[0].set_ylabel("|CRR − BSM| (price units)")
    axes[0].legend()
    fig.suptitle("CRR convergence to closed-form BSM: odd and even step counts", color=INK)
    fig.tight_layout()
    fig.savefig(plots / "crr_convergence.svg")
    plt.close(fig)
    written.append("crr_convergence.svg")

    # 2. MC coverage vs predeclared binomial acceptance band.
    stat = report["statistical"]["results"]
    case_names = list(dict.fromkeys(r["case"] for r in stat))
    methods = list(dict.fromkeys(r["method"] for r in stat))
    fig, ax = plt.subplots(figsize=(11, 4))
    lo, hi = stat[0]["coverage_region"]
    reps = stat[0]["replications"]
    ax.axhspan(lo / reps, hi / reps, color=GRID, alpha=0.6, lw=0, label="acceptance region")
    ax.axhline(report["statistical"]["confidence_level"], color=INK_2, lw=1)
    for j, m in enumerate(methods):
        xs = [i + (j - 1.5) * 0.15 for i in range(len(case_names))]
        ys = [
            next(r["coverage_rate"] for r in stat if r["case"] == c and r["method"] == m)
            for c in case_names
        ]
        ax.plot(
            xs,
            ys,
            ls="none",
            marker="o",
            ms=8,
            mec=SURFACE,
            mew=2,
            color=SERIES[j],
            label=m.replace("_", " "),
        )
    ax.set_xticks(range(len(case_names)), [c.replace("_", " ") for c in case_names])
    ax.set_ylabel(f"coverage of nominal 95% CI ({reps} replications)")
    ax.legend(ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout()
    fig.savefig(plots / "mc_coverage.svg")
    plt.close(fig)
    written.append("mc_coverage.svg")

    # 3. Error vs runtime.
    evr = report["benchmarks"]["error_vs_runtime"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = {
        "crr_tree_abs_error": "CRR tree: |error| vs BSM",
        "mc_plain_ci_half_width": "MC plain: 95% CI half-width",
        "mc_antithetic_cv_ci_half_width": "MC antithetic + CV: 95% CI half-width",
    }
    for i, (key, rows) in enumerate(evr.items()):
        ax.loglog(
            [r["seconds"] for r in rows],
            [r["abs_error"] for r in rows],
            color=SERIES[i],
            lw=2,
            marker="o",
            ms=5,
            label=labels.get(key, key),
        )
    ax.set_xlabel("median runtime per price (s)")
    ax.set_ylabel("error measure (price units)")
    ax.set_title(
        "ATM call: accuracy vs runtime (different error measures; see labels)",
        color=INK,
        fontsize=10,
    )
    ax.legend()
    fig.tight_layout()
    fig.savefig(plots / "error_vs_runtime.svg")
    plt.close(fig)
    written.append("error_vs_runtime.svg")

    # 4. FD sweep V-curves: small multiples, one series each.
    fd = next(c for c in report["deterministic"] if c["name"] == "greeks_fd_sweep")["details"][
        "example_curves"
    ]
    curves = fd["curves"]
    fig, axes = plt.subplots(2, 3, figsize=(11, 6))
    for ax, (g, pts) in zip(axes.ravel(), curves.items(), strict=False):
        ax.loglog(
            [p[0] for p in pts],
            [max(p[1], 1e-18) for p in pts],
            color=SERIES[0],
            lw=2,
            marker="o",
            ms=4,
        )
        ax.set_title(g, fontsize=9, color=INK)
        ax.set_xlabel("bump size h")
    axes[0][0].set_ylabel("|FD − analytic|")
    axes[1][0].set_ylabel("|FD − analytic|")
    fig.suptitle(
        f"Central-difference error vs bump size ({fd['case']}): truncation vs rounding",
        color=INK,
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(plots / "fd_sweep.svg")
    plt.close(fig)
    written.append("fd_sweep.svg")
    return written


def _fmt(x: Any, digits: int = 3) -> str:
    if isinstance(x, float):
        if x == 0 or math.isinf(x) or math.isnan(x):
            return str(x)
        return f"{x:.{digits}g}"
    return str(x)


def _markdown(r: dict[str, Any]) -> str:
    env = r["environment"]
    git = env.get("git", {})
    lines = [
        "# Validation report",
        "",
        f"- Generated: {r['generated_at']} ({'quick smoke run - not a gate' if r['quick'] else 'full run'})",
        f"- Overall: **{'PASS' if r['all_gates_passed'] else 'FAIL'}**",
        f"- Code: commit `{git.get('commit')}`, dirty={git.get('dirty')}, dirty-state sha256 `{git.get('dirty_state_sha256')}` (tracked diff + untracked files under result-affecting paths)",
        f"- Lockfile sha256 `{env.get('uv_lock_sha256')}`; policy sha256 `{r['policy_sha256']}`",
        f"- Platform: {env.get('platform')}; CPU {env.get('cpu')} ({env.get('logical_cpus')} logical); "
        f"Python {env.get('python')}, NumPy {env.get('numpy')}, SciPy {env.get('scipy')}, BLAS {env.get('blas')}",
        f"- Thread env: {env.get('thread_env')}",
        f"- Runtime of this report: {r['elapsed_seconds']:.1f} s",
        "",
        "## Deterministic gates",
        "",
        "| check | result | cases | failed | excluded | max abs error | max scaled error (≤1 passes) |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in r["deterministic"]:
        lines.append(
            f"| `{c['name']}` | {'pass' if c['passed'] else '**FAIL**'} | {c['n_cases']} | {c['n_failed']} | "
            f"{c['n_excluded']} | {_fmt(c['max_abs_error'])} | {_fmt(c['max_scaled_error'])} |"
        )
    lines += ["", "Descriptions, tolerances, exclusions and failures are in `report.json`.", ""]
    for c in r["deterministic"]:
        if c["exclusions"]:
            lines.append(f"- `{c['name']}` exclusions: {c['exclusions']}")
        if c["failures"]:
            lines.append(f"- `{c['name']}` first failures: {json.dumps(c['failures'][:3])}")
    s = r["statistical"]
    lines += [
        "",
        "## Monte Carlo statistical gates",
        "",
        f"{s['replications']} independent replications × {s['paths']} payoff evaluations per case and "
        f"method; master seed {s['master_seed']}; family α = {s['family_alpha']} split over "
        f"{s['n_tests']} tests (α per test = {_fmt(s['alpha_per_test'])}).",
        "",
        "| case | method | coverage | region | bias z | SE ratio | ratio region | var/eval | efficiency vs plain | pass |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in s["results"]:
        lines.append(
            f"| {row['case']} | {row['method']} | {row['coverage_count']}/{row['replications']} | "
            f"{row['coverage_region']} | {row['bias_z']:+.2f} | {row['se_ratio']:.3f} | "
            f"[{row['se_ratio_region'][0]:.3f}, {row['se_ratio_region'][1]:.3f}] | "
            f"{_fmt(row['variance_per_evaluation'])} | {_fmt(row['efficiency_vs_plain'])}× | "
            f"{'pass' if row['passed'] else '**FAIL**'} |"
        )
    lines += [
        "",
        "Efficiency vs plain = (variance × runtime) of plain / (variance × runtime) of the method, "
        "i.e. the speed-up at equal accuracy, measured on this machine.",
        "",
        "## Benchmarks",
        "",
    ]
    b = r["benchmarks"]
    cs = b["cold_start"]
    lines += [
        f"- Cold start (fresh interpreter, median of {cs['runs']}): import {cs['import_seconds_median'] * 1e3:.0f} ms, "
        f"first price {cs['first_price_seconds_median'] * 1e3:.2f} ms. {cs['note']}",
        f"- BSM scalar price + 6 Greeks (engine call): median {b['bsm_scalar_price_and_greeks']['median_seconds'] * 1e6:.1f} µs "
        f"(p10 {b['bsm_scalar_price_and_greeks']['p10_seconds'] * 1e6:.1f}, p90 {b['bsm_scalar_price_and_greeks']['p90_seconds'] * 1e6:.1f}).",
        f"- BSM vectorised batch, 1,000,000 mixed options: median {b['bsm_batch_price_1m']['median_seconds']:.3f} s "
        f"({b['bsm_batch_price_1m']['options_per_second_median']:.3g} options/s).",
        f"- CRR engine, 1000 steps, price + all Greeks (7 extra trees for bumps/odd-even): median "
        f"{b['crr_engine_price_all_greeks_1000']['median_seconds'] * 1e3:.1f} ms.",
        "",
        "| CRR single tree | steps | median ms | p90 ms | peak memory KiB |",
        "|---|---|---|---|---|",
    ]
    for t in b["crr_single_tree"]:
        lines.append(
            f"| {t['exercise']} | {t['steps']} | {t['median_seconds'] * 1e3:.2f} | {t['p90_seconds'] * 1e3:.2f} | {t['peak_bytes'] / 1024:.0f} |"
        )
    lines += [
        "",
        "| MC terminal | paths | chunk | median ms | peak memory KiB |",
        "|---|---|---|---|---|",
    ]
    for t in b["mc_terminal"]:
        lines.append(
            f"| antithetic | {t['paths']} | {t['chunk_size']} | {t['median_seconds'] * 1e3:.1f} | {t['peak_bytes'] / 1024:.0f} |"
        )
    rb = b["release_b"]
    lines += [
        "",
        f"- Surface fit incl. ingestion (synthetic day 1, 5 restarts): median {rb['surface_fit_incl_ingest']['median_seconds']:.3f} s.",
        f"- Portfolio scenarios, {rb['portfolio_typical']['description']}: median {rb['portfolio_typical']['median_seconds']:.3f} s.",
    ]
    if "portfolio_worst_case_seconds" in rb:
        lines.append(
            "- Portfolio worst cases at the work limits (single run, seconds): "
            + ", ".join(f"{k} {v:.1f}" for k, v in rb["portfolio_worst_case_seconds"].items())
            + "."
        )
    lines += ["", "### Before/after at comparable accuracy", ""]
    for imp in b["improvements"]:
        lines.append(
            f"- **{imp['name']}** ({imp['comparison']}): {imp['before']['method']} median "
            f"{imp['before']['median_seconds']:.3f} s → {imp['after']['method']} median "
            f"{imp['after']['median_seconds']:.3f} s; speed-up {imp['speedup_median']:.1f}×."
        )
        if "achieved_ci_half_width" in imp["before"]:
            lines.append(
                f"  Achieved CI half-widths: {imp['before']['achieved_ci_half_width']:.4f} (plain, "
                f"{imp['before']['paths']} paths) vs {imp['after']['achieved_ci_half_width']:.4f} "
                f"({imp['after']['paths']} paths)."
            )
    lines += ["", "## Plots", ""] + [f"![{p}](plots/{p})" for p in r["plots"]]
    lines += [
        "",
        "## Scope and limitations",
        "",
        "- Accuracy statements hold for the tested stress matrix and fixtures only.",
        "- CRR error bound is a predeclared regime-aware bound, not a theorem for all inputs.",
        "- American references (QuantLib FD) carry their own discretisation error.",
        "- Timings are specific to the recorded machine and thread settings.",
    ]
    return "\n".join(lines) + "\n"


def run_validation(out: Path, quick: bool = False) -> int:
    start = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    pol = load_policy()
    print("running deterministic checks ...", flush=True)
    det = [c.summary() for c in _deterministic()]
    print("running statistical suite ...", flush=True)
    stat_pol = pol["statistical"]
    reps = 60 if quick else stat_pol["replications"]
    results = run_suite(replications=reps)
    rows = []
    for res in results:
        d = res.to_dict()
        plain = next(x for x in results if x.case == res.case and x.method == "plain")
        cost = res.variance_per_evaluation * res.mean_seconds
        d["efficiency_vs_plain"] = (
            plain.variance_per_evaluation * plain.mean_seconds / cost if cost else math.inf
        )
        rows.append(d)
    n_tests = 3 * len(results)
    statistical = {
        "replications": reps,
        "paths": stat_pol["paths_per_replication"],
        "master_seed": stat_pol["master_seed"],
        "confidence_level": stat_pol["confidence_level"],
        "family_alpha": stat_pol["family_alpha"],
        "n_tests": n_tests,
        "alpha_per_test": stat_pol["family_alpha"] / n_tests,
        "results": rows,
        "all_passed": all(r["passed"] for r in rows),
    }
    print("running convergence studies and benchmarks ...", flush=True)
    report: dict[str, Any] = {
        "report_version": "1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "quick": quick,
        "environment": environment_info(include_git=True),
        "policy_sha256": policy_sha256(),
        "fixture_sha256": checks.fixture_hashes(),
        "deterministic": det,
        "statistical": statistical,
        "convergence": {"crr": _convergence()},
        "benchmarks": run_benchmarks(quick),
    }
    report["all_gates_passed"] = (
        all(c["passed"] for c in det) and statistical["all_passed"] and not quick
    )
    report["elapsed_seconds"] = time.perf_counter() - start
    report["plots"] = _plot_all(report, out / "plots")
    (out / "report.json").write_text(
        json.dumps(to_jsonable(report), indent=1, allow_nan=True) + "\n"
    )
    (out / "report.md").write_text(_markdown(report))
    status = (
        "PASS" if report["all_gates_passed"] else ("QUICK RUN (not a gate)" if quick else "FAIL")
    )
    print(f"{status}: wrote {out / 'report.md'} and {out / 'report.json'}")
    if quick:
        return 0 if all(c["passed"] for c in det) else 1
    return 0 if report["all_gates_passed"] else 1
