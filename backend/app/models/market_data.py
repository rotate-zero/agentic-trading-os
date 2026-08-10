"""
Symbol + Candle — the only tables scaffolded in Phase 2. Everything else
in system-design.md §4.13 (trades, orders, positions, ai_decisions,
feature_snapshots, market_events, market_state_history, ...) belongs to
the phase that actually populates it (§4.8's table, roadmap Phases 3-6) —
adding empty tables now would just be schema guessing ahead of the modules
that define what they actually need to store.

Candle is partitioned by RANGE (candle_ts) — native Postgres declarative
partitioning, no extension — per confirmed decision #2 (plain PostgreSQL,
TimescaleDB deferred). See alembic/versions/0001_initial_symbols_and_candles.py
for the actual partition DDL; SQLAlchemy's ORM layer here just describes the
columns, since query code operates against `candles` as one logical table
regardless of how it's physically partitioned.

Simplification flagged, not hidden: system-design.md §4.13 describes
partitioning "by month, sub-partitioned by timeframe." This migration
implements the month partitioning only; `timeframe` is an indexed column
within each monthly partition rather than a second partition level.
Sub-partitioning is cheap to add later (another ALTER/partition-of-a-
partition) once partition sizes actually justify the extra complexity —
no need to guess that now.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    exchange: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    candles: Mapped[list["Candle"]] = relationship(back_populates="symbol")


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol_id", "timeframe", "candle_ts", name="uq_candle_symbol_tf_ts"),
        {"postgresql_partition_by": "RANGE (candle_ts)"},
    )

    # Partitioned tables require the partition key (candle_ts) in the PK.
    # Identity() here isn't decorative — it must match the migration's own
    # `sa.Identity()` (alembic/versions/0001_...py) exactly, or SQLAlchemy
    # warns that this composite PK column has no known default generator
    # and, on stricter configs, can refuse to proceed at all. This mismatch
    # was latent since Phase 2 (the model and its own migration disagreed)
    # and only surfaced now that CandleRecorder (confirmed decision #42) is
    # the first code ever to actually INSERT into this table.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    candle_ts: Mapped[datetime] = mapped_column(primary_key=True)

    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)  # "1m", "5m", "1d", ...
    open: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    symbol: Mapped[Symbol] = relationship(back_populates="candles")
