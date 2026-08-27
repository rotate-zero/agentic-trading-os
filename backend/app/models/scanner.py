"""
Scanner's persisted universe — docs/architecture/scanner-design.md §3's
"a real scanner_universe config table is genuine follow-up work" now
built, replacing app/scanner/universe.py's TEST_UNIVERSE hardcoded list
as GET /scanner/state's default source (still just a placeholder set of
symbols underneath, initially — see migration 0004's seed data — now
editable via GET/POST/DELETE /scanner/universe instead of a hardcoded
list requiring a code change).

FK to `symbols.id` (the same table candles/daily_levels already key
off), not a raw ticker string column, so this universe automatically
stays consistent with however `symbols.ticker` gets normalized
elsewhere — one canonical symbol identity, not a second one invented
for this table.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScannerUniverseSymbol(Base):
    __tablename__ = "scanner_universe_symbols"
    __table_args__ = (UniqueConstraint("symbol_id", name="uq_scanner_universe_symbol_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
