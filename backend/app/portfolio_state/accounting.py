"""Pure average-cost accounting; no clock, database, bus, or exit policy.

Money stays Decimal internally. Profit/loss are gross; fees are independent
reported facts (None never means free). Position lifetime is not a trading day.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

ZERO = Decimal("0")
ExecutionMode = Literal["backtest", "simulated", "paper", "live"]
MODES = {"backtest", "simulated", "paper", "live"}


def decimal(value: Decimal | float | int) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("money/price must be finite")
    return result


def aware(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("event timestamps must be timezone-aware")


@dataclass(frozen=True)
class LedgerFill:
    ledger_seq: int
    venue_fill_id: str
    client_order_id: str
    trade_id: UUID
    execution_mode: str
    execution_venue: str
    symbol: str
    side: Literal["BUY", "SELL"]
    position_effect: Literal["open", "close"]
    qty: int
    price: Decimal
    venue_ts: datetime
    commission: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.execution_venue, self.venue_fill_id


@dataclass(frozen=True)
class PositionState:
    position_id: UUID
    trade_id: UUID
    execution_mode: str
    execution_venue: str
    symbol: str
    side: str
    qty: int
    avg_price: Decimal
    opened_at: datetime
    stop: Decimal | None = None
    target: Decimal | None = None
    status: str = "open"
    closed_at: datetime | None = None
    realized_profit: Decimal = ZERO
    realized_loss: Decimal = ZERO
    reported_fees: Decimal = ZERO
    unknown_fee_count: int = 0
    entry_qty: int = 0
    exit_qty: int = 0
    exit_notional: Decimal = ZERO
    exit_attempt: int = 0

    @property
    def realized_pnl(self) -> Decimal:
        return self.realized_profit - self.realized_loss

    @property
    def fees(self) -> Decimal | None:
        return None if self.unknown_fee_count else self.reported_fees

    @property
    def exit_price(self) -> Decimal | None:
        return self.exit_notional / self.exit_qty if self.exit_qty else None


@dataclass(frozen=True)
class AccountingResult:
    position: PositionState
    realized_delta: Decimal


def apply_fill(
    position: PositionState | None, fill: LedgerFill, *, new_position_id: UUID | None = None
) -> AccountingResult:
    """Return a new value; never mutate the input or clamp an overfill.

An opposite-side open is NOT implicitly a reduction/reversal. A reduction
must explicitly say close and have the opposite order side. Adds belong to
the same trade. A new position after a full close requires a fresh ID.
"""
    if type(fill.qty) is not int or fill.qty <= 0:
        raise ValueError("fill quantity must be a positive integer")
    if type(fill.ledger_seq) is not int or fill.ledger_seq <= 0:
        raise ValueError("ledger sequence must be positive")
    if not all((fill.venue_fill_id, fill.client_order_id, fill.symbol, fill.execution_venue)):
        raise ValueError("fill identity is incomplete")
    if fill.execution_mode not in MODES:
        raise ValueError("unknown execution mode")
    if (fill.execution_venue == "simulated") != (fill.execution_mode in {"backtest", "simulated"}):
        raise ValueError("execution mode/venue mismatch")
    if fill.side not in {"BUY", "SELL"} or fill.position_effect not in {"open", "close"}:
        raise ValueError("invalid side or position effect")
    aware(fill.venue_ts)
    price = decimal(fill.price)
    if price <= ZERO:
        raise ValueError("fill price must be positive")
    fee = None if fill.commission is None else decimal(fill.commission)
    stop = None if fill.stop is None else decimal(fill.stop)
    target = None if fill.target is None else decimal(fill.target)
    if position is not None:
        if position.qty <= 0 or position.status == "closed":
            raise ValueError("closed positions cannot be reused")
        if (position.trade_id, position.execution_mode, position.execution_venue, position.symbol) != (
            fill.trade_id, fill.execution_mode, fill.execution_venue, fill.symbol
        ):
            raise ValueError("fill does not belong to this position")
        if fill.venue_ts < position.opened_at:
            raise ValueError("fill predates the position")

    delta = ZERO
    if fill.position_effect == "open":
        if position is None:
            if new_position_id is None:
                raise ValueError("opening a position requires an ID")
            position = PositionState(
                position_id=new_position_id, trade_id=fill.trade_id,
                execution_mode=fill.execution_mode, execution_venue=fill.execution_venue,
                symbol=fill.symbol, side=fill.side, qty=fill.qty, avg_price=price,
                opened_at=fill.venue_ts, stop=stop, target=target, entry_qty=fill.qty,
            )
        else:
            if position.side != fill.side:
                raise ValueError("opposite-side open would reverse the position")
            total = position.qty + fill.qty
            position = replace(
                position, qty=total, entry_qty=position.entry_qty + fill.qty,
                avg_price=(position.avg_price * position.qty + price * fill.qty) / total,
            )
    else:
        if position is None:
            raise ValueError("no open position to reduce")
        if fill.side == position.side:
            raise ValueError("a close must have the opposite order side")
        if fill.qty > position.qty:
            raise ValueError("close quantity exceeds the position; reversal forbidden")
        delta = (price - position.avg_price) * fill.qty * (1 if position.side == "BUY" else -1)
        remaining = position.qty - fill.qty
        position = replace(
            position, qty=remaining, status="closed" if remaining == 0 else "closing",
            closed_at=fill.venue_ts if remaining == 0 else None,
            realized_profit=position.realized_profit + max(delta, ZERO),
            realized_loss=position.realized_loss + max(-delta, ZERO),
            exit_qty=position.exit_qty + fill.qty,
            exit_notional=position.exit_notional + price * fill.qty,
        )
    return AccountingResult(
        replace(position, reported_fees=position.reported_fees + (fee if fee is not None else ZERO),
                unknown_fee_count=position.unknown_fee_count + (fee is None)),
        delta,
    )
