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

Two mutually exclusive row shapes, one table (M3, decision #91: "no
separate table" for `CrossSymbolState` — same reasoning `strategy_outcomes`
already applies to live/backtest rows sharing one schema distinguished by
a flag, rather than splitting into two tables). A per-symbol row (any
ordinary tracked ticker, including SPY/QQQ/IWM's own per-symbol scoring)
populates `trend_score`/`volatility_regime_score`/`volume_regime_score`/
`vwap_relationship_score` and leaves the 7 cross-symbol columns NULL. The
`__MARKET__` sentinel row (symbol_id resolving to that ticker in
`symbols`) does the reverse: populates the 7 cross-symbol columns and
leaves the 4 per-symbol score columns NULL. `acceleration_score` sits
outside this split — always nullable, independent of row shape, since
it's legitimately NULL for a per-symbol row's first-ever recompute too
(decision #93). `MarketStateEngine._persist`/`_persist_cross_symbol`
enforce this split with a write-time assertion (decision #89's
entry_qty==exit_qty precedent) rather than a DB CHECK constraint.
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

    # Per-symbol group — NULL only on the `__MARKET__` sentinel row (M3).
    trend_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    volatility_regime_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    volume_regime_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    vwap_relationship_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    # NULL for a symbol's first-ever recompute — no prior trend_score to
    # diff against yet (decision #93). Independent of the per-symbol/
    # cross-symbol split below; never populated on a sentinel row either way.
    acceleration_score: Mapped[float | None] = mapped_column(Numeric(6, 2))

    # Cross-symbol group (M3, decision #91's `CrossSymbolState`) — NULL on
    # every ordinary per-symbol row, populated only on the `__MARKET__`
    # sentinel row.
    spy_direction_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    qqq_direction_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    iwm_direction_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    trend_alignment_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    risk_on_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    qqq_leadership_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    iwm_confirmation_score: Mapped[float | None] = mapped_column(Numeric(6, 2))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
