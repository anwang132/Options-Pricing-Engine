"""Command-line interface. Uses the same handlers as the HTTP API.

Examples:
  options-engine price --input examples/price_hull_call.json
  options-engine price --as-of 2026-09-28T20:00:00Z --expiry 2027-03-29T20:00:00Z \\
      --type call --spot 42 --strike 40 --rate 0.10 --vol 0.20 --engine crr_tree
  options-engine price --input examples/price_heston_call.json
  options-engine iv --input examples/iv_quotes.json
  options-engine replay manifest.json
  options-engine snapshot import-csv chain.csv --mapping m.toml --underlying SPX --spot 5000 \\
      --as-of 2026-09-29T20:00:00Z --ingest
  options-engine heston calibrate <snapshot-id> --later <snapshot-id>
  options-engine study <day1-snapshot-id> <day2-snapshot-id>
  options-engine hedge --heston 0.04,1.5,0.04,0.3,-0.7 --strategies bsm,heston,heston_mv
  options-engine validate --out reports/latest
  options-engine demo
  options-engine serve
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.interfaces import handlers
from options_engine.interfaces.schemas import (
    CompareRequestIn,
    ImpliedVolIn,
    PriceRequestIn,
    PriceResponse,
    RunManifest,
)


def _emit(obj: Any) -> None:
    if isinstance(obj, BaseModel):
        print(obj.model_dump_json(indent=2))
    else:
        print(json.dumps(obj, indent=2, default=str))


def _price_request_from_flags(a: argparse.Namespace) -> PriceRequestIn:
    engine: dict[str, Any] = {"engine": a.engine}
    if a.engine == "crr_tree" and a.steps:
        engine["steps"] = a.steps
    if a.engine in ("mc_terminal_gbm", "lsm_american"):
        engine.update({"paths": a.paths, "seed": a.seed})
    model: dict[str, Any] = {"volatility": a.vol}
    if a.heston:
        v0, kappa, theta, sigma, rho = (float(x) for x in a.heston.split(","))
        model = {"family": "heston", "v0": v0, "kappa": kappa, "theta": theta}
        model |= {"sigma": sigma, "rho": rho}
        engine = {"engine": "heston_fourier"}
    return PriceRequestIn.model_validate(
        {
            "contract": {
                "underlying": a.underlying,
                "currency": a.currency,
                "strike": a.strike,
                "option_type": a.type,
                "exercise_style": a.exercise,
                "expiry": a.expiry,
                "multiplier": a.multiplier,
            },
            "valuation": {"as_of": a.as_of},
            "market": {"spot": a.spot, "rate": a.rate, "dividend_yield": a.dividend_yield},
            "model": model,
            "engine": engine,
        }
    )


def _render_price(r: PriceResponse) -> str:
    lines = [
        f"engine        {r.engine_id} v{r.engine_version}",
        f"price         {r.price!r} {r.currency} per underlying unit",
        f"contract      {r.contract_value!r} {r.currency} (multiplier {r.multiplier})",
        f"T (years)     {r.time_to_expiry!r} ({r.assumptions.get('day_count')})",
        f"dividends     {r.dividend_treatment}",
    ]
    if r.uncertainty:
        u = r.uncertainty
        lines.append(
            f"sampling      SE {u.standard_error:.6g}; {u.confidence_level:.0%} CI "
            f"[{u.ci_low:.6f}, {u.ci_high:.6f}] from {u.independent_observations} obs"
        )
    lines.append("greeks:")
    for g in r.greeks:
        if g.value is None:
            lines.append(f"  {g.name.value:<13}{g.status}: {g.note or ''}")
        else:
            se = f" ± {g.standard_error:.2g} (SE)" if g.standard_error else ""
            lines.append(
                f"  {g.name.value:<13}{g.value:>14.8f}{se}  [{g.method}; raw unit: {g.unit}; "
                f"{g.display_value:.6f} {g.display_unit}]"
            )
    lines.append(f"request hash  {r.request_hash}")
    return "\n".join(lines)


def _load(path: str) -> str:
    return Path(path).read_text()


def cmd_price(a: argparse.Namespace) -> int:
    req = (
        PriceRequestIn.model_validate_json(_load(a.input))
        if a.input
        else _price_request_from_flags(a)
    )
    if a.manifest:
        m = handlers.manifest(req)
        Path(a.manifest).write_text(m.model_dump_json(indent=2))
        print(f"manifest written to {a.manifest}", file=sys.stderr)
        result = m.result
    else:
        result = handlers.price(req)
    print(_render_price(result) if a.format == "table" else result.model_dump_json(indent=2))
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    _emit(handlers.compare(CompareRequestIn.model_validate_json(_load(a.input))))
    return 0


def cmd_iv(a: argparse.Namespace) -> int:
    _emit(handlers.implied_vol(ImpliedVolIn.model_validate_json(_load(a.input))))
    return 0


def cmd_replay(a: argparse.Namespace) -> int:
    out = handlers.replay(RunManifest.model_validate_json(_load(a.manifest)))
    _emit(out)
    return 0 if out["bitwise_identical"] and out["request_hash_matches"] else 3


def cmd_engines(_: argparse.Namespace) -> int:
    _emit(handlers.engines())
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    from options_engine.validation.report import run_validation

    return run_validation(Path(a.out), quick=a.quick)


def cmd_demo(_: argparse.Namespace) -> int:
    from options_engine.interfaces.demo import run_demo

    return run_demo()


def cmd_snapshot(a: argparse.Namespace) -> int:
    from options_engine.application.snapshots import SnapshotService

    svc = SnapshotService(Path(a.data_dir) if a.data_dir else None)
    if a.action == "ingest":
        for path in a.paths:
            m, created = svc.ingest_file(Path(path))
            state = "ingested" if created else "already present"
            q = m["quality"]
            print(
                f"{m['snapshot_id']} {state}: {q['accepted']}/{q['total_quotes']} accepted; "
                f"quarantined by reason {q['quarantine_reasons']}"
            )
    elif a.action == "list":
        _emit(svc.list())
    elif a.action == "show":
        _emit(svc.manifest(a.paths[0]))
    elif a.action == "generate-synthetic":
        from options_engine.adapters.snapshots.synthetic import write_default_fixtures

        print(write_default_fixtures(a.paths[0] if a.paths else "fixtures/snapshots"))
    elif a.action == "import-csv":
        return _import_csv(a, svc)
    return 0


def _ts_arg(text: str | None, name: str) -> datetime | None:
    if text is None:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise DomainError(ErrorCode.INVALID_REQUEST, f"--{name} is not ISO-8601") from None
    if dt.tzinfo is None:
        raise DomainError(ErrorCode.INVALID_REQUEST, f"--{name} needs a timezone")
    return dt


def _import_csv(a: argparse.Namespace, svc: Any) -> int:
    from options_engine.adapters.snapshots.csv_chain import convert_files

    if len(a.paths) != 1 or not a.mapping or not a.spot or not a.as_of or not a.underlying:
        raise DomainError(
            ErrorCode.INVALID_REQUEST,
            "import-csv needs one CSV path, --mapping, --underlying, --spot and --as-of",
        )
    as_of = _ts_arg(a.as_of, "as-of")
    assert as_of is not None
    doc = convert_files(
        Path(a.paths[0]),
        Path(a.mapping),
        underlying_id=a.underlying,
        currency=a.currency,
        spot=a.spot,
        as_of=as_of,
        spot_observed_at=_ts_arg(a.spot_observed_at, "spot-observed-at"),
        rate=a.rate,
        dividend_yield=a.dividend_yield,
        synthetic=a.synthetic,
    )
    raw = (json.dumps(doc, indent=1) + "\n").encode()
    if a.out:
        Path(a.out).write_bytes(raw)
        print(f"wrote {a.out} ({len(doc['quotes'])} quotes)", file=sys.stderr)
    if a.ingest:
        m, created = svc.ingest(raw)
        q = m["quality"]
        print(
            f"{m['snapshot_id']} {'ingested' if created else 'already present'}: "
            f"{q['accepted']}/{q['total_quotes']} accepted; quarantined by reason "
            f"{q['quarantine_reasons']}"
        )
    elif not a.out:
        sys.stdout.write(raw.decode())
    return 0


def cmd_heston(a: argparse.Namespace) -> int:
    from options_engine.application.heston_calibration import HestonCalibrationService

    cfg = None
    if a.pillars:
        from options_engine.application.heston_calibration import HestonCalibrationConfig

        pillars = tuple(int(x) for x in a.pillars.split(","))
        cfg = HestonCalibrationConfig(term_structure_pillars_days=pillars)
    svc = HestonCalibrationService(Path(a.data_dir) if a.data_dir else None, cfg)
    if a.action == "list":
        _emit(svc.list())
        return 0
    if not a.snapshot_id:
        raise DomainError(ErrorCode.INVALID_REQUEST, "calibrate needs a snapshot id")
    art, created = svc.calibrate(a.snapshot_id, a.later)
    if a.format == "json":
        _emit(art)
        return 0 if art["status"] == "ok" else 1
    print(f"{art['calibration_id']} ({'new' if created else 'existing'}): status {art['status']}")
    if art["status"] != "ok":
        print(f"  failure: {art['failure_reason']}: {art.get('failure_detail')}")
        return 1
    se = art["uncertainty"]["standard_errors"]
    for name, value in art["parameters"].items():
        print(f"  {name:<6} {value:>10.5f}  (SE {se[name]:.2g})")
    print(f"  Feller 2*kappa*theta/sigma^2 = {art['feller_ratio']:.3f}")
    pairs = art["uncertainty"]["strongly_correlated_pairs"]
    print(f"  strongly correlated pairs: {pairs or 'none'}")
    for part in ("in_sample", "held_out"):
        st = art[part]
        print(
            f"  {part}: n={st['n']} containment {st['bid_ask_containment']:.1%}, "
            f"IV RMSE {st['iv_rmse_vol_points']:.3f} vol pts"
        )
    if art.get("later_snapshot"):
        ls = art["later_snapshot"]
        print(f"  later, no refit: containment {ls['no_refit']['bid_ask_containment']:.1%}")
        vr = ls["v0_refit"]
        print(
            f"  later, v0 refit (v0 = {vr['v0']:.5f}): held-out containment "
            f"{vr['held_out']['bid_ask_containment']:.1%}"
        )
    return 0


def cmd_study(a: argparse.Namespace) -> int:
    from options_engine.adapters.snapshots.store import default_data_dir
    from options_engine.application.study import run_study
    from options_engine.validation.policy import load_policy

    data = Path(a.data_dir) if a.data_dir else None
    out = (
        Path(a.out) if a.out else (data or default_data_dir()) / "studies" / (f"{a.day1}__{a.day2}")
    )
    pol = load_policy()
    pillars = tuple(pol["heston_term_structure"]["pillars_days"])
    report = run_study(data, a.day1, a.day2, out, pol["real_data_study"], pillars)
    print(f"wrote {out / 'report.md'}")
    for metric, by_model in report["comparison"].items():
        print(
            f"  {metric}: "
            + ", ".join(
                f"{m} {v:.1%}" if v is not None else f"{m} n/a" for m, v in by_model.items()
            )
        )
    return 0


def cmd_hedge(a: argparse.Namespace) -> int:
    from options_engine.application import hedging
    from options_engine.domain.conventions import OptionType
    from options_engine.models.heston import HestonModel

    heston = None
    if a.heston:
        v0, kappa, theta, sigma, rho = (float(x) for x in a.heston.split(","))
        heston = HestonModel(v0, kappa, theta, sigma, rho)
    setup = hedging.HedgeSetup(
        option_type=OptionType(a.type),
        spot=a.spot,
        strike=a.strike,
        time=a.time,
        rate=a.rate,
        dividend_yield=a.dividend_yield,
        paths=a.paths,
        rebalances=tuple(int(x) for x in a.rebalances.split(",")),
        seed=a.seed,
        real_vol=None if heston else a.vol,
        heston=heston,
        hedge_vol=a.hedge_vol,
        strategies=tuple(a.strategies.split(",")),
    )
    out = hedging.run(setup)
    out.pop("pnl")
    if a.format == "json":
        _emit(out)
        return 0
    print(
        f"world {out['world']}: model price {out['model_price']:.4f}, implied vol "
        f"{out['model_implied_vol']:.4f}, hedge vol {out['hedge_vol']:.4f}; {out['paths']} paths"
    )
    print(f"{'strategy':<10}{'N':>6}{'mean':>10}{'std':>10}{'5%':>10}{'95%':>10}  theory std")
    for r in out["rows"]:
        th = f"{r['leading_order_std']:.4f}" if "leading_order_std" in r else ""
        print(
            f"{r['strategy']:<10}{r['rebalances']:>6}{r['mean']:>10.4f}{r['std']:>10.4f}"
            f"{r['q05']:>10.4f}{r['q95']:>10.4f}  {th}"
        )
    return 0


def _svi_slices(a: argparse.Namespace) -> int:
    from options_engine.application.svi_slices import SviSliceService

    art, created = SviSliceService(Path(a.data_dir) if a.data_dir else None).fit(
        a.snapshot_id, a.later
    )
    if a.format == "json":
        _emit(art)
        return 0 if art["status"] == "ok" else 1
    print(f"{art['fit_id']} ({'new' if created else 'existing'}): status {art['status']}")
    if art["status"] != "ok":
        print(f"  failure: {art['failure_reason']}: {art.get('failure_detail')}")
        return 1
    print(
        f"  {sum(1 for s in art['slices'] if s['success'])} slices fitted; arbitrage: "
        f"{art['arbitrage_diagnostics']['statement']}"
    )
    for part in ("in_sample", "held_out"):
        st = art[part]
        print(
            f"  {part}: n={st['n']} containment {st['bid_ask_containment']:.1%}, "
            f"IV RMSE {st['iv_rmse_vol_points']:.3f} vol pts"
        )
    if art.get("later_snapshot"):
        ls = art["later_snapshot"]
        print(f"  later snapshot (no refit): containment {ls['bid_ask_containment']:.1%}")
    return 0


def cmd_surface(a: argparse.Namespace) -> int:
    from options_engine.application.surface import SurfaceService

    if a.action == "fit" and a.model == "svi-slices":
        return _svi_slices(a)
    svc = SurfaceService(Path(a.data_dir) if a.data_dir else None)
    if a.action == "list":
        _emit(svc.list())
        return 0
    art, created = svc.fit(a.snapshot_id, a.later)
    if a.format == "json":
        _emit(art)
    else:
        print(f"{art['fit_id']} ({'new' if created else 'existing'}): status {art['status']}")
        if art["status"] != "ok":
            print(f"  failure: {art['failure_reason']}: {art.get('failure_detail')}")
            return 1
        s = art["surface"]
        print(
            f"  rho={s['rho']:.4f} eta={s['eta']:.4f} gamma={s['gamma']:.4f}; "
            f"bound hits {art['bound_hits']}"
        )
        for part in ("in_sample", "held_out"):
            st = art[part]
            print(
                f"  {part}: n={st['n']} price RMSE {st['price_rmse']:.4f}, bid/ask containment "
                f"{st['bid_ask_containment']:.1%}, weighted RMS {st['weighted_residual_rms']:.3f}"
            )
        print(
            f"  arbitrage: {art['arbitrage_diagnostics']['statement']}; guarantee conditions "
            f"{all(v for k, v in art['guarantee']['conditions'].items() if k.endswith('ok'))}"
        )
        if art.get("later_snapshot"):
            ls = art["later_snapshot"]
            print(
                f"  later snapshot ({ls['elapsed_days']:.1f} d later, no refit): price RMSE "
                f"{ls['price_rmse']:.3f}, containment {ls['bid_ask_containment']:.1%}"
            )
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import logging

    import uvicorn

    from options_engine.interfaces.http.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(create_app(), host=a.host, port=a.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="options-engine",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("price", help="price one contract")
    pr.add_argument("--input", help="PriceRequest v1 JSON file")
    pr.add_argument("--manifest", help="also write a replayable run manifest here")
    pr.add_argument("--format", choices=["table", "json"], default="table")
    pr.add_argument("--as-of", help="valuation timestamp, ISO-8601 with timezone")
    pr.add_argument("--expiry", help="expiry timestamp, ISO-8601 with timezone")
    pr.add_argument("--type", choices=["call", "put"])
    pr.add_argument("--exercise", choices=["european", "american"], default="european")
    pr.add_argument("--spot")
    pr.add_argument("--strike")
    pr.add_argument("--rate", help="continuously compounded, decimal (0.05 = 5%%)")
    pr.add_argument("--dividend-yield", default="0")
    pr.add_argument("--vol", type=float, help="decimal annualised volatility")
    pr.add_argument(
        "--heston",
        metavar="V0,KAPPA,THETA,SIGMA,RHO",
        help="price under Heston instead of --vol (uses the heston_fourier engine)",
    )
    pr.add_argument("--multiplier", default="100")
    pr.add_argument("--underlying", default="DEMO")
    pr.add_argument("--currency", default="USD")
    pr.add_argument(
        "--engine",
        default="bsm_analytic",
        choices=["bsm_analytic", "crr_tree", "mc_terminal_gbm", "heston_fourier", "lsm_american"],
    )
    pr.add_argument("--steps", type=int)
    pr.add_argument("--paths", type=int, default=200_000)
    pr.add_argument("--seed", type=int, default=20260928)
    pr.set_defaults(func=cmd_price)

    for name, fn, helptext in (
        ("compare", cmd_compare, "run all compatible engines and explain differences"),
        ("iv", cmd_iv, "invert quote/bid/ask to implied volatility"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--input", required=True)
        sp.set_defaults(func=fn)

    rp = sub.add_parser("replay", help="recompute a run manifest and check agreement")
    rp.add_argument("manifest")
    rp.set_defaults(func=cmd_replay)

    sub.add_parser("engines", help="print the engine capability matrix").set_defaults(
        func=cmd_engines
    )

    vp = sub.add_parser("validate", help="run the validation suite and write a report")
    vp.add_argument("--out", default="reports/latest")
    vp.add_argument("--quick", action="store_true", help="fewer replications (smoke run only)")
    vp.set_defaults(func=cmd_validate)

    sub.add_parser("demo", help="offline demonstration incl. failure cases").set_defaults(
        func=cmd_demo
    )

    snp = sub.add_parser("snapshot", help="ingest, list, show, import or generate snapshots")
    snp.add_argument(
        "action", choices=["ingest", "list", "show", "generate-synthetic", "import-csv"]
    )
    snp.add_argument("paths", nargs="*", help="files (ingest/import-csv) or a snapshot id")
    snp.add_argument("--data-dir")
    snp.add_argument("--mapping", help="import-csv: TOML column mapping")
    snp.add_argument("--underlying", help="import-csv: underlying id")
    snp.add_argument("--currency", default="USD")
    snp.add_argument("--spot", help="import-csv: underlying price (decimal text)")
    snp.add_argument("--as-of", help="import-csv: snapshot time, ISO-8601 with timezone")
    snp.add_argument("--spot-observed-at")
    snp.add_argument("--rate", help="import-csv: optional carry; forwards come from parity")
    snp.add_argument("--dividend-yield")
    snp.add_argument("--synthetic", action="store_true", help="label the import as synthetic")
    snp.add_argument("--out", help="import-csv: write the snapshot JSON here")
    snp.add_argument("--ingest", action="store_true", help="import-csv: also ingest it")
    snp.set_defaults(func=cmd_snapshot)

    sfp = sub.add_parser("surface", help="fit or list SSVI surfaces")
    sfp.add_argument("action", choices=["fit", "list"])
    sfp.add_argument("snapshot_id", nargs="?")
    sfp.add_argument("--later", help="later snapshot id, evaluated without refit")
    sfp.add_argument(
        "--model", choices=["ssvi", "svi-slices"], default="ssvi", help="SVI slices: ADR 0024"
    )
    sfp.add_argument("--format", choices=["table", "json"], default="table")
    sfp.add_argument("--data-dir")
    sfp.set_defaults(func=cmd_surface)

    hp = sub.add_parser("heston", help="calibrate Heston to a snapshot, or list calibrations")
    hp.add_argument("action", choices=["calibrate", "list"])
    hp.add_argument("snapshot_id", nargs="?")
    hp.add_argument("--later", help="later snapshot id (no refit and v0-only refit)")
    hp.add_argument(
        "--pillars", help="theta term structure: pillar days, e.g. 7,30,91,182 (ADR 0024)"
    )
    hp.add_argument("--format", choices=["table", "json"], default="table")
    hp.add_argument("--data-dir")
    hp.set_defaults(func=cmd_heston)

    stp = sub.add_parser("study", help="two-day SSVI vs Heston study on snapshots")
    stp.add_argument("day1")
    stp.add_argument("day2")
    stp.add_argument("--out", help="default: <data-dir>/studies/<day1>__<day2>")
    stp.add_argument("--data-dir")
    stp.set_defaults(func=cmd_study)

    hg = sub.add_parser("hedge", help="simulate discrete delta hedging of a short option")
    hg.add_argument("--type", choices=["call", "put"], default="call")
    hg.add_argument("--spot", type=float, default=100.0)
    hg.add_argument("--strike", type=float, default=100.0)
    hg.add_argument("--time", type=float, default=0.5, help="years")
    hg.add_argument("--rate", type=float, default=0.03)
    hg.add_argument("--dividend-yield", type=float, default=0.01)
    hg.add_argument("--vol", type=float, default=0.2, help="GBM world volatility")
    hg.add_argument("--heston", metavar="V0,KAPPA,THETA,SIGMA,RHO", help="Heston world instead")
    hg.add_argument("--hedge-vol", type=float, help="BSM hedge vol (default: model implied)")
    hg.add_argument("--strategies", default="bsm", help="comma list of bsm,heston,heston_mv")
    hg.add_argument("--rebalances", default="16,32,64,128,256")
    hg.add_argument("--paths", type=int, default=10_000)
    hg.add_argument("--seed", type=int, default=20260928)
    hg.add_argument("--format", choices=["table", "json"], default="table")
    hg.set_defaults(func=cmd_hedge)

    sp = sub.add_parser("serve", help="run the HTTP API and web UI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except DomainError as exc:
        print(json.dumps({"error": exc.to_dict()}, indent=2), file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(
            json.dumps({"error": {"code": "invalid_request", "message": str(exc)}}), file=sys.stderr
        )
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
