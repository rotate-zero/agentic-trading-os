"""
Market State Engine's persisted table — confirmed decision #91 (shape)
and #93 (this build). See trading-intelligence-architecture.md §4.

Append-only, one row per recompute — NOT a restart checkpoint the way
`level_interaction_state` is (models/trading_intelligence.py). Decision
#91 explicitly revises the older "rebuild from persisted history on
restart" decision for Market State specifically: v1's rolling window is
short enough that a cold start on restart is an accepted simplification,
not something this table needs to help reconstruct. This table's role is
structurally identical to `level_interaction_events` — an analytical log
— not to `level_interaction_state`; see that module's own docstring for
the distinction this mirrors.

No range partitioning, same reasoning as `level_interaction_events`:
grows at debounced-recompute rate (floored to ~1/second per symbol, in
practice much less once the ceiling/floor settle into steady state),
nowhere near `candles`-table tick/candle volume.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketStateHistory(Base):
    __tablename__ = "market_state_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    candle_ts: Mapped[datetime] = mapped_column(nullable=False)

    trend_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    volatility_regime_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    volume_regime_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    vwap_relationship_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    # NULL for a symbol's first-ever recompute — no prior trend_score to
    # diff against yet (decision #93).
    acceleration_score: Mapped[float | None] = mapped_column(Numeric(6, 2))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
