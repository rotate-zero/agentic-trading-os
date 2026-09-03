"""
`symbol_fundamentals` — decision #90's shape (`trading-intelligence-
architecture.md` §5), built here per decision #96, refresh logic driven
by decision #94's empirical findings (`confirmed-decisions.md`).

One row per symbol, PK'd on `symbol_id` — decision #90's own pydantic
sketch in §5 shows `symbol: str # PK`, but that's treated here as
`FundamentalsProvider`'s OUTPUT shape (what `evaluate()` returns), not
literally this table's column definition. Every other table in this
project FKs to `symbols.id` specifically so ticker normalization has one
canonical source (`app/models/scanner.py`'s own docstring states this
principle directly) — `symbol_id` here keeps that consistent rather than
inventing a second symbol identity for this one table alone.

Four independent `_updated_at` timestamp columns, not one — because the
four refresh jobs that own this table's columns (`context_engine/
fundamentals_refresh.py`) run on four different cadences (weekly,
daily, daily, filing-triggered) and can each land before the others
have ever run. `NULL` in an `_updated_at` column means "this refresh job
has never completed for this symbol yet," distinct from a job having
run and confirmed the underlying value is genuinely absent (e.g. `sector`
staying `NULL` for an ETF after `profile_updated_at` IS set — honest
state over fabricated state, same principle decision #94 states
directly for the ETF case).

`sector` is a real column but is never written by anything in this
build — Finnhub's `/stock/profile2` provides exactly one classification
field (`finnhubIndustry`), not a separate sector+industry pair the way
decision #90's schema assumed. Mapping the one available field into both
columns would be fabricating a second dimension that doesn't exist in
the source data; left permanently `NULL` instead, flagged in decision
#96 rather than silently duplicated. `industry` receives the real value.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SymbolFundamentals(Base):
    __tablename__ = "symbol_fundamentals"

    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), primary_key=True)

    sector: Mapped[str | None] = mapped_column(String(100))  # always NULL in this build — see module docstring
    industry: Mapped[str | None] = mapped_column(String(100))
    profile_updated_at: Mapped[datetime | None]

    market_cap: Mapped[float | None] = mapped_column(Numeric(20, 2))
    market_cap_updated_at: Mapped[datetime | None]

    revenue_ttm: Mapped[float | None] = mapped_column(Numeric(20, 2))
    net_income_ttm: Mapped[float | None] = mapped_column(Numeric(20, 2))
    operating_cash_flow_ttm: Mapped[float | None] = mapped_column(Numeric(20, 2))
    financials_period: Mapped[str | None] = mapped_column(String(10))  # e.g. "2026-Q2"
    financials_updated_at: Mapped[datetime | None]

    next_earnings_date: Mapped[date | None] = mapped_column(Date)
    earnings_updated_at: Mapped[datetime | None]

    data_source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="finnhub")
