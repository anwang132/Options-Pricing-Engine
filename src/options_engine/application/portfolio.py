"""Portfolio valuation and deterministic full-repricing scenarios (ADR 0014).

Conventions:
* Position value = signed quantity x contract multiplier x price per unit.
* One underlying and one currency per request (no FX model; other cases rejected).
* Shocks: spot in percent of spot, volatility in absolute vol points, rate in
  basis points, time roll in calendar days (valuation time moves forward).
* Surface dynamics: ``sticky_strike`` keeps each position's volatility (plus
  the shock). ``sticky_moneyness`` re-reads volatility from a fitted surface at
  the scenario forward moneyness and requires a fit artifact.
* A time roll at or past expiry settles the position at intrinsic value at the
  scenario spot, held as cash without interest; Greek approximations are not
  meaningful for such scenarios and are reported as unavailable.
* Dividends whose ex-date passes during a time roll leave the schedule; spot is
  not mechanically reduced (the spot shock is the only spot movement).
* Recalibrated scenarios are a separate operation and are not implemented.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from options_engine.application.pricing import PriceRequest, PricingService
from options_engine.domain.contracts import VanillaContract
from options_engine.domain.conventions import ExerciseStyle, GreekName, year_fraction
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import AnalyticConfig, CRRConfig, NumericalConfig
from options_engine.models.black_scholes import BlackScholesModel

MAX_POSITIONS = 50
MAX_SCENARIOS = 1_000
MAX_REPRICINGS = 50_000
MAX_AMERICAN_REPRICINGS = 10_000
# Tree cost scales with steps^2: budget American work in node-steps. Measured ~2.2 ns per
# node-step through the service on an Apple M4, so 4e9 bounds that part at roughly 9 s.
MAX_AMERICAN_TREE_WORK = 4_000_000_000

AGG_GREEKS = (GreekName.DELTA, GreekName.GAMMA, GreekName.VEGA, GreekName.THETA, GreekName.RHO)


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    contract: VanillaContract
    quantity: Decimal  # signed number of contracts
    volatility: float | None = None  # flat model vol; None => read from a fitted surface


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    spot_shocks_pct: tuple[float, ...] = (-10.0, -5.0, 0.0, 5.0, 10.0)
    vol_shocks_pts: tuple[float, ...] = (-5.0, 0.0, 5.0)
    rate_shocks_bp: tuple[float, ...] = (0.0,)
    time_roll_days: tuple[float, ...] = (0.0,)
    surface_dynamics: str = "sticky_strike"
    american_steps: int = 400

    def grid(self) -> list[tuple[float, float, float, float]]:
        return list(
            itertools.product(
                self.spot_shocks_pct, self.vol_shocks_pts, self.rate_shocks_bp, self.time_roll_days
            )
        )


# (position, market, valuation) -> volatility (decimal)
VolSource = Callable[[Position, MarketSnapshot, ValuationContext], float]


def flat_vol(position: Position, market: MarketSnapshot, valuation: ValuationContext) -> float:
    if position.volatility is None:
        raise DomainError(
            ErrorCode.INVALID_REQUEST,
            f"position {position.position_id} has no volatility and no surface was supplied",
        )
    return position.volatility


@dataclass
class PortfolioResult:
    base: dict[str, Any]
    positions: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    assumptions: dict[str, Any]
    engines_used: dict[str, str]
    work: dict[str, int] = field(default_factory=dict)


class PortfolioService:
    def __init__(self, pricing: PricingService | None = None) -> None:
        self.pricing = pricing or PricingService()

    # --- validation -------------------------------------------------------------------------

    def _validate(
        self, positions: list[Position], spec: ScenarioSpec, vol_source: VolSource
    ) -> dict[str, int]:
        """Structural checks and work budgets, before any pricing. Returns the work counts."""
        if not positions:
            raise DomainError(ErrorCode.INVALID_REQUEST, "portfolio has no positions")
        if len(positions) > MAX_POSITIONS:
            raise DomainError(ErrorCode.WORK_LIMIT_EXCEEDED, f"more than {MAX_POSITIONS} positions")
        ids = [p.position_id for p in positions]
        if len(set(ids)) != len(ids):
            raise DomainError(ErrorCode.INVALID_REQUEST, "position ids must be unique")
        currencies = {p.contract.currency for p in positions}
        if len(currencies) > 1:
            raise DomainError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                "positions in several currencies cannot be aggregated without an FX model",
                {"currencies": sorted(currencies)},
            )
        underlyings = {p.contract.underlying for p in positions}
        if len(underlyings) > 1:
            raise DomainError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                "one underlying per scenario request (market inputs are per underlying)",
                {"underlyings": sorted(underlyings)},
            )
        if spec.surface_dynamics not in ("sticky_strike", "sticky_moneyness"):
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                f"unknown surface dynamics '{spec.surface_dynamics}'; recalibrated scenarios are "
                "a separate operation and not implemented",
            )
        if spec.surface_dynamics == "sticky_moneyness" and vol_source is flat_vol:
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                "sticky_moneyness needs a fitted volatility surface (fit_id); flat per-position "
                "volatilities have no moneyness dependence",
            )
        for p in positions:
            if p.quantity == 0:
                raise DomainError(ErrorCode.INVALID_REQUEST, f"{p.position_id}: zero quantity")
        grid = spec.grid()
        n_american = sum(p.contract.exercise_style is ExerciseStyle.AMERICAN for p in positions)
        work = {
            "scenarios": len(grid),
            "repricings": len(grid) * len(positions),
            "american_repricings": len(grid) * n_american,
            "american_tree_work": len(grid) * n_american * spec.american_steps**2,
        }
        for key, limit in (
            ("scenarios", MAX_SCENARIOS),
            ("repricings", MAX_REPRICINGS),
            ("american_repricings", MAX_AMERICAN_REPRICINGS),
            ("american_tree_work", MAX_AMERICAN_TREE_WORK),
        ):
            if work[key] > limit:
                raise DomainError(
                    ErrorCode.WORK_LIMIT_EXCEEDED, f"{key}={work[key]} exceeds {limit}", work
                )
        if not 1 <= spec.american_steps <= 2_000:
            raise DomainError(ErrorCode.WORK_LIMIT_EXCEEDED, "american_steps must be in [1, 2000]")
        if min(spec.spot_shocks_pct) <= -100.0:
            raise DomainError(ErrorCode.INVALID_REQUEST, "spot shock must be > -100%")
        return work

    @staticmethod
    def _check_vol_shocks(base_vols: dict[str, float], spec: ScenarioSpec) -> None:
        worst = min(spec.vol_shocks_pts)
        for pid, vol in base_vols.items():
            if vol + worst / 100.0 < 0.0:
                raise DomainError(
                    ErrorCode.INVALID_REQUEST,
                    f"{pid}: vol shock {worst} pts makes volatility negative; "
                    "shocks are not clipped",
                )

    # --- pricing ----------------------------------------------------------------------------

    @staticmethod
    def _engine(p: Position, spec: ScenarioSpec) -> tuple[str, NumericalConfig]:
        if p.contract.exercise_style is ExerciseStyle.AMERICAN:
            return "crr_tree", CRRConfig(
                steps=spec.american_steps,
                allow_step_refinement=True,
                odd_even_diagnostic=False,
                early_exercise_diagnostic=False,
            )
        return "bsm_analytic", AnalyticConfig()

    def _price(
        self,
        p: Position,
        valuation: ValuationContext,
        market: MarketSnapshot,
        vol: float,
        spec: ScenarioSpec,
        greeks: tuple[GreekName, ...],
    ) -> tuple[float, dict[GreekName, float | None], str]:
        """Per-unit price (or settled intrinsic at/after expiry) and raw Greeks."""
        if valuation.as_of >= p.contract.expiry:
            sign = p.contract.option_type.sign
            payoff = max(sign * (float(market.spot) - float(p.contract.strike)), 0.0)
            return payoff, dict.fromkeys(greeks), "settled_at_intrinsic"
        engine_id, config = self._engine(p, spec)
        _, _, output = self.pricing.evaluate(
            PriceRequest(
                p.contract, valuation, market, BlackScholesModel(vol), engine_id, config, greeks
            )
        )
        return output.price, {g: r.value for g, r in output.greeks.items()}, engine_id

    def run(
        self,
        positions: list[Position],
        valuation: ValuationContext,
        market: MarketSnapshot,
        spec: ScenarioSpec,
        vol_source: VolSource = flat_vol,
        include_position_detail: bool = False,
    ) -> PortfolioResult:
        work = self._validate(positions, spec, vol_source)
        base_vols = {p.position_id: vol_source(p, market, valuation) for p in positions}
        self._check_vol_shocks(base_vols, spec)
        spot0 = float(market.spot)
        base_rows = []
        agg: dict[str, float | None] = {g.value: 0.0 for g in AGG_GREEKS}
        base_value = 0.0
        engines: dict[str, str] = {}
        for p in positions:
            vol = base_vols[p.position_id]
            price, greeks, engine = self._price(p, valuation, market, vol, spec, AGG_GREEKS)
            engines[p.position_id] = engine
            scale = float(p.quantity) * float(p.contract.multiplier)
            value = scale * price
            base_value += value
            pos_greeks = {g.value: (None if v is None else scale * v) for g, v in greeks.items()}
            for g, v in pos_greeks.items():
                cur = agg[g]
                agg[g] = None if (v is None or cur is None) else cur + v
            base_rows.append(
                {
                    "position_id": p.position_id,
                    "contract": f"{p.contract.option_type.value} K={p.contract.strike} "
                    f"{p.contract.exercise_style.value} exp {p.contract.expiry.isoformat()}",
                    "quantity": str(p.quantity),
                    "multiplier": str(p.contract.multiplier),
                    "volatility": vol,
                    "engine": engine,
                    "price_per_unit": price,
                    "value": value,
                    "greeks_position_raw": pos_greeks,
                }
            )
        scenarios = []
        for ds, dv, dr, dt in spec.grid():
            s_val = replace(valuation, as_of=valuation.as_of + timedelta(days=dt))
            s_spot = spot0 * (1.0 + ds / 100.0)
            s_market = replace(
                market,
                spot=Decimal(repr(s_spot)),
                rate=market.rate + Decimal(repr(dr)) / Decimal(10_000),
            )
            total = 0.0
            expired = 0
            detail = {}
            for p in positions:
                base_vol = base_vols[p.position_id]
                vol = (
                    vol_source(p, s_market, s_val)
                    if spec.surface_dynamics == "sticky_moneyness"
                    else base_vol
                ) + dv / 100.0
                price, _, engine = self._price(p, s_val, s_market, vol, spec, ())
                expired += engine == "settled_at_intrinsic"
                v = float(p.quantity) * float(p.contract.multiplier) * price
                total += v
                if include_position_detail:
                    detail[p.position_id] = v
            pnl = total - base_value
            dS, dsig, dT, drate = s_spot - spot0, dv / 100.0, dt / 365.0, dr / 10_000.0
            approx: float | None = None
            if expired == 0 and all(agg[g.value] is not None for g in AGG_GREEKS):
                a = {k: float(v) for k, v in agg.items() if v is not None}
                approx = (
                    a["delta"] * dS
                    + 0.5 * a["gamma"] * dS * dS
                    + a["vega"] * dsig
                    + a["theta"] * dT
                    + a["rho"] * drate
                )
            row: dict[str, Any] = {
                "spot_shock_pct": ds,
                "vol_shock_pts": dv,
                "rate_shock_bp": dr,
                "time_roll_days": dt,
                "spot": s_spot,
                "value": total,
                "pnl": pnl,
                "approx_pnl": approx,
                "unexplained": None if approx is None else pnl - approx,
                "positions_settled": expired,
            }
            if include_position_detail:
                row["position_values"] = detail
            scenarios.append(row)
        T_min = min(year_fraction(valuation.as_of, p.contract.expiry) for p in positions)
        return PortfolioResult(
            base={
                "value": base_value,
                "currency": positions[0].contract.currency,
                "spot": spot0,
                "greeks_raw": agg,
                "greeks_display": _display(agg, spot0),
                "shortest_time_to_expiry_years": T_min,
            },
            positions=base_rows,
            scenarios=scenarios,
            assumptions={
                "value": "signed quantity x contract multiplier x price per unit",
                "surface_dynamics": spec.surface_dynamics,
                "spot_shock": "percent of base spot",
                "vol_shock": "absolute volatility points added to each position's volatility",
                "rate_shock": "basis points added to the continuously compounded rate",
                "time_roll": "calendar days; valuation time moves forward, spot/vol held except "
                "for shocks; dividends whose ex-date passes leave the schedule",
                "expiry": "positions at/after expiry settle at intrinsic value at scenario spot, "
                "held as cash without interest",
                "approximation": "delta*dS + 0.5*gamma*dS^2 + vega*dsigma + theta*dt + rho*dr "
                "with base Greeks; 'unexplained' = full repricing P&L - approximation",
                "engines": "European: closed-form BSM; American: CRR tree "
                f"({spec.american_steps} steps, refinement allowed)",
            },
            engines_used=engines,
            work=work,
        )


def _display(agg: dict[str, float | None], spot: float) -> dict[str, float | None]:
    def f(k: str, factor: float) -> float | None:
        v = agg.get(k)
        return None if v is None else v * factor

    return {
        "delta_units_of_underlying": f("delta", 1.0),
        "delta_notional": f("delta", spot),
        "gamma_units_per_1_spot": f("gamma", 1.0),
        "vega_per_vol_point": f("vega", 0.01),
        "theta_per_calendar_day": f("theta", 1 / 365),
        "rho_per_1pct": f("rho", 0.01),
    }
