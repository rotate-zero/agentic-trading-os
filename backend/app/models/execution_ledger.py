"""
The execution ledger — `trades`, `orders`, `fills`, `positions`,
`portfolio_state_cursor` (design doc §6.8, decision #172).
**These tables are authoritative (I12)** — Portfolio State
(`app/portfolio_state/`) is a cache over them, never the record.

This module builds the SCHEMA the design doc's persistence sketch
describes; it does not populate `trades`/`orders` (that is the
authorizer stub's and Execution Engine's job — `governor/` and
`execution_engine/`, both sibling-task territory, decision
execution-authorizer-and-engine) beyond what this delivery's own tests
need to exercise the ledger and `Portfolio State` in isolation.

**Column types follow this file's own established convention**
(`models/trading_intelligence.py`'s module docstring): native
PostgreSQL `UUID` for identity that must be known to the application
before the row exists (`trade_id`, `position_id` — Python-side
`default=uuid.uuid4`, not `server_default`, precisely because
`client_order_id` values like `"<trade_id>:entry"` must be
constructible *before* the `trades` row is inserted); `BigInteger` +
`Identity()` elsewhere, including `fills.ledger_seq` itself, which
doubles as both this table's primary key and the strictly-monotonic
sequence Portfolio State's replay cursor advances against — a second,
separate "monotonic counter" column would just be tracking the same
fact twice. `JSONB` for genuinely dict-shaped fields, never for
homogeneous lists (`ARRAY` for those, matching the existing
`symbol_universe` precedent).

**The `execution_mode`/`execution_venue` pairing invariant (EX-2)**
is enforced by `CHECK ((execution_venue = 'simulated') = (execution_mode
IN ('backtest', 'simulated')))` on every table that carries both
columns (`trades`, `orders`, `positions`) — the design doc states this
check explicitly only for `strategy_outcomes` (§6.8), but the same
population-safety reasoning (AC #18: "a simulated-venue row cannot be
inserted as paper or live and vice versa") applies anywhere the two
columns co-occur, so it is repeated here rather than left as a gap
only `strategy_outcomes` happens to close. `fills` does not carry
`execution_mode` at all (only `execution_venue` — the design doc's own
column list for it), since a fill's mode is derivable via its
`orders` row.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Shared vocabulary — kept as plain string CHECK constraints (matching
# this codebase's existing convention of String(N) + CHECK rather than
# a native Postgres ENUM type anywhere in this file's neighbors) rather
# than a DB enum, so adding a value later is a CHECK-constraint
# migration, not a type migration.
_MODE_VENUE_PAIRING = "(execution_venue = 'simulated') = (execution_mode IN ('backtest', 'simulated'))"

ORDER_STATUSES = (
    "approved",
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "unknown",
    "expired",
)


class Trade(Base):
    """One row per authorization attempt, approved or rejected (§6.8).
    `trade_id` doubles as `opportunity_id` per the design doc; it is a
    plain UUID with no FK, matching `trading_intelligence.py`'s own
    "referenced not duplicated" convention — no `opportunities` table
    exists anywhere in this codebase (confirmed by grep) to enforce it
    against."""

    __tablename__ = "trades"

    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Rejected attempts retain even an unknown requested mode, with no
    # invented venue. Approved rows are constrained to complete valid labels.
    execution_mode: Mapped[str | None] = mapped_column(String(), nullable=True)
    execution_venue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")  # auto | manual (EX-13: auto only, v1)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY | SELL (EX-14)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)

    # Groups structural_*/final_*/confidence/evidence together (§6.8's
    # "thesis (structural_*, final_*, confidence, evidence)") — one
    # opaque blob, matching how the design doc itself talks about the
    # thesis as a unit rather than as independently-queried columns.
    thesis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # approved | rejected
    reasons: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    limits_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision_record: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    # open|closing|closed for an approved trade; NULL for a rejected one.
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    entry_market_state: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    entry_context: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    entry_snapshot_captured_at: Mapped[datetime | None] = mapped_column(nullable=True)
    entry_snapshot_missing_reasons: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_outcomes.outcome_id"), nullable=True
    )
    outcome_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pending | recorded | pending_retry

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_trades_decision"),
        CheckConstraint("direction IN ('BUY', 'SELL')", name="ck_trades_direction"),
        CheckConstraint(
            "status IS NULL OR status IN ('open', 'closing', 'closed')", name="ck_trades_status"
        ),
        CheckConstraint(_MODE_VENUE_PAIRING, name="ck_trades_mode_venue_pairing"),
        CheckConstraint(
            "decision = 'rejected' OR (execution_mode IS NOT NULL AND execution_venue IS NOT NULL "
            "AND execution_mode IN ('backtest', 'simulated', 'paper', 'live'))",
            name="ck_trades_approved_execution_labels",
        ),
    )


class TradeReservation(Base):
    """Durable approved entry exposure before Execution inserts its order.

    Retained as immutable approval terms after order insertion; the order's
    status and applied fills then determine the remaining exposure.
    """

    __tablename__ = "trade_reservations"

    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.trade_id"), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_trade_reservations_qty"),
        CheckConstraint("reference_price > 0 AND reference_price NOT IN ('NaN', 'Infinity', '-Infinity')",
                        name="ck_trade_reservations_price"),
        CheckConstraint("client_order_id = trade_id::text || chr(58) || 'entry'", name="ck_trade_reservations_client_id"),
    )


class Order(Base):
    """The order ledger and state machine (§6.8). `client_order_id` is
    the idempotency key (I10, I11) — **UNIQUE**, enforced at the
    database, not merely by application-level caution (AC #7's
    "ledger half")."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.trade_id"), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(32), nullable=False)
    venue_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL
    position_effect: Mapped[str] = mapped_column(String(8), nullable=False)  # open | close
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False, default="market")  # market | limit
    limit_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # stop | target | eod_flatten
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_side"),
        CheckConstraint("position_effect IN ('open', 'close')", name="ck_orders_position_effect"),
        CheckConstraint("order_type IN ('market', 'limit')", name="ck_orders_order_type"),
        CheckConstraint(f"status IN {ORDER_STATUSES!r}", name="ck_orders_status"),
        CheckConstraint(_MODE_VENUE_PAIRING, name="ck_orders_mode_venue_pairing"),
    )


class Fill(Base):
    """Every fill, deduplicated, in ledger order (§6.8). `ledger_seq`
    is BOTH the primary key and the strictly-monotonic sequence
    Portfolio State's replay cursor advances against — a single
    globally-ordered autoincrement column, not a separate counter
    layered on top of a surrogate id."""

    __tablename__ = "fills"

    ledger_seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True, cache=1), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("orders.client_order_id"), nullable=False
    )
    execution_venue: Mapped[str] = mapped_column(String(32), nullable=False)
    venue_fill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    venue_ts: Mapped[datetime] = mapped_column(nullable=False)
    commission: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)  # I3 — never fabricated
    anomaly: Mapped[str | None] = mapped_column(String(32), nullable=True)  # overfill | unmatched_order

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("execution_venue", "venue_fill_id", name="uq_fills_venue_fill_id"),
        CheckConstraint(
            "anomaly IS NULL OR anomaly IN ('overfill', 'unmatched_order')", name="ck_fills_anomaly"
        ),
    )


class Position(Base):
    """Position accounting — owner: Portfolio State (§6.8) — a
    deterministic function of `fills`. `position_id` is a Python-side
    UUID (not server-generated) for the same reason as `trade_id`:
    Portfolio State constructs the in-memory position object first and
    persists it, rather than round-tripping to read back a
    server-generated id before it can reference the position anywhere
    else (e.g. a future `PositionClosed` payload)."""

    __tablename__ = "positions"

    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.trade_id"), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    stop: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    target: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open | closing | closed
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    exit_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
        CheckConstraint("status IN ('open', 'closing', 'closed')", name="ck_positions_status"),
        CheckConstraint(_MODE_VENUE_PAIRING, name="ck_positions_mode_venue_pairing"),
    )


class PortfolioStateCursor(Base):
    """Where Portfolio State's replay resumes (§6.8) — one row per
    `execution_mode`. `rebuild_from_ledger()` applies every fill with
    `ledger_seq > last_applied_ledger_seq`, in order, then advances
    this row in the SAME transaction as the position upsert it
    covers (§6.5's "ONE TRANSACTION")."""

    __tablename__ = "portfolio_state_cursor"

    execution_mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_applied_ledger_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class PositionFillReceipt(Base):
    """Immutable replay inputs and explicit fill-to-position attribution.

    Decimal inputs in the JSON object are strings, preserving accounting
    precision independently of the six-place position projection.
    """

    __tablename__ = "position_fill_receipts"

    ledger_seq: Mapped[int] = mapped_column(BigInteger, ForeignKey("fills.ledger_seq"), primary_key=True)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    execution_venue: Mapped[str] = mapped_column(String(32), nullable=False)
    venue_fill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("positions.position_id"), nullable=False)
    fill_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trading_day: Mapped[date] = mapped_column(Date, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)

    __table_args__ = (
        UniqueConstraint("execution_venue", "venue_fill_id", name="uq_position_receipts_fill_key"),
        CheckConstraint(_MODE_VENUE_PAIRING, name="ck_position_receipts_mode_venue"),
    )
