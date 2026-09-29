from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from options_engine.application.portfolio import PortfolioService, Position, ScenarioSpec
from options_engine.application.pricing import PricingService
from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from tests.conftest import AS_OF, make_contract

VAL = ValuationContext(AS_OF)
MKT = MarketSnapshot(spot=Decimal("100"), rate=Decimal("0.03"), dividend_yield=Decimal("0.01"))
SPEC = ScenarioSpec(
    spot_shocks_pct=(-10.0, -1.0, 0.0, 1.0, 10.0),
    vol_shocks_pts=(-2.0, 0.0, 2.0),
    rate_shocks_bp=(0.0, 25.0),
    time_roll_days=(0.0, 7.0),
)


def pos(pid: str, qty: str, **kw) -> Position:
    contract_kw = {"strike": Decimal("100"), "expiry": AS_OF + timedelta(days=91)}
    contract_kw.update(kw)
    return Position(pid, make_contract(**contract_kw), Decimal(qty), 0.25)


def run(positions, spec=SPEC, **kw):
    return PortfolioService().run(positions, VAL, MKT, spec, **kw)


def test_offsetting_positions_cancel_exactly():
    r = run([pos("long", "3"), pos("short", "-3")])
    assert r.base["value"] == 0.0
    assert all(v == 0.0 for v in r.base["greeks_raw"].values())
    assert all(s["value"] == 0.0 and s["pnl"] == 0.0 for s in r.scenarios)


def test_value_is_quantity_times_multiplier_times_price():
    p = pos("a", "-2", multiplier=Decimal("50"))
    r = run([p])
    unit = r.positions[0]["price_per_unit"]
    assert r.base["value"] == pytest.approx(-2 * 50 * unit, rel=1e-15)


def test_scaling_is_linear():
    one = run([pos("a", "1"), pos("b", "-1", option_type=OptionType.PUT, strike=Decimal("90"))])
    two = run([pos("a", "2"), pos("b", "-2", option_type=OptionType.PUT, strike=Decimal("90"))])
    assert two.base["value"] == pytest.approx(2 * one.base["value"], rel=1e-14)
    for a, b in zip(one.scenarios, two.scenarios, strict=True):
        assert b["pnl"] == pytest.approx(2 * a["pnl"], rel=1e-12, abs=1e-9)


def test_missing_multiplier_mutant_is_detected():
    class NoMultiplier(PortfolioService):
        def run(self, positions, valuation, market, spec, **kw):  # type: ignore[override]
            stripped = [
                Position(
                    p.position_id,
                    p.contract.__class__(
                        **{
                            **{f: getattr(p.contract, f) for f in p.contract.__slots__},
                            "multiplier": Decimal(1),
                        }
                    ),
                    p.quantity,
                    p.volatility,
                )
                for p in positions
            ]
            return super().run(stripped, valuation, market, spec, **kw)

    p = pos("a", "1")
    good = PortfolioService().run([p], VAL, MKT, SPEC)
    bad = NoMultiplier().run([p], VAL, MKT, SPEC)
    unit = good.positions[0]["price_per_unit"]
    assert good.base["value"] == pytest.approx(100 * unit)  # the check a mutant must fail
    assert bad.base["value"] != pytest.approx(100 * unit)


def test_zero_shock_scenario_reproduces_base():
    r = run([pos("a", "1"), pos("b", "-2", strike=Decimal("110"))])
    zero = next(
        s
        for s in r.scenarios
        if s["spot_shock_pct"] == 0
        and s["vol_shock_pts"] == 0
        and s["rate_shock_bp"] == 0
        and s["time_roll_days"] == 0
    )
    assert zero["pnl"] == pytest.approx(0.0, abs=1e-9)


def test_greek_approximation_residual_small_for_small_shocks():
    r = run([pos("a", "10"), pos("b", "-5", strike=Decimal("105"))])
    small = [
        s
        for s in r.scenarios
        if abs(s["spot_shock_pct"]) == 1.0
        and s["time_roll_days"] == 0
        and s["rate_shock_bp"] == 0
        and s["vol_shock_pts"] == 0
    ]
    for s in small:
        assert abs(s["unexplained"]) < 0.05 * abs(s["pnl"])
    large = [s for s in r.scenarios if abs(s["spot_shock_pct"]) == 10.0]
    assert max(abs(s["unexplained"]) for s in large) > max(abs(s["unexplained"]) for s in small)


def test_expiry_crossing_settles_at_intrinsic():
    p = pos("a", "1", expiry=AS_OF + timedelta(days=3))
    r = run(
        [p], ScenarioSpec(spot_shocks_pct=(10.0,), vol_shocks_pts=(0.0,), time_roll_days=(7.0,))
    )
    s = r.scenarios[0]
    assert s["positions_settled"] == 1
    assert s["value"] == pytest.approx(100 * 10.0)
    assert s["approx_pnl"] is None and s["unexplained"] is None


def test_american_positions_use_the_tree():
    r = run([pos("am", "1", exercise_style=ExerciseStyle.AMERICAN, option_type=OptionType.PUT)])
    assert r.engines_used == {"am": "crr_tree"}
    eu = run([pos("eu", "1", option_type=OptionType.PUT)])
    assert r.base["value"] >= eu.base["value"]


@pytest.mark.parametrize(
    ("positions", "spec", "code"),
    [
        ([pos("a", "1"), pos("b", "1", currency="EUR")], SPEC, ErrorCode.UNSUPPORTED_CONTRACT),
        ([pos("a", "1"), pos("b", "1", underlying="OTHER")], SPEC, ErrorCode.UNSUPPORTED_CONTRACT),
        ([pos("a", "1"), pos("a", "2")], SPEC, ErrorCode.INVALID_REQUEST),
        ([pos("a", "1")], ScenarioSpec(vol_shocks_pts=(-30.0,)), ErrorCode.INVALID_REQUEST),
        (
            [pos("a", "1")],
            ScenarioSpec(surface_dynamics="sticky_moneyness"),
            ErrorCode.INVALID_REQUEST,
        ),
        ([pos("a", "1")], ScenarioSpec(surface_dynamics="recalibrated"), ErrorCode.INVALID_REQUEST),
        (
            [pos("a", "1")],
            ScenarioSpec(
                spot_shocks_pct=tuple(range(-50, 51)), vol_shocks_pts=tuple(range(-9, 11))
            ),
            ErrorCode.WORK_LIMIT_EXCEEDED,
        ),
        ([pos("a", "0")], SPEC, ErrorCode.INVALID_REQUEST),
        (
            [pos(f"am{i}", "1", exercise_style=ExerciseStyle.AMERICAN) for i in range(11)],
            ScenarioSpec(
                spot_shocks_pct=tuple(float(x) for x in range(-50, 50)),
                vol_shocks_pts=(0.0,),
                american_steps=2000,
            ),
            ErrorCode.WORK_LIMIT_EXCEEDED,
        ),
    ],
)
def test_invalid_portfolios_rejected(positions, spec, code):
    with pytest.raises(DomainError) as e:
        run(positions, spec)
    assert e.value.code is code


def test_matches_single_contract_pricing():
    p = pos("a", "1")
    from options_engine.application.pricing import PriceRequest
    from options_engine.models.black_scholes import BlackScholesModel

    single = PricingService().price(PriceRequest(p.contract, VAL, MKT, BlackScholesModel(0.25)))
    assert run([p]).positions[0]["price_per_unit"] == single.price
