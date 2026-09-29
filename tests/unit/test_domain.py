from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from options_engine.domain.contracts import Settlement
from options_engine.domain.conventions import to_decimal, year_fraction
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import CashDividend, MarketSnapshot, ValuationContext
from options_engine.domain.numerics import CRRConfig, MonteCarloConfig
from options_engine.models.black_scholes import BlackScholesModel
from tests.conftest import AS_OF, make_contract


def code_of(excinfo: pytest.ExceptionInfo[DomainError]) -> ErrorCode:
    return excinfo.value.code


class TestYearFraction:
    def test_act365f_exact_seconds(self):
        assert year_fraction(AS_OF, AS_OF + timedelta(days=365)) == 1.0
        assert year_fraction(AS_OF, AS_OF + timedelta(days=182.5)) == 0.5
        assert year_fraction(AS_OF, AS_OF + timedelta(hours=6)) == pytest.approx(0.25 / 365)

    def test_signed(self):
        assert year_fraction(AS_OF, AS_OF - timedelta(days=73)) == -0.2

    def test_naive_timestamps_rejected(self):
        with pytest.raises(DomainError) as e:
            year_fraction(datetime(2026, 1, 1), AS_OF)
        assert code_of(e) is ErrorCode.INVALID_VALUATION_CONTEXT


class TestDecimalIngestion:
    def test_strings_keep_precision(self):
        assert str(to_decimal("40.10", "strike")) == "40.10"

    def test_float_uses_shortest_repr_not_binary_expansion(self):
        assert to_decimal(0.1, "x") == Decimal("0.1")

    @pytest.mark.parametrize("bad", ["nan", "inf", "abc", True, None, [1]])
    def test_rejects_non_numbers(self, bad):
        with pytest.raises(DomainError):
            to_decimal(bad, "x")


class TestContract:
    @pytest.mark.parametrize(
        ("kw", "fragment"),
        [
            ({"strike": Decimal("0")}, "strike"),
            ({"strike": Decimal("-1")}, "strike"),
            ({"strike": Decimal("NaN")}, "strike"),
            ({"multiplier": Decimal("0")}, "multiplier"),
            ({"currency": "usd"}, "currency"),
            ({"underlying": " "}, "underlying"),
            ({"expiry": datetime(2027, 1, 1)}, "timezone"),
        ],
    )
    def test_invalid_contract(self, kw, fragment):
        with pytest.raises(DomainError) as e:
            make_contract(**kw)
        assert code_of(e) is ErrorCode.INVALID_CONTRACT
        assert fragment in e.value.message

    def test_last_trade_after_expiry_rejected(self):
        c = make_contract()
        with pytest.raises(DomainError):
            make_contract(last_trade=c.expiry + timedelta(seconds=1))

    def test_unsupported_features_rejected_before_pricing(self):
        with pytest.raises(DomainError) as e:
            make_contract(deliverable="adjusted: 100 shares + cash").ensure_priceable()
        assert code_of(e) is ErrorCode.UNSUPPORTED_CONTRACT
        with pytest.raises(DomainError) as e:
            make_contract(settlement=Settlement(lag_days=2)).ensure_priceable()
        assert code_of(e) is ErrorCode.UNSUPPORTED_CONTRACT


class TestMarket:
    def test_valid(self):
        m = MarketSnapshot(spot=Decimal("100"), rate=Decimal("-0.005"))
        assert m.observed_at is None  # unknown freshness stays unknown

    @pytest.mark.parametrize(
        "kw",
        [
            {"spot": Decimal("0")},
            {"spot": Decimal("Infinity")},
            {"rate": Decimal("1.5")},
            {"dividend_yield": Decimal("-2")},
        ],
    )
    def test_invalid(self, kw):
        base = {"spot": Decimal("100"), "rate": Decimal("0.01")}
        base.update(kw)
        with pytest.raises(DomainError) as e:
            MarketSnapshot(**base)
        assert code_of(e) is ErrorCode.INVALID_MARKET_INPUT

    def test_dividends_validated(self):
        with pytest.raises(DomainError):
            CashDividend(AS_OF, Decimal("0"))
        d = CashDividend(AS_OF + timedelta(days=10), Decimal("1"))
        with pytest.raises(DomainError):
            MarketSnapshot(spot=Decimal("100"), rate=Decimal("0"), cash_dividends=(d, d))

    def test_valuation_context_requires_tz(self):
        with pytest.raises(DomainError):
            ValuationContext(datetime(2026, 1, 1))


class TestModelAndConfig:
    @pytest.mark.parametrize("vol", [-0.1, float("nan"), float("inf"), 5.01])
    def test_invalid_volatility(self, vol):
        with pytest.raises(DomainError) as e:
            BlackScholesModel(vol)
        assert code_of(e) is ErrorCode.INVALID_MODEL_PARAMETER

    def test_zero_volatility_allowed(self):
        assert BlackScholesModel(0.0).volatility == 0.0

    def test_work_limits(self):
        with pytest.raises(DomainError) as e:
            CRRConfig(steps=20_001)
        assert code_of(e) is ErrorCode.WORK_LIMIT_EXCEEDED
        with pytest.raises(DomainError) as e:
            MonteCarloConfig(paths=20_000_002)
        assert code_of(e) is ErrorCode.WORK_LIMIT_EXCEEDED

    def test_antithetic_requires_even_paths(self):
        with pytest.raises(DomainError) as e:
            MonteCarloConfig(paths=1001, antithetic=True)
        assert code_of(e) is ErrorCode.INVALID_NUMERICAL_CONFIG
        assert MonteCarloConfig(paths=1001, antithetic=False).paths == 1001

    def test_as_of_is_always_explicit(self):
        # ValuationContext has no default: there is no hidden wall-clock read.
        with pytest.raises(TypeError):
            ValuationContext()  # type: ignore[call-arg]
        assert ValuationContext(datetime.now(UTC)).as_of.tzinfo is not None
