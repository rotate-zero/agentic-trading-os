"""Detached read-side values; no I/O and no imports of read-side consumers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .accounting import PositionState, ZERO
from .ports import InFlightOrder, LedgerState, TERMINAL_STATUSES


@dataclass(frozen=True)
class OpenExposure:
    symbol: str
    direction: str
    qty: int
    avg_entry_price: Decimal | None
    stop: Decimal | None
    mark: Decimal | None
    unrealized_pnl: Decimal | None
    is_in_flight: bool


@dataclass(frozen=True)
class PortfolioSnapshot:
    execution_mode: str
    trading_day: date
    as_of: datetime
    positions: dict[str, PositionState]
    in_flight: dict[str, InFlightOrder]
    marks: dict[str, tuple[Decimal, datetime]]
    exposures: tuple[OpenExposure, ...]
    realized_profit_today: Decimal | None
    realized_loss_today: Decimal | None
    reported_fees_today: Decimal | None
    fees_today: Decimal | None
    unknown_fee_count_today: int | None
    unrealized_pnl: Decimal | None
    open_risk: Decimal | None
    buying_power: None = None

    @property
    def realized_pnl_today(self) -> Decimal | None:
        if self.realized_profit_today is None or self.realized_loss_today is None:
            return None
        return self.realized_profit_today - self.realized_loss_today

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def in_flight_count(self) -> int:
        return len(self.in_flight)


def build_snapshot(
    state: LedgerState, marks: dict[str, tuple[Decimal, datetime]],
    day: date, symbol: str | None,
) -> PortfolioSnapshot | None:
    positions = {p.symbol: p for p in state.positions if symbol is None or p.symbol == symbol}
    orders = {
        o.client_order_id: o for o in state.orders
        if o.status not in TERMINAL_STATUSES and (symbol is None or o.symbol == symbol)
    }
    rows = [r for r in state.daily if symbol is None or r.symbol == symbol]
    if symbol is not None and not positions and not orders and not rows:
        return None
    marks = {s: m for s, m in marks.items() if s in positions}
    exposures = []
    for p in positions.values():
        mark = marks.get(p.symbol)
        price = mark[0] if mark else None
        unrealized = None if price is None else (price - p.avg_price) * p.qty * (1 if p.side == "BUY" else -1)
        exposures.append(OpenExposure(p.symbol, p.side, p.qty, p.avg_price, p.stop, price, unrealized, False))
    for o in orders.values():
        # Exits never add exposure; partially filled entries contribute only
        # their remainder alongside the already-filled position.
        if o.position_effect == "open" and o.remaining_qty:
            mark = marks.get(o.symbol)
            price = mark[0] if mark else None
            unrealized = None if price is None or o.reference_price is None else (
                (price - o.reference_price) * o.remaining_qty * (1 if o.side == "BUY" else -1)
            )
            exposures.append(OpenExposure(o.symbol, o.side, o.remaining_qty, o.reference_price,
                                          o.stop, price, unrealized, True))
    unrealized_total = ZERO
    risk_total = ZERO
    for e in exposures:
        if e.unrealized_pnl is None:
            unrealized_total = None
        elif unrealized_total is not None:
            unrealized_total += e.unrealized_pnl
        if e.mark is None or e.stop is None or e.avg_entry_price is None:
            risk_total = None
        elif risk_total is not None:
            risk_total += abs(e.avg_entry_price - e.stop) * e.qty
    today = [r for r in rows if r.trading_day == day]
    profit = sum((r.profit for r in today), ZERO) if state.history_complete else None
    loss = sum((r.loss for r in today), ZERO) if state.history_complete else None
    reported = sum((r.reported_fees for r in today), ZERO) if state.history_complete else None
    unknown = sum(r.unknown_fee_count for r in today) if state.history_complete else None
    return PortfolioSnapshot(
        execution_mode=state.execution_mode, trading_day=day,
        as_of=max([state.as_of] + [ts for _, ts in marks.values()]),
        positions=positions, in_flight=orders, marks=marks, exposures=tuple(exposures),
        realized_profit_today=profit, realized_loss_today=loss,
        reported_fees_today=reported, fees_today=reported if unknown == 0 else None,
        unknown_fee_count_today=unknown, unrealized_pnl=unrealized_total, open_risk=risk_total,
    )
