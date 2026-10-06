"""Versioned request/response schemas (v1) shared by the CLI and HTTP API.

Decimal fields accept JSON strings (exact, recommended) or numbers (parsed
via their shortest decimal text; trailing zeros are lost, which matters for
quote-resolution defaults).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from options_engine.analytics.implied_vol import SOLVED, IVResult, IVStatus
from options_engine.application.hedging import HedgeSetup
from options_engine.application.pricing import PriceRequest, PricingResult
from options_engine.domain.contracts import Settlement, VanillaContract
from options_engine.domain.conventions import (
    GREEK_DISPLAY,
    GREEK_UNITS,
    DayCount,
    ExerciseStyle,
    GreekName,
    OptionType,
    SettlementType,
)
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import CashDividend, MarketSnapshot, ValuationContext
from options_engine.domain.numerics import (
    AnalyticConfig,
    ControlVariate,
    CRRConfig,
    HestonConfig,
    LSMConfig,
    MonteCarloConfig,
    NumericalConfig,
)
from options_engine.domain.results import GreekResult
from options_engine.models.black_scholes import BlackScholesModel
from options_engine.models.heston import HestonModel

SCHEMA_VERSION: Literal["1"] = "1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContractIn(Strict):
    underlying: str = Field(default="DEMO", min_length=1, max_length=32)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    strike: Decimal = Field(description="Strike per underlying unit")
    option_type: OptionType
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN
    expiry: AwareDatetime = Field(description="Exercise/expiration timestamp (timezone required)")
    multiplier: Decimal = Field(description="Units of underlying per contract")
    settlement_type: SettlementType = SettlementType.PHYSICAL
    settlement_lag_days: int = 0
    last_trade: AwareDatetime | None = None
    deliverable: str = "standard"
    contract_id: str | None = None

    def to_domain(self) -> VanillaContract:
        return VanillaContract(
            underlying=self.underlying,
            currency=self.currency,
            strike=self.strike,
            option_type=self.option_type,
            exercise_style=self.exercise_style,
            expiry=self.expiry,
            multiplier=self.multiplier,
            settlement=Settlement(self.settlement_type, self.settlement_lag_days),
            last_trade=self.last_trade,
            deliverable=self.deliverable,
            contract_id=self.contract_id,
        )


class DividendIn(Strict):
    ex_date: AwareDatetime
    amount: Decimal


class MarketIn(Strict):
    spot: Decimal
    rate: Decimal = Field(description="Continuously compounded annual rate, decimal (0.05 = 5%)")
    dividend_yield: Decimal = Field(default=Decimal(0), description="Continuous annual yield")
    cash_dividends: list[DividendIn] = Field(default_factory=list, max_length=100)
    snapshot_id: str | None = None
    source: str = "user_input"
    observed_at: AwareDatetime | None = None

    def to_domain(self) -> MarketSnapshot:
        return MarketSnapshot(
            spot=self.spot,
            rate=self.rate,
            dividend_yield=self.dividend_yield,
            cash_dividends=tuple(CashDividend(d.ex_date, d.amount) for d in self.cash_dividends),
            snapshot_id=self.snapshot_id,
            source=self.source,
            observed_at=self.observed_at,
        )


class ModelIn(Strict):
    family: Literal["black_scholes"] = "black_scholes"
    volatility: float = Field(description="Decimal annualised volatility (0.2 = 20%)")

    def to_domain(self) -> BlackScholesModel:
        return BlackScholesModel(self.volatility)


class HestonModelIn(Strict):
    family: Literal["heston"]
    v0: float = Field(description="Initial variance (0.04 = 20% vol squared)")
    kappa: float = Field(description="Mean-reversion speed per year")
    theta: float = Field(description="Long-run variance")
    sigma: float = Field(description="Volatility of variance")
    rho: float = Field(description="Spot/variance correlation, in (-1, 1)")

    def to_domain(self) -> HestonModel:
        return HestonModel(self.v0, self.kappa, self.theta, self.sigma, self.rho)


class ValuationIn(Strict):
    as_of: AwareDatetime
    day_count: DayCount = DayCount.ACT_365F

    def to_domain(self) -> ValuationContext:
        return ValuationContext(self.as_of, self.day_count)


_CRR = CRRConfig()
_MC = MonteCarloConfig()


class AnalyticEngineIn(Strict):
    engine: Literal["bsm_analytic"] = "bsm_analytic"

    def to_domain(self) -> AnalyticConfig:
        return AnalyticConfig()


# Engine schemas take their defaults from the domain configs so the two cannot drift.
class CRREngineIn(Strict):
    engine: Literal["crr_tree"] = "crr_tree"
    steps: int = _CRR.steps
    allow_step_refinement: bool = _CRR.allow_step_refinement
    odd_even_diagnostic: bool = _CRR.odd_even_diagnostic

    def to_domain(self) -> CRRConfig:
        return CRRConfig(**self.model_dump(exclude={"engine"}))


_HESTON = HestonConfig()


class HestonEngineIn(Strict):
    engine: Literal["heston_fourier"] = "heston_fourier"
    epsabs: float = _HESTON.epsabs
    epsrel: float = _HESTON.epsrel
    limit: int = _HESTON.limit

    def to_domain(self) -> HestonConfig:
        return HestonConfig(**self.model_dump(exclude={"engine"}))


class MonteCarloEngineIn(Strict):
    engine: Literal["mc_terminal_gbm"] = "mc_terminal_gbm"
    paths: int = _MC.paths
    seed: int = _MC.seed
    antithetic: bool = _MC.antithetic
    control_variate: ControlVariate = _MC.control_variate
    pilot_paths: int = _MC.pilot_paths
    chunk_size: int = _MC.chunk_size
    confidence_level: float = _MC.confidence_level

    def to_domain(self) -> MonteCarloConfig:
        return MonteCarloConfig(**self.model_dump(exclude={"engine"}))


_LSM = LSMConfig()


class LSMEngineIn(Strict):
    engine: Literal["lsm_american"] = "lsm_american"
    paths: int = Field(_LSM.paths, description="Pricing-set payoff evaluations")
    regression_paths: int = Field(_LSM.regression_paths, description="Independent fitting set")
    exercise_dates: int = _LSM.exercise_dates
    basis_degree: int = _LSM.basis_degree
    seed: int = _LSM.seed
    antithetic: bool = _LSM.antithetic
    control_variate: bool = _LSM.control_variate
    confidence_level: float = _LSM.confidence_level
    upper_bound: bool = Field(
        _LSM.upper_bound, description="Also compute the Andersen-Broadie dual upper bound"
    )
    outer_paths: int = _LSM.outer_paths
    inner_paths: int = _LSM.inner_paths

    def to_domain(self) -> LSMConfig:
        return LSMConfig(**self.model_dump(exclude={"engine"}))


EngineIn = Annotated[
    AnalyticEngineIn | CRREngineIn | MonteCarloEngineIn | HestonEngineIn | LSMEngineIn,
    Field(discriminator="engine"),
]


class InstrumentIn(Strict):
    """Everything except the engine choice."""

    contract: ContractIn
    valuation: ValuationIn
    market: MarketIn
    model: ModelIn | HestonModelIn = Field(description="Black-Scholes (default) or Heston")
    greeks: list[GreekName] = Field(default_factory=lambda: list(GreekName))

    def to_request(self, engine: EngineIn) -> PriceRequest:
        config: NumericalConfig = engine.to_domain()
        return PriceRequest(
            contract=self.contract.to_domain(),
            valuation=self.valuation.to_domain(),
            market=self.market.to_domain(),
            model=self.model.to_domain(),
            engine_id=engine.engine,
            config=config,
            greeks=tuple(dict.fromkeys(self.greeks)),
        )


class PriceRequestIn(InstrumentIn):
    schema_version: Literal["1"] = SCHEMA_VERSION
    engine: EngineIn = Field(
        default_factory=AnalyticEngineIn,
        description="Defaults to bsm_analytic, or heston_fourier for a Heston model",
    )

    @model_validator(mode="before")
    @classmethod
    def _default_engine_for_model(cls, data: Any) -> Any:
        if isinstance(data, dict) and "engine" not in data:
            model = data.get("model")
            if isinstance(model, dict) and model.get("family") == "heston":
                return {**data, "engine": {"engine": "heston_fourier"}}
        return data

    def to_domain(self) -> PriceRequest:
        return self.to_request(self.engine)


class GreekOut(BaseModel):
    name: GreekName
    status: str
    value: float | None
    unit: str
    display_value: float | None
    display_unit: str
    method: str | None
    standard_error: float | None
    display_standard_error: float | None
    note: str | None

    @classmethod
    def from_domain(cls, g: GreekResult) -> GreekOut:
        factor, display_unit = GREEK_DISPLAY[g.name]
        return cls(
            name=g.name,
            status=g.status.value,
            value=g.value,
            unit=GREEK_UNITS[g.name],
            display_value=None if g.value is None else g.value * factor,
            display_unit=display_unit,
            method=g.method,
            standard_error=g.standard_error,
            display_standard_error=None if g.standard_error is None else g.standard_error * factor,
            note=g.note,
        )


class UncertaintyOut(BaseModel):
    kind: Literal["monte_carlo_sampling"] = "monte_carlo_sampling"
    standard_error: float
    confidence_level: float
    ci_low: float
    ci_high: float
    independent_observations: int


class PriceResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    request_hash: str
    engine_id: str
    engine_version: str
    package_version: str
    model_family: str
    currency: str
    quote_basis: str
    price: float
    contract_value: float
    multiplier: str
    time_to_expiry: float
    dividend_treatment: str
    greeks: list[GreekOut]
    uncertainty: UncertaintyOut | None
    diagnostics: dict[str, Any]
    warnings: list[str]
    assumptions: dict[str, Any]
    elapsed_seconds: float

    @classmethod
    def from_domain(cls, r: PricingResult) -> PriceResponse:
        u = r.output.uncertainty
        return cls(
            request_hash=r.request_hash,
            engine_id=r.engine_id,
            engine_version=r.engine_version,
            package_version=r.package_version,
            model_family=r.model_family,
            currency=r.currency,
            quote_basis=r.quote_basis.value,
            price=r.price,
            contract_value=r.contract_value,
            multiplier=r.multiplier,
            time_to_expiry=r.time_to_expiry,
            dividend_treatment=r.dividend_treatment,
            greeks=[GreekOut.from_domain(g) for g in r.output.greeks.values()],
            uncertainty=None
            if u is None
            else UncertaintyOut(
                standard_error=u.standard_error,
                confidence_level=u.confidence_level,
                ci_low=u.ci_low,
                ci_high=u.ci_high,
                independent_observations=u.independent_observations,
            ),
            diagnostics=r.output.diagnostics,
            warnings=list(r.output.warnings),
            assumptions=r.assumptions,
            elapsed_seconds=r.elapsed_seconds,
        )


class ErrorOut(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, e: DomainError) -> ErrorOut:
        return cls(code=e.code.value, message=e.message, details=e.details)


class ErrorResponse(BaseModel):
    error: ErrorOut


# --- Comparison and convergence ---------------------------------------------------------


class CompareRequestIn(InstrumentIn):
    schema_version: Literal["1"] = SCHEMA_VERSION
    engines: list[EngineIn] | None = Field(
        default=None, description="Engines to run; default = all compatible with default settings"
    )


class EngineComparison(BaseModel):
    engine_id: str
    result: PriceResponse | None
    error: ErrorOut | None
    difference_vs_reference: float | None
    interpretation: str | None


class CompareResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    reference_engine: str | None
    reference_note: str
    comparisons: list[EngineComparison]


class TreeConvergenceIn(InstrumentIn):
    schema_version: Literal["1"] = SCHEMA_VERSION
    steps: list[int] = Field(min_length=1, max_length=400)


class TreePoint(BaseModel):
    steps: int
    price: float | None
    error_vs_reference: float | None
    parity: Literal["odd", "even"]
    failure: str | None


class TreeConvergenceResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    reference_price: float | None
    reference_engine: str | None
    points: list[TreePoint]
    elapsed_seconds: float


class MCConvergenceIn(InstrumentIn):
    schema_version: Literal["1"] = SCHEMA_VERSION
    path_counts: list[int] = Field(min_length=1, max_length=40)
    seed: int = 20260928
    antithetic: bool = True
    control_variate: ControlVariate = ControlVariate.NONE


class MCPoint(BaseModel):
    paths: int
    price: float
    standard_error: float
    ci_low: float
    ci_high: float
    covers_reference: bool | None


class MCConvergenceResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    reference_price: float | None
    confidence_level: float
    seed: int
    note: str
    points: list[MCPoint]
    elapsed_seconds: float


# --- Implied volatility -------------------------------------------------------------------


class ImpliedVolIn(Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    contract: ContractIn
    valuation: ValuationIn
    market: MarketIn
    quote: Decimal | None = Field(default=None, description="Option price per underlying unit")
    bid: Decimal | None = None
    ask: Decimal | None = None
    price_resolution: float | None = Field(
        default=None,
        description="Quote uncertainty used for stability checks; default is half the last "
        "supplied decimal place",
    )


class IVOut(BaseModel):
    label: str
    quote: float
    status: IVStatus
    solved: bool
    implied_vol: float | None
    explanation: str
    lower_bound: float
    upper_bound: float
    price_resolution: float | None
    price_residual: float | None
    bracket: tuple[float, float] | None
    iterations: int | None
    function_calls: int | None
    vega: float | None
    vol_uncertainty: float | None
    iv_interval: tuple[float | None, float | None] | None
    notes: list[str]

    @classmethod
    def from_domain(cls, label: str, r: IVResult) -> IVOut:
        return cls(label=label, solved=r.status in SOLVED, **dataclasses.asdict(r))


class ImpliedVolResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    time_to_expiry: float
    forward: float
    discount_factor: float
    results: list[IVOut]
    assumptions: dict[str, Any]


# --- Manifests ----------------------------------------------------------------------------


class RunManifest(BaseModel):
    manifest_version: Literal["1"] = "1"
    kind: Literal["price"] = "price"
    created_at: datetime
    request: PriceRequestIn
    result: PriceResponse
    environment: dict[str, Any]


# --- Portfolio scenarios (Release B) ------------------------------------------------------


class PositionIn(Strict):
    position_id: str = Field(min_length=1, max_length=64)
    contract: ContractIn
    quantity: Decimal = Field(description="Signed number of contracts (negative = short)")
    volatility: float | None = Field(
        default=None, description="Flat model volatility (decimal); omit to read from fit_id"
    )


class ScenarioIn(Strict):
    spot_shocks_pct: list[float] = Field(default_factory=lambda: [-10.0, -5.0, 0.0, 5.0, 10.0])
    vol_shocks_pts: list[float] = Field(default_factory=lambda: [-5.0, 0.0, 5.0])
    rate_shocks_bp: list[float] = Field(default_factory=lambda: [0.0])
    time_roll_days: list[float] = Field(default_factory=lambda: [0.0])
    surface_dynamics: Literal["sticky_strike", "sticky_moneyness"] = "sticky_strike"
    american_steps: int = 400


class PortfolioRequestIn(Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    valuation: ValuationIn
    market: MarketIn
    positions: list[PositionIn] = Field(min_length=1, max_length=50)
    scenarios: ScenarioIn = Field(default_factory=ScenarioIn)
    fit_id: str | None = Field(default=None, description="Surface fit supplying volatilities")
    include_position_detail: bool = False


class PositionOut(BaseModel):
    position_id: str
    contract: str
    quantity: str
    multiplier: str
    volatility: float
    engine: str
    price_per_unit: float
    value: float
    greeks_position_raw: dict[str, float | None]


class ScenarioOut(BaseModel):
    spot_shock_pct: float
    vol_shock_pts: float
    rate_shock_bp: float
    time_roll_days: float
    spot: float
    value: float
    pnl: float
    approx_pnl: float | None
    unexplained: float | None
    positions_settled: int
    position_values: dict[str, float] | None = None


class PortfolioResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    base: dict[str, Any]
    positions: list[PositionOut]
    scenarios: list[ScenarioOut]
    assumptions: dict[str, Any]
    engines_used: dict[str, str]
    work: dict[str, int]
    fit_id: str | None = None


# --- Surface calibration ---------------------------------------------------------------------


class SurfaceFitIn(Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    snapshot_id: str
    later_snapshot_id: str | None = Field(
        default=None, description="Optional later snapshot for out-of-sample-in-time evaluation"
    )


class HestonCalibrationIn(Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    snapshot_id: str
    later_snapshot_id: str | None = Field(
        default=None, description="Optional later snapshot: no-refit and v0-only-refit evaluation"
    )


# --- Hedging experiment ------------------------------------------------------------------------


HedgeStrategy = Literal["bsm", "heston", "heston_mv"]


def _default_strategies() -> list[HedgeStrategy]:
    return ["bsm"]


class HedgingIn(Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    option_type: OptionType = OptionType.CALL
    spot: float = 100.0
    strike: float = 100.0
    time: float = Field(0.5, description="Years to expiry")
    rate: float = 0.03
    dividend_yield: float = 0.01
    world: Literal["gbm", "heston"] = "gbm"
    real_vol: float = Field(0.2, description="Volatility of the GBM world")
    heston: HestonModelIn | None = Field(None, description="Parameters of the Heston world")
    hedge_vol: float | None = Field(
        None, description="Black-Scholes hedge vol; default: the model's own (implied) vol"
    )
    strategies: list[HedgeStrategy] = Field(default_factory=_default_strategies)
    rebalances: list[int] = Field(default_factory=lambda: [16, 32, 64, 128, 256])
    paths: int = 5000
    seed: int = 20260928
    drift: float | None = Field(None, description="Real-world drift; default the rate")

    def to_domain(self) -> HedgeSetup:
        heston = None
        if self.world == "heston":
            if self.heston is None:
                raise DomainError(ErrorCode.INVALID_REQUEST, "world 'heston' needs parameters")
            heston = self.heston.to_domain()
        return HedgeSetup(
            option_type=self.option_type,
            spot=self.spot,
            strike=self.strike,
            time=self.time,
            rate=self.rate,
            dividend_yield=self.dividend_yield,
            paths=self.paths,
            rebalances=tuple(self.rebalances),
            seed=self.seed,
            real_vol=None if heston else self.real_vol,
            heston=heston,
            hedge_vol=self.hedge_vol,
            strategies=tuple(self.strategies),
            drift=self.drift,
        )


class HedgingRow(BaseModel):
    strategy: str
    rebalances: int
    mean: float
    std: float
    se_mean: float
    q01: float
    q05: float
    median: float
    q95: float
    q99: float
    std_times_sqrt_n: float
    leading_order_std: float | None = None
    vol_mismatch_identity_mean: float | None = None
    pnl_minus_identity_mean: float | None = None
    pnl_minus_identity_se: float | None = None


class Histogram(BaseModel):
    edges: list[float]
    counts: list[int]


class HedgingResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    world: str
    model_price: float
    model_implied_vol: float
    hedge_vol: float
    drift: float
    paths: int
    grid_steps: int
    simulation: dict[str, Any]
    rows: list[HedgingRow]
    histograms: dict[str, Histogram]
    histogram_rebalances: int
    slope: dict[str, float | None]
    note: str = Field(
        "Short one option, hedged in the underlying at N equal intervals; P&L discounted to "
        "t = 0. leading_order_std is the Gamma-based prediction sqrt(0.5 sigma^4 dt^2 "
        "sum E[e^{-2rt} Gamma^2 S^4]) on the same paths (GBM world)."
    )


# --- Visualisation data ------------------------------------------------------------------


class ProfileHorizon(BaseModel):
    elapsed_fraction: float
    remaining_days: float
    values: list[float]


class ProfileResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    spots: list[float]
    current_spot: float
    strike: float
    horizons: list[ProfileHorizon]
    payoff: list[float]
    greeks: dict[str, list[float | None]]
    greek_units: dict[str, str]
    engine: str
    assumptions: str


class BoundaryPoint(BaseModel):
    time_years: float
    days_from_now: float
    boundary_spot: float | None


class ExerciseBoundaryResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    points: list[BoundaryPoint]
    strike: float
    current_spot: float
    option_type: str
    steps: int
    price: float
    exercise_region: str
    no_node_exercised: bool
    exercise_optimal_now: bool
    gap_note: str
    resolution_note: str


class TermPoint(BaseModel):
    T: float
    atm_vol: float
    region: Literal["interpolated", "extrapolated"]


class DensitySlice(BaseModel):
    T: float
    density: list[float]
    min_density: float
    mass_on_grid: float
    mean_forward_ratio_on_grid: float


class SurfaceViewsResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    fit_id: str
    k_grid: list[float]
    t_grid: list[float]
    fitted_expiries: list[float]
    implied_vol: list[list[float]]
    term_structure: list[TermPoint]
    density_k: list[float]
    densities: list[DensitySlice]
    notes: str


# --- Paper trading (no real money moves) -------------------------------------------------
# Money amounts are Decimal and serialise as exact decimal strings.


class PaperAccountIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    starting_cash: Decimal
    opened_at: AwareDatetime


class PaperTradeIn(Strict):
    timestamp: AwareDatetime = Field(description="When the (paper) trade happened")
    contract: ContractIn
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(description="Whole number of contracts, positive")
    price: Decimal = Field(description="Fill price per underlying unit")
    fees: Decimal = Decimal(0)
    note: str = Field(default="", max_length=200)


class PaperVoidIn(Strict):
    trade_id: str
    reason: str = Field(min_length=1, max_length=200)
    timestamp: AwareDatetime


class PaperSettlementIn(Strict):
    underlying: str
    expiry: AwareDatetime
    settlement_price: Decimal
    timestamp: AwareDatetime


class MarkInputIn(Strict):
    observed_price: Decimal | None = Field(default=None, description="Market price per unit")
    volatility: float | None = Field(default=None, description="Model volatility (decimal)")


class PaperMarkIn(Strict):
    as_of: AwareDatetime
    markets: dict[str, MarketIn] = Field(description="Market inputs per underlying")
    inputs: dict[str, MarkInputIn] = Field(default_factory=dict, description="Per position key")


class PaperPositionOut(BaseModel):
    key: str
    contract: dict[str, Any]
    quantity: Decimal
    average_price: Decimal
    realized: Decimal


class MarkRowOut(BaseModel):
    key: str
    quantity: Decimal
    average_price: Decimal
    mark_basis: Literal["market", "model", "intrinsic"]
    observed_price: Decimal | None
    model_price: float | None
    model_minus_market: float | None
    volatility: float | None
    value: Decimal
    unrealized: Decimal
    status: str


class MarkResultOut(BaseModel):
    as_of: datetime
    cash: Decimal
    positions_value: Decimal
    equity: Decimal
    realized: Decimal
    fees: Decimal
    net_realized: Decimal
    unrealized: Decimal
    total_pnl: Decimal
    positions: list[MarkRowOut]


class LedgerIntegrityOut(BaseModel):
    intact: bool
    events: int
    first_bad_seq: int | None
    head_hash: str | None = None


class PaperSummaryOut(BaseModel):
    account_id: str
    name: str
    currency: str
    starting_cash: Decimal
    opened_at: datetime
    cash: Decimal
    realized: Decimal
    fees: Decimal
    net_realized: Decimal
    trades: int
    voided_trades: list[str]
    positions: list[PaperPositionOut]
    last_mark: MarkResultOut | None
    integrity: LedgerIntegrityOut
    notice: str = "Paper trading only: no real money moves and nothing here is investment advice."


class PaperAccountRow(BaseModel):
    account_id: str
    name: str
    currency: str


class PaperHistoryPoint(BaseModel):
    as_of: datetime
    equity: Decimal
    cash: Decimal
    net_realized: Decimal
    unrealized: Decimal
    total_pnl: Decimal


class LedgerEventOut(BaseModel):
    seq: int
    type: str
    timestamp: datetime
    recorded_at: datetime
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class SmilePoint(BaseModel):
    k: float
    strike: float
    price: float
    implied_vol: float | None
    status: str


class SmileResponse(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    model: str
    engine: str
    time_to_expiry: float
    forward: float
    points: list[SmilePoint]
    atm_implied_vol: float | None
    skew: float | None = Field(description="IV at k = -s/2 minus IV at k = +s/2")
    note: str
