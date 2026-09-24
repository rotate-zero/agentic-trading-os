"""Persistence seam for decision #173 (portfolio-state-engine). No ORM or sibling imports.

postgres.py implements PositionLedgerPort with owned PostgreSQL transactions.
The old Session API is retained separately for reconciliation compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from .accounting import LedgerFill, PositionState, ZERO

TERMINAL_STATUSES = frozenset({"filled", "rejected", "cancelled", "expired"})
ORDER_STATUSES = TERMINAL_STATUSES | {"approved", "submitted", "partially_filled", "unknown"}


@dataclass(frozen=True)
class InFlightOrder:
    client_order_id: str
    symbol: str
    side: str
    qty: int  # original ordered quantity; remaining_qty is exposure
    position_effect: str
    execution_mode: str
    status: str = "approved"
    filled_qty: int = 0
    reference_price: Decimal | None = None
    stop: Decimal | None = None

    @property
    def remaining_qty(self) -> int:
        return max(0, self.qty - self.filled_qty)


@dataclass(frozen=True)
class RealizedFill:
    """One durable attribution, including entry fees with zero realized P&L."""

    execution_mode: str
    execution_venue: str
    venue_fill_id: str
    ledger_seq: int
    position_id: UUID
    symbol: str
    trading_day: date
    fill_ts: datetime
    gross_pnl: Decimal
    commission: Decimal | None


@dataclass(frozen=True)
class DailyAmounts:
    symbol: str
    trading_day: date
    profit: Decimal = ZERO
    loss: Decimal = ZERO  # nonnegative magnitude
    reported_fees: Decimal = ZERO  # may include an explicitly reported rebate
    unknown_fee_count: int = 0

    @property
    def fees(self) -> Decimal | None:
        return None if self.unknown_fee_count else self.reported_fees


@dataclass(frozen=True)
class LedgerState:
    execution_mode: str
    cursor: int
    as_of: datetime
    positions: tuple[PositionState, ...] = ()  # open/closing only
    orders: tuple[InFlightOrder, ...] = ()  # all non-terminal orders/reservations
    daily: tuple[DailyAmounts, ...] = ()  # per-symbol/day, derived from individual fills
    history_complete: bool = True
    # False means absence of a daily bucket is UNKNOWN, never a zero.
    # Positions/orders must always be complete for this mode; otherwise load raises.
    problems: tuple[str, ...] = ()  # unresolved ledger anomalies block usable reads


@dataclass(frozen=True)
class FillApplication:
    fill: LedgerFill
    position: PositionState
    realized: RealizedFill
    expected_cursor: int


@dataclass(frozen=True)
class CommitResult:
    applied: bool
    state: LedgerState


class PositionLedgerError(Exception):
    """Persistence, conflict, or uncertain commit: stop replay and reload before retry."""


class PositionLedgerPort(Protocol):
    def load_state(self, execution_mode: str) -> LedgerState:
        """One consistent committed checkpoint. Restore position IDs/costs/lifetime
        totals, cursor, non-terminal orders (including approved reservations),
        and per-fill-derived daily amounts for ALL holding dates. Never infer
        a fresh zero balance from an absent/uninitialized/corrupt checkpoint.
        Marks are intentionally absent: a restart has unknown marks.
        """
        ...

    def pending_fills(self, execution_mode: str, after_cursor: int) -> tuple[LedgerFill, ...]:
        """Return a complete, strictly ordered SAFE committed prefix after cursor.

        Read fill identity from fills and mode/effect/side/trade/symbol from the
        committed order, never infer them from OrderFilled or process config.
        A PostgreSQL Identity is allocation order, NOT commit order: the adapter
        must serialize writers or establish a safe watermark so a lower sequence
        cannot commit later behind this cursor. Gaps due to other modes/aborts
        are legal. No uncommitted, anomalous, or unresolvable fill may be skipped
        silently. Such a gap raises and blocks new usable snapshots.
        """
        ...

    def get_order(self, client_order_id: str) -> InFlightOrder | None:
        """Authoritative order/reservation, including terminal states. None means
        unresolved metadata, NOT a terminal order. An approval can precede the
        order insert; keep its snapshot unavailable until this can resolve it.
        """
        ...

    def commit_fill(self, application: FillApplication) -> CommitResult:
        """Atomically dedupe (execution_venue, venue_fill_id), compare-and-set
        expected_cursor, persist position/closure and RealizedFill attribution,
        and advance the mode's cursor. Return ONLY after durable commit.

        Conflicting reuse of a key is an error. Exact duplicate: applied=False
        and current committed state, even on restart; no closure publication.
        Concurrent writers/conflicting cursor: raise PositionLedgerError.
        Stored attribution (or immutable replay inputs) must reproduce separate
        profit/loss/fees for each fill day, not the position's closing day.
        A new position ID is committed on its first open and retained forever;
        a later opening after closure gets a new ID, even for the same symbol.
        Orders remain execution-owned; returned progress must agree with the
        applied fills. A failed/uncertain commit must never report applied=True.
        """
        ...
