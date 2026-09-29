"""Command-line interface. Uses the same handlers as the HTTP API.

Examples:
  options-engine price --input examples/price_hull_call.json
  options-engine price --as-of 2026-09-28T20:00:00Z --expiry 2027-03-29T20:00:00Z \\
      --type call --spot 42 --strike 40 --rate 0.10 --vol 0.20 --engine crr_tree
  options-engine iv --input examples/iv_quotes.json
  options-engine replay manifest.json
  options-engine validate --out reports/latest
  options-engine demo
  options-engine serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from options_engine.domain.errors import DomainError
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
    if a.engine == "mc_terminal_gbm":
        engine.update({"paths": a.paths, "seed": a.seed})
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
            "model": {"volatility": a.vol},
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
    return 0


def cmd_surface(a: argparse.Namespace) -> int:
    from options_engine.application.surface import SurfaceService

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
    pr.add_argument("--multiplier", default="100")
    pr.add_argument("--underlying", default="DEMO")
    pr.add_argument("--currency", default="USD")
    pr.add_argument(
        "--engine", default="bsm_analytic", choices=["bsm_analytic", "crr_tree", "mc_terminal_gbm"]
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
