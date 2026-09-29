"""Schema-level use cases shared verbatim by the CLI and the HTTP API."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from options_engine.adapters.environment import PROJECT_ROOT, environment_info
from options_engine.application import visuals
from options_engine.application.analysis import AnalysisService, invert_quotes, z_for
from options_engine.application.hashing import to_jsonable
from options_engine.application.paper import PaperTradingService
from options_engine.application.portfolio import (
    PortfolioService,
    Position,
    ScenarioSpec,
    VolSource,
    flat_vol,
)
from options_engine.application.pricing import PricingService
from options_engine.application.snapshots import SnapshotService, quote_to_json
from options_engine.application.surface import SurfaceService
from options_engine.domain.conventions import year_fraction
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import NumericalConfig
from options_engine.interfaces.schemas import (
    AnalyticEngineIn,
    CompareRequestIn,
    CompareResponse,
    EngineComparison,
    ErrorOut,
    ExerciseBoundaryResponse,
    ImpliedVolIn,
    ImpliedVolResponse,
    IVOut,
    LedgerEventOut,
    MarkResultOut,
    MCConvergenceIn,
    MCConvergenceResponse,
    MCPoint,
    PaperAccountIn,
    PaperAccountRow,
    PaperHistoryPoint,
    PaperMarkIn,
    PaperSettlementIn,
    PaperSummaryOut,
    PaperTradeIn,
    PaperVoidIn,
    PortfolioRequestIn,
    PortfolioResponse,
    PositionOut,
    PriceRequestIn,
    PriceResponse,
    ProfileResponse,
    RunManifest,
    ScenarioOut,
    SurfaceFitIn,
    SurfaceViewsResponse,
    TreeConvergenceIn,
    TreeConvergenceResponse,
    TreePoint,
)

_pricing = PricingService()
_analysis = AnalysisService(_pricing)


def engines() -> list[dict[str, Any]]:
    return _pricing.registry.capability_matrix()


def price(req: PriceRequestIn) -> PriceResponse:
    return PriceResponse.from_domain(_pricing.price(req.to_domain()))


def manifest(req: PriceRequestIn) -> RunManifest:
    return RunManifest(
        created_at=datetime.now(UTC),
        request=req,
        result=price(req),
        environment=environment_info(include_git=True),
    )


def replay(m: RunManifest) -> dict[str, Any]:
    """Recompute a manifest and report exact agreement.

    Deterministic engines and seeded Monte Carlo should reproduce the price
    bit-for-bit on the same platform and library versions; differences are
    reported, not hidden.
    """
    fresh = price(m.request)
    same_hash = fresh.request_hash == m.result.request_hash
    keys = ("numpy", "scipy", "python", "platform")
    current = environment_info(include_git=False)
    return {
        "request_hash_matches": same_hash,
        "recorded_price": m.result.price,
        "replayed_price": fresh.price,
        "absolute_difference": abs(fresh.price - m.result.price),
        "bitwise_identical": fresh.price == m.result.price,
        "recorded_engine_version": m.result.engine_version,
        "replayed_engine_version": fresh.engine_version,
        "recorded_environment": {k: m.environment.get(k) for k in keys},
        "current_environment": {k: current.get(k) for k in keys},
    }


def compare(req: CompareRequestIn) -> CompareResponse:
    base = req.to_request(_default_engine())
    configs: list[tuple[str, NumericalConfig]] | None = None
    if req.engines is not None:
        if len(req.engines) > 6:
            raise DomainError(ErrorCode.WORK_LIMIT_EXCEEDED, "at most 6 engine runs per comparison")
        configs = [(e.engine, e.to_domain()) for e in req.engines]
    result = _analysis.compare(base, configs)
    return CompareResponse(
        reference_engine=result.reference_engine,
        reference_note=result.reference_note,
        comparisons=[
            EngineComparison(
                engine_id=o.engine_id,
                result=PriceResponse.from_domain(o.result) if o.result else None,
                error=ErrorOut.from_domain(o.error) if o.error else None,
                difference_vs_reference=o.difference,
                interpretation=o.interpretation,
            )
            for o in result.outcomes
        ],
    )


def tree_convergence(req: TreeConvergenceIn) -> TreeConvergenceResponse:
    base = req.to_request(_default_engine())
    reference, points, elapsed = _analysis.tree_convergence(base, req.steps)
    return TreeConvergenceResponse(
        reference_price=reference,
        reference_engine="bsm_analytic" if reference is not None else None,
        points=[
            TreePoint(
                steps=n,
                price=p,
                error_vs_reference=None if p is None or reference is None else p - reference,
                parity="odd" if n % 2 else "even",
                failure=f,
            )
            for n, p, f in points
        ],
        elapsed_seconds=elapsed,
    )


def mc_convergence(req: MCConvergenceIn) -> MCConvergenceResponse:
    base = req.to_request(_default_engine())
    reference, runs, elapsed = _analysis.mc_convergence(
        base, req.path_counts, req.seed, req.antithetic, req.control_variate
    )
    points = []
    for n, r in runs:
        u = r.output.uncertainty
        assert u is not None
        covers = None if reference is None else u.ci_low <= reference <= u.ci_high
        points.append(
            MCPoint(
                paths=n,
                price=r.price,
                standard_error=u.standard_error,
                ci_low=u.ci_low,
                ci_high=u.ci_high,
                covers_reference=covers,
            )
        )
    return MCConvergenceResponse(
        reference_price=reference,
        confidence_level=0.95,
        seed=req.seed,
        note=(
            "All path counts share one seeded stream, so smaller runs are prefixes of larger "
            "ones; the points are not independent replications. About 1 interval in 20 is "
            f"expected to miss the reference (z = {z_for(0.95):.2f})."
        ),
        points=points,
        elapsed_seconds=elapsed,
    )


def implied_vol(req: ImpliedVolIn) -> ImpliedVolResponse:
    quotes = [
        (label, value)
        for label, value in (("quote", req.quote), ("bid", req.bid), ("ask", req.ask))
        if value is not None
    ]
    if not quotes:
        raise DomainError(ErrorCode.INVALID_REQUEST, "provide quote and/or bid/ask")
    if req.bid is not None and req.ask is not None and req.bid > req.ask:
        raise DomainError(
            ErrorCode.INVALID_MARKET_INPUT,
            "crossed quote: bid exceeds ask",
            {"bid": str(req.bid), "ask": str(req.ask)},
        )
    results, context = invert_quotes(
        req.contract.to_domain(),
        req.valuation.to_domain(),
        req.market.to_domain(),
        quotes,
        req.price_resolution,
    )
    return ImpliedVolResponse(
        time_to_expiry=context["time_to_expiry"],
        forward=context["forward"],
        discount_factor=context["discount_factor"],
        results=[IVOut.from_domain(q.label, q.result) for q in results],
        assumptions={
            "model": "Black-Scholes-Merton, European exercise",
            "carry": "flat continuously compounded rate and dividend yield; "
            "cash dividends via escrowed spot",
            "bounds": "call: D*max(F-K,0) <= C < D*F; put: D*max(K-F,0) <= P < D*K",
            "stability_threshold_vol": 0.01,
        },
    )


def _default_engine() -> AnalyticEngineIn:
    return AnalyticEngineIn()


# --- Snapshots (Release B) ----------------------------------------------------------------

BUNDLED_SNAPSHOTS = ("synthetic_day1.json", "synthetic_day2.json")


def _snapshots() -> SnapshotService:
    return SnapshotService()


def snapshots_list() -> list[dict[str, Any]]:
    return _snapshots().list()


def snapshot_ingest(raw: bytes) -> dict[str, Any]:
    manifest, created = _snapshots().ingest(raw)
    return {"created": created, "manifest": manifest}


def snapshot_ingest_bundled() -> list[dict[str, Any]]:
    """Ingest the committed synthetic fixtures (offline demo data)."""
    root = PROJECT_ROOT / "fixtures" / "snapshots"
    svc = _snapshots()
    out = []
    for name in BUNDLED_SNAPSHOTS:
        manifest, created = svc.ingest_file(root / name)
        out.append({"file": name, "created": created, "snapshot_id": manifest["snapshot_id"]})
    return out


def snapshot_manifest(snapshot_id: str) -> dict[str, Any]:
    return _snapshots().manifest(snapshot_id)


def snapshot_quotes(snapshot_id: str) -> list[dict[str, Any]]:
    return [quote_to_json(q) for q in _snapshots().load(snapshot_id).quotes]


# --- Portfolio scenarios --------------------------------------------------------------------


def portfolio(req: PortfolioRequestIn) -> PortfolioResponse:
    positions = [
        Position(p.position_id, p.contract.to_domain(), p.quantity, p.volatility)
        for p in req.positions
    ]
    sc = req.scenarios
    spec = ScenarioSpec(
        tuple(sc.spot_shocks_pct),
        tuple(sc.vol_shocks_pts),
        tuple(sc.rate_shocks_bp),
        tuple(sc.time_roll_days),
        sc.surface_dynamics,
        sc.american_steps,
    )
    vol_source: VolSource = flat_vol
    if req.fit_id is not None:
        vol_source = surface_vol_source(req.fit_id)
    result = PortfolioService(_pricing).run(
        positions,
        req.valuation.to_domain(),
        req.market.to_domain(),
        spec,
        vol_source,
        req.include_position_detail,
    )
    return PortfolioResponse(
        base=to_jsonable(result.base),
        positions=[PositionOut(**p) for p in result.positions],
        scenarios=[ScenarioOut(**s) for s in result.scenarios],
        assumptions=result.assumptions,
        engines_used=result.engines_used,
        work=result.work,
        fit_id=req.fit_id,
    )


def surface_vol_source(fit_id: str) -> VolSource:
    """Volatility from a fitted SSVI surface at k = ln(K/F), F from the given market."""
    surface, artifact = SurfaceService().surface(fit_id)
    fitted_underlying = artifact.get("underlying")

    def source(position: Position, market: MarketSnapshot, valuation: ValuationContext) -> float:
        c = position.contract
        if c.underlying != fitted_underlying:
            raise DomainError(
                ErrorCode.UNSUPPORTED_COMBINATION,
                f"surface {fit_id} was fitted for '{fitted_underlying}', not '{c.underlying}'",
            )
        T = year_fraction(valuation.as_of, c.expiry)
        if T <= 0:
            return 0.0  # at/after expiry the position settles at intrinsic; vol unused
        F = float(market.spot) * math.exp((float(market.rate) - float(market.dividend_yield)) * T)
        return float(surface.implied_vol(math.log(float(c.strike) / F), T))

    return source


# --- Surface calibration ----------------------------------------------------------------------


def surface_fit(req: SurfaceFitIn) -> dict[str, Any]:
    artifact, created = SurfaceService().fit(req.snapshot_id, req.later_snapshot_id)
    return {"created": created, "artifact": artifact}


def surface_fits() -> list[dict[str, Any]]:
    return SurfaceService().list()


def surface_artifact(fit_id: str) -> dict[str, Any]:
    return SurfaceService().artifact(fit_id)


# --- Visualisation data ---------------------------------------------------------------------


def profile(req: PriceRequestIn) -> ProfileResponse:
    return ProfileResponse(**visuals.value_profile(_pricing, req.to_domain()))


def exercise_boundary(req: PriceRequestIn) -> ExerciseBoundaryResponse:
    return ExerciseBoundaryResponse(**visuals.exercise_boundary(_pricing, req.to_domain()))


def surface_views(fit_id: str) -> SurfaceViewsResponse:
    surface, artifact = SurfaceService().surface(fit_id)
    return SurfaceViewsResponse(fit_id=fit_id, **visuals.surface_views(surface, artifact))


# --- Paper trading ----------------------------------------------------------------------------


def _paper() -> PaperTradingService:
    return PaperTradingService(pricing=_pricing)


def _summary(raw: dict[str, Any]) -> PaperSummaryOut:
    return PaperSummaryOut.model_validate(to_jsonable(raw))


def paper_accounts() -> list[PaperAccountRow]:
    return [PaperAccountRow(**a) for a in _paper().accounts()]


def paper_open(req: PaperAccountIn) -> PaperSummaryOut:
    return _summary(_paper().open_account(req.name, req.currency, req.starting_cash, req.opened_at))


def paper_summary(account_id: str) -> PaperSummaryOut:
    return _summary(_paper().summary(account_id))


def paper_trade(account_id: str, req: PaperTradeIn) -> PaperSummaryOut:
    out = _paper().record_trade(
        account_id,
        req.timestamp,
        req.contract.to_domain(),
        req.side,
        req.quantity,
        req.price,
        req.fees,
        req.note,
    )
    return _summary(out)


def paper_void(account_id: str, req: PaperVoidIn) -> PaperSummaryOut:
    return _summary(_paper().void_trade(account_id, req.trade_id, req.reason, req.timestamp))


def paper_settle(account_id: str, req: PaperSettlementIn) -> PaperSummaryOut:
    out = _paper().settle_expiry(
        account_id, req.underlying, req.expiry, req.settlement_price, req.timestamp
    )
    return _summary(out)


def paper_mark(account_id: str, req: PaperMarkIn) -> MarkResultOut:
    markets = {u: m.to_domain() for u, m in req.markets.items()}
    inputs = {k: v.model_dump(exclude_none=True) for k, v in req.inputs.items()}
    result = _paper().mark(account_id, req.as_of, markets, inputs)
    return MarkResultOut.model_validate(to_jsonable(result))


def paper_history(account_id: str) -> list[PaperHistoryPoint]:
    return [PaperHistoryPoint.model_validate(p) for p in _paper().history(account_id)]


def paper_events(account_id: str) -> list[LedgerEventOut]:
    return [LedgerEventOut.model_validate(e) for e in _paper().events(account_id)]
