"""Paper-trading ledger. Expected numbers are worked by hand in the comments."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from options_engine.application.paper import PaperTradingService, contract_key
from options_engine.domain.conventions import OptionType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot
from tests.conftest import make_contract

T0 = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
EXPIRY = datetime(2026, 12, 18, 21, 0, tzinfo=UTC)
CALL = make_contract(strike=Decimal("40"), expiry=EXPIRY)
PUT = make_contract(strike=Decimal("40"), expiry=EXPIRY, option_type=OptionType.PUT)
MKT = {"SYNTH": MarketSnapshot(spot=Decimal("42"), rate=Decimal("0.05"))}


@pytest.fixture
def svc(tmp_path) -> PaperTradingService:
    return PaperTradingService(tmp_path)


def open_acct(svc: PaperTradingService) -> str:
    return str(svc.open_account("test", "USD", Decimal("10000"), T0)["account_id"])


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_average_cost_partial_close_and_crossing_zero(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(2), Decimal("1.50"), Decimal("1.30"))
    s = svc.record_trade(a, at(2), CALL, "buy", Decimal(1), Decimal("2.10"), Decimal("0.65"))
    # avg = (2*1.50 + 1*2.10)/3 = 1.70; cash = 10000 - 300 - 1.30 - 210 - 0.65 = 9488.05
    assert s["cash"] == Decimal("9488.05")
    assert s["positions"][0]["average_price"] == Decimal("1.70")
    s = svc.record_trade(a, at(3), CALL, "sell", Decimal(2), Decimal("2.50"), Decimal("1.30"))
    # realized = 2*(2.50-1.70)*100 = 160; cash = 9488.05 + 500 - 1.30 = 9986.75
    assert s["realized"] == Decimal("160")
    assert s["cash"] == Decimal("9986.75")
    assert s["positions"][0]["quantity"] == Decimal(1)
    s = svc.record_trade(a, at(4), CALL, "sell", Decimal(3), Decimal("2.00"), Decimal("1.95"))
    # closes 1 long: +1*(2.00-1.70)*100 = 30 -> realized 190; short 2 opened at 2.00
    # cash = 9986.75 + 600 - 1.95 = 10584.80; fees = 1.30+0.65+1.30+1.95 = 5.20
    assert s["realized"] == Decimal("190")
    assert s["fees"] == Decimal("5.20")
    assert s["net_realized"] == Decimal("184.80")
    assert s["cash"] == Decimal("10584.80")
    pos = s["positions"][0]
    assert (pos["quantity"], pos["average_price"]) == (Decimal(-2), Decimal("2.00"))


def test_mark_to_market_and_identity(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(2), Decimal("1.50"), Decimal("1.30"))
    key = contract_key(CALL)
    r = svc.mark(a, at(60), MKT, {key: {"observed_price": "2.25"}})
    # value = 2*100*2.25 = 450; unrealized = 450 - 300 = 150; equity = 9698.70 + 450 = 10148.70
    assert r["positions_value"] == Decimal("450.00")
    assert r["unrealized"] == Decimal("150.00")
    assert r["equity"] == Decimal("10148.70")
    assert r["total_pnl"] == r["net_realized"] + r["unrealized"] == Decimal("148.70")
    assert r["positions"][0]["mark_basis"] == "market"


def test_model_mark_matches_pricing_engine(svc):
    from options_engine.application.pricing import PriceRequest, PricingService
    from options_engine.domain.market import ValuationContext
    from options_engine.models.black_scholes import BlackScholesModel

    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(1), Decimal("3.00"), Decimal(0))
    key = contract_key(CALL)
    r = svc.mark(a, at(60), MKT, {key: {"volatility": 0.25, "observed_price": "3.40"}})
    engine = PricingService().price(
        PriceRequest(CALL, ValuationContext(at(60)), MKT["SYNTH"], BlackScholesModel(0.25))
    )
    row = r["positions"][0]
    assert row["model_price"] == engine.price
    assert row["mark_basis"] == "market"  # observed price wins; model shown alongside
    assert row["model_minus_market"] == pytest.approx(engine.price - 3.40)
    r2 = svc.mark(a, at(61), MKT, {key: {"volatility": 0.25}})
    assert r2["positions"][0]["mark_basis"] == "model"


def test_expiry_settlement(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), PUT, "sell", Decimal(3), Decimal("1.20"), Decimal(0))
    with pytest.raises(DomainError):
        svc.settle_expiry(a, "SYNTH", EXPIRY, Decimal("38.50"), EXPIRY - timedelta(hours=1))
    s = svc.settle_expiry(a, "SYNTH", EXPIRY, Decimal("38.50"), EXPIRY + timedelta(minutes=5))
    # short 3 puts at 1.20, intrinsic 1.50: realized = 3*(1.20-1.50)*100 = -90
    # cash = 10000 + 360 - 450 = 9910
    assert s["positions"] == []
    assert s["realized"] == Decimal("-90")
    assert s["cash"] == Decimal("9910")


def test_void_removes_trade_but_keeps_audit_trail(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(5), Decimal("1.00"), Decimal(0))
    s = svc.void_trade(a, "t00001", "fat finger", at(2))
    assert s["cash"] == Decimal("10000")
    assert s["voided_trades"] == ["t00001"]
    kinds = [e["type"] for e in svc.events(a)]
    assert kinds == ["open_account", "trade", "void"]
    with pytest.raises(DomainError):
        svc.void_trade(a, "t00001", "again", at(3))


def test_backdated_trade_respects_effective_time(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(30), CALL, "buy", Decimal(1), Decimal("2.00"), Decimal(0))
    key = contract_key(CALL)
    early = svc.mark(a, at(10), MKT, {})  # before the trade's effective time
    assert early["positions"] == []
    late = svc.mark(a, at(40), MKT, {key: {"observed_price": "2.00"}})
    assert late["positions_value"] == Decimal("200.00")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (
            {"contract": make_contract(currency="EUR", expiry=EXPIRY)},
            ErrorCode.UNSUPPORTED_CONTRACT,
        ),
        ({"quantity": Decimal("1.5")}, ErrorCode.INVALID_REQUEST),
        ({"quantity": Decimal(0)}, ErrorCode.INVALID_REQUEST),
        ({"price": Decimal("-1")}, ErrorCode.INVALID_REQUEST),
        ({"timestamp": EXPIRY}, ErrorCode.EXPIRED_CONTRACT),
        ({"timestamp": T0 - timedelta(days=1)}, ErrorCode.INVALID_REQUEST),
        ({"side": "short"}, ErrorCode.INVALID_REQUEST),
    ],
)
def test_invalid_trades_rejected(svc, kwargs, code):
    a = open_acct(svc)
    args = {
        "timestamp": at(1),
        "contract": CALL,
        "side": "buy",
        "quantity": Decimal(1),
        "price": Decimal("1"),
        "fees": Decimal(0),
    }
    args.update(kwargs)
    with pytest.raises(DomainError) as e:
        svc.record_trade(a, **args)
    assert e.value.code is code
    assert svc.summary(a)["trades"] == 0  # nothing written


def test_mark_requires_inputs(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(1), Decimal("2.00"), Decimal(0))
    with pytest.raises(DomainError, match="observed price or a volatility"):
        svc.mark(a, at(5), MKT, {})
    with pytest.raises(DomainError, match="missing for underlying"):
        svc.mark(a, at(5), {}, {contract_key(CALL): {"observed_price": "2"}})


def test_hash_chain_detects_tampering(svc):
    a = open_acct(svc)
    svc.record_trade(a, at(1), CALL, "buy", Decimal(1), Decimal("2.00"), Decimal(0))
    assert svc.store.verify(a)["intact"] is True
    path = svc.store._path(a)
    lines = path.read_text().splitlines()
    doctored = json.loads(lines[1])
    doctored["payload"]["price"] = "0.01"
    lines[1] = json.dumps(doctored)
    path.write_text("\n".join(lines) + "\n")
    v = svc.store.verify(a)
    assert v["intact"] is False
    assert v["first_bad_seq"] == 1


def test_concurrent_appends_keep_the_chain(svc):
    a = open_acct(svc)

    def worker(i: int) -> None:
        svc.record_trade(a, at(1 + i), CALL, "buy", Decimal(1), Decimal("1.00"), Decimal(0))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert svc.store.verify(a)["intact"] is True
    assert svc.store.verify(a)["events"] == 9
    ids = [e["payload"]["trade_id"] for e in svc.events(a) if e["type"] == "trade"]
    assert len(set(ids)) == 8  # ids assigned under the lock are unique
    assert svc.summary(a)["positions"][0]["quantity"] == Decimal(8)


def test_unknown_account(svc):
    with pytest.raises(DomainError) as e:
        svc.summary("acct-000000000000")
    assert e.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(DomainError):
        svc.summary("../../etc")
