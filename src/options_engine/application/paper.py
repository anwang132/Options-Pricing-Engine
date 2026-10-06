"""Paper-trading ledger: record trades, mark positions, track P&L. No real money moves.

Accounting (ADR 0016):
* Cash, prices, fees and realized P&L are exact Decimals. Model values are
  float64, as elsewhere, and are converted to Decimal only when combined with
  cash, via their shortest repr.
* Average-cost method per contract. A trade that crosses zero closes the old
  position at the average cost and opens the remainder at the trade price.
* Cash flow of a trade = -(signed quantity x multiplier x price) - fees.
  Fees are tracked separately: net realized P&L = realized trading P&L - fees.
* Expiry settlement closes every position of that underlying and expiry at
  intrinsic value against the settlement price (cash-equivalent, even for
  physically settled contracts).
* Invariant, tested: equity - starting cash = net realized P&L + unrealized P&L.
* Replay at time t uses events with effective timestamp <= t; voided trades are
  excluded everywhere (the void itself stays visible in the log).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from options_engine.adapters.paper_store import LedgerStore
from options_engine.adapters.snapshots.store import default_data_dir
from options_engine.application.pricing import PriceRequest, PricingService
from options_engine.domain.contracts import Settlement, VanillaContract
from options_engine.domain.conventions import ExerciseStyle, OptionType, SettlementType
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.market import MarketSnapshot, ValuationContext
from options_engine.domain.numerics import AnalyticConfig, CRRConfig
from options_engine.models.black_scholes import BlackScholesModel

MARK_TREE_STEPS = 500
MAX_POSITIONS_PER_MARK = 100
IDENTITY_TOLERANCE = Decimal("1e-9")  # currency units


def contract_key(c: VanillaContract) -> str:
    """Human-readable identity of a contract within an account."""
    kind = "C" if c.option_type is OptionType.CALL else "P"
    style = "AM" if c.exercise_style is ExerciseStyle.AMERICAN else "EU"
    when = c.expiry.strftime("%Y-%m-%d %H:%M")
    return f"{c.underlying} {when}Z {kind}{c.strike} {style} x{c.multiplier}"


@dataclass
class OpenPosition:
    contract: VanillaContract
    quantity: Decimal = Decimal(0)  # signed contracts
    average_price: Decimal = Decimal(0)  # per underlying unit
    realized: Decimal = Decimal(0)  # trading P&L realized on this contract, before fees


@dataclass
class LedgerState:
    account_id: str
    name: str
    currency: str
    starting_cash: Decimal
    opened_at: datetime
    cash: Decimal
    fees: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)
    positions: dict[str, OpenPosition] = field(default_factory=dict)
    trades: int = 0
    voided: set[str] = field(default_factory=set)

    @property
    def net_realized(self) -> Decimal:
        return self.realized - self.fees


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def apply_fill(
    state: LedgerState,
    contract: VanillaContract,
    signed_qty: Decimal,
    price: Decimal,
    fees: Decimal,
) -> None:
    """Average-cost update for one fill."""
    key = contract_key(contract)
    pos = state.positions.setdefault(key, OpenPosition(contract))
    mult = contract.multiplier
    q = pos.quantity
    if q == 0 or (q > 0) == (signed_qty > 0):
        total = abs(q) + abs(signed_qty)
        pos.average_price = (abs(q) * pos.average_price + abs(signed_qty) * price) / total
        pos.quantity = q + signed_qty
    else:
        closing = min(abs(signed_qty), abs(q))
        pnl = closing * (price - pos.average_price) * (1 if q > 0 else -1) * mult
        pos.realized += pnl
        state.realized += pnl
        pos.quantity = q + signed_qty
        if pos.quantity == 0:
            pos.average_price = Decimal(0)
        elif (pos.quantity > 0) != (q > 0):  # crossed zero: remainder opened at the trade price
            pos.average_price = price
    state.cash -= signed_qty * mult * price + fees
    state.fees += fees
    if pos.quantity == 0:
        del state.positions[key]


def replay(events: list[dict[str, Any]], until: datetime | None = None) -> LedgerState:
    opening = events[0]
    if opening["type"] != "open_account":
        raise DomainError(ErrorCode.NUMERICAL_FAILURE, "ledger does not start with open_account")
    p = opening["payload"]
    start = Decimal(p["starting_cash"])
    state = LedgerState(
        p["account_id"], p["name"], p["currency"], start, _ts(opening["timestamp"]), start
    )
    state.voided = {e["payload"]["trade_id"] for e in events if e["type"] == "void"}
    ordered = sorted(events[1:], key=lambda e: (e["timestamp"], e["seq"]))
    for e in ordered:
        if until is not None and _ts(e["timestamp"]) > until:
            break
        payload = e["payload"]
        if e["type"] == "trade" and payload["trade_id"] not in state.voided:
            contract = contract_from_payload(payload["contract"])
            qty = Decimal(payload["quantity"]) * (1 if payload["side"] == "buy" else -1)
            apply_fill(state, contract, qty, Decimal(payload["price"]), Decimal(payload["fees"]))
            state.trades += 1
        elif e["type"] == "settlement":
            settle = Decimal(payload["settlement_price"])
            for key, pos in list(state.positions.items()):
                c = pos.contract
                if c.underlying == payload["underlying"] and c.expiry == _ts(payload["expiry"]):
                    intrinsic = max(c.option_type.sign * (settle - c.strike), Decimal(0))
                    apply_fill(state, c, -pos.quantity, intrinsic, Decimal(0))
                    assert key not in state.positions
    return state


def contract_from_payload(d: dict[str, Any]) -> VanillaContract:
    return VanillaContract(
        underlying=d["underlying"],
        currency=d["currency"],
        strike=Decimal(d["strike"]),
        option_type=OptionType(d["option_type"]),
        exercise_style=ExerciseStyle(d["exercise_style"]),
        expiry=_ts(d["expiry"]),
        multiplier=Decimal(d["multiplier"]),
        settlement=Settlement(SettlementType(d["settlement"]["type"]), d["settlement"]["lag_days"]),
    )


def _dec(x: float) -> Decimal:
    return Decimal(repr(x))


class PaperTradingService:
    def __init__(self, data_dir: Path | None = None, pricing: PricingService | None = None) -> None:
        self.store = LedgerStore(data_dir or default_data_dir())
        self.pricing = pricing or PricingService()

    # --- commands -------------------------------------------------------------------------

    def open_account(
        self, name: str, currency: str, starting_cash: Decimal, opened_at: datetime
    ) -> dict[str, Any]:
        if starting_cash < 0:
            raise DomainError(ErrorCode.INVALID_REQUEST, "starting cash cannot be negative")
        if not name.strip():
            raise DomainError(ErrorCode.INVALID_REQUEST, "account name is required")
        account_id = self.store.new_account_id()
        payload = {
            "account_id": account_id,
            "name": name.strip(),
            "currency": currency,
            "starting_cash": starting_cash,
        }
        self.store.append(account_id, "open_account", opened_at, payload, create=True)
        return self.summary(account_id)

    def record_trade(
        self,
        account_id: str,
        timestamp: datetime,
        contract: VanillaContract,
        side: str,
        quantity: Decimal,
        price: Decimal,
        fees: Decimal,
        note: str = "",
    ) -> dict[str, Any]:
        state = self._state(account_id)
        if side not in ("buy", "sell"):
            raise DomainError(ErrorCode.INVALID_REQUEST, "side must be 'buy' or 'sell'")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "quantity must be a positive whole number of contracts"
            )
        if price < 0 or fees < 0:
            raise DomainError(ErrorCode.INVALID_REQUEST, "price and fees cannot be negative")
        if contract.currency != state.currency:
            raise DomainError(
                ErrorCode.UNSUPPORTED_CONTRACT,
                f"account currency is {state.currency}; "
                f"trades in {contract.currency} need an FX model",
            )
        if timestamp < state.opened_at:
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "trade time is before the account was opened"
            )
        if timestamp >= contract.expiry:
            raise DomainError(
                ErrorCode.EXPIRED_CONTRACT, "trade time is at or after the contract's expiry"
            )
        contract.ensure_priceable()
        payload = {
            "contract": contract,
            "side": side,
            "quantity": quantity,
            "price": price,
            "fees": fees,
            "note": note[:200],
        }
        self.store.append(account_id, "trade", timestamp, payload, id_field="trade_id")
        return self.summary(account_id)

    def void_trade(
        self, account_id: str, trade_id: str, reason: str, timestamp: datetime
    ) -> dict[str, Any]:
        events = self.store.events(account_id)
        trades = {e["payload"]["trade_id"] for e in events if e["type"] == "trade"}
        voided = {e["payload"]["trade_id"] for e in events if e["type"] == "void"}
        if trade_id not in trades:
            raise DomainError(ErrorCode.NOT_FOUND, f"trade {trade_id} not found")
        if trade_id in voided:
            raise DomainError(ErrorCode.INVALID_REQUEST, f"trade {trade_id} is already void")
        if not reason.strip():
            raise DomainError(ErrorCode.INVALID_REQUEST, "a reason is required to void a trade")
        self.store.append(
            account_id, "void", timestamp, {"trade_id": trade_id, "reason": reason[:200]}
        )
        return self.summary(account_id)

    def settle_expiry(
        self,
        account_id: str,
        underlying: str,
        expiry: datetime,
        settlement_price: Decimal,
        timestamp: datetime,
    ) -> dict[str, Any]:
        if settlement_price <= 0:
            raise DomainError(ErrorCode.INVALID_REQUEST, "settlement price must be positive")
        if timestamp < expiry:
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "settlement cannot be recorded before expiry"
            )
        state = self._state(account_id)
        if not any(
            p.contract.underlying == underlying and p.contract.expiry == expiry
            for p in state.positions.values()
        ):
            raise DomainError(
                ErrorCode.NOT_FOUND, "no open position with that underlying and expiry"
            )
        payload = {"underlying": underlying, "expiry": expiry, "settlement_price": settlement_price}
        self.store.append(account_id, "settlement", timestamp, payload)
        return self.summary(account_id)

    def mark(
        self,
        account_id: str,
        as_of: datetime,
        markets: dict[str, MarketSnapshot],
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Value open positions at ``as_of`` and append the result to the ledger.

        ``markets`` holds one market (spot, carry) per underlying; ``inputs`` maps a
        position key to an ``observed_price`` and/or a ``volatility``.
        """
        state = self._state(account_id, until=as_of)
        if len(state.positions) > MAX_POSITIONS_PER_MARK:
            raise DomainError(
                ErrorCode.WORK_LIMIT_EXCEEDED, f"more than {MAX_POSITIONS_PER_MARK} positions"
            )
        missing = sorted({p.contract.underlying for p in state.positions.values()} - set(markets))
        if missing:
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                "market inputs missing for underlying(s): " + ", ".join(missing),
                {"missing_underlyings": missing},
            )
        rows, total_value, total_unrealized = [], Decimal(0), Decimal(0)
        valuation = ValuationContext(as_of)
        for key, pos in sorted(state.positions.items()):
            c = pos.contract
            market = markets[c.underlying]
            given = inputs.get(key, {})
            observed = given.get("observed_price")
            vol = given.get("volatility")
            model_price = None
            status = "ok"
            if as_of >= c.expiry:
                model_price = float(max(c.option_type.sign * (market.spot - c.strike), Decimal(0)))
                status = "expired_awaiting_settlement"
            elif vol is not None:
                engine, config = (
                    (
                        "crr_tree",
                        CRRConfig(
                            steps=MARK_TREE_STEPS,
                            allow_step_refinement=True,
                            odd_even_diagnostic=False,
                            early_exercise_diagnostic=False,
                        ),
                    )
                    if c.exercise_style is ExerciseStyle.AMERICAN
                    else ("bsm_analytic", AnalyticConfig())
                )
                _, _, out = self.pricing.evaluate(
                    PriceRequest(
                        c, valuation, market, BlackScholesModel(float(vol)), engine, config, ()
                    )
                )
                model_price = out.price
            if observed is None and model_price is None:
                raise DomainError(
                    ErrorCode.INVALID_REQUEST,
                    f"position '{key}' needs an observed price or a volatility to be marked",
                    {"position": key},
                )
            unit_value = (
                Decimal(observed) if observed is not None else _dec(float(model_price or 0.0))
            )
            basis = (
                "market" if observed is not None else ("intrinsic" if status != "ok" else "model")
            )
            value = pos.quantity * c.multiplier * unit_value
            cost = pos.quantity * c.multiplier * pos.average_price
            unrealized = value - cost
            total_value += value
            total_unrealized += unrealized
            rows.append(
                {
                    "key": key,
                    "quantity": pos.quantity,
                    "average_price": pos.average_price,
                    "mark_basis": basis,
                    "observed_price": None if observed is None else Decimal(observed),
                    "model_price": model_price,
                    "model_minus_market": None
                    if (observed is None or model_price is None or status != "ok")
                    else model_price - float(observed),
                    "volatility": vol,
                    "value": value,
                    "unrealized": unrealized,
                    "status": status,
                }
            )
        equity = state.cash + total_value
        result = {
            "as_of": as_of,
            "cash": state.cash,
            "positions_value": total_value,
            "equity": equity,
            "realized": state.realized,
            "fees": state.fees,
            "net_realized": state.net_realized,
            "unrealized": total_unrealized,
            "total_pnl": equity - state.starting_cash,
            "positions": rows,
        }
        # Accounting identity. Average-cost prices come from Decimal division (28 significant
        # digits), so it holds to ~1e-25, not exactly; a larger gap means an accounting bug.
        gap = (equity - state.starting_cash) - (state.net_realized + total_unrealized)
        if abs(gap) > IDENTITY_TOLERANCE:
            raise DomainError(ErrorCode.NUMERICAL_FAILURE, f"accounting identity violated by {gap}")
        payload = {"markets": markets, "inputs": inputs, "result": result}
        self.store.append(account_id, "mark", as_of, payload)
        return result

    # --- queries ----------------------------------------------------------------------------

    def _state(self, account_id: str, until: datetime | None = None) -> LedgerState:
        return replay(self.store.events(account_id), until)

    def summary(self, account_id: str) -> dict[str, Any]:
        events = self.store.events(account_id)
        state = replay(events)
        marks = [e for e in events if e["type"] == "mark"]
        return {
            "account_id": account_id,
            "name": state.name,
            "currency": state.currency,
            "starting_cash": state.starting_cash,
            "opened_at": state.opened_at,
            "cash": state.cash,
            "realized": state.realized,
            "fees": state.fees,
            "net_realized": state.net_realized,
            "trades": state.trades,
            "voided_trades": sorted(state.voided),
            "positions": [
                {
                    "key": k,
                    "contract": p.contract,
                    "quantity": p.quantity,
                    "average_price": p.average_price,
                    "realized": p.realized,
                }
                for k, p in sorted(state.positions.items())
            ],
            "last_mark": marks[-1]["payload"]["result"] if marks else None,
            "integrity": self.store.verify(account_id),
        }

    def accounts(self) -> list[dict[str, Any]]:
        out = []
        for aid in self.store.account_ids():
            opening = self.store.events(aid)[0]["payload"]
            out.append(
                {"account_id": aid, "name": opening["name"], "currency": opening["currency"]}
            )
        return out

    def history(self, account_id: str) -> list[dict[str, Any]]:
        points = []
        for e in self.store.events(account_id):
            if e["type"] == "mark":
                r = e["payload"]["result"]
                points.append(
                    {
                        k: r[k]
                        for k in (
                            "as_of",
                            "equity",
                            "cash",
                            "net_realized",
                            "unrealized",
                            "total_pnl",
                        )
                    }
                )
        return sorted(points, key=lambda p: p["as_of"])

    def events(self, account_id: str) -> list[dict[str, Any]]:
        return self.store.events(account_id)
