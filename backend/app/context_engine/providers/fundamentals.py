"""
FundamentalsProvider — decision #90's provider, decision #96's build.
Reads `symbol_fundamentals` only; never makes a live API call inside
evaluate() (the doc's own explicit constraint, §5) — writing that table
is entirely `fundamentals_refresh.py`'s job, on its own independent
cadences.

`sector` is always None here, matching the table itself (see
app/models/symbol_fundamentals.py's module docstring) — Finnhub's
`/stock/profile2` provides one classification field, not a
sector-and-industry pair, so there's nothing to read for `sector`
regardless of the DB row's state, not a bug in the read path.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.context_engine.provider import SymbolContextProvider
from app.db.session import SessionLocal
from app.models.market_data import Symbol
from app.models.symbol_fundamentals import SymbolFundamentals


class FundamentalsProvider(SymbolContextProvider):
    name = "fundamentals"

    async def evaluate(self, symbol: str) -> dict:
        return await asyncio.to_thread(self._read, symbol)

    def _read(self, symbol: str) -> dict:
        session = SessionLocal()
        try:
            row = session.execute(
                select(SymbolFundamentals)
                .join(Symbol, Symbol.id == SymbolFundamentals.symbol_id)
                .where(Symbol.ticker == symbol)
            ).scalar_one_or_none()
            if row is None:
                # No refresh job has ever run for this symbol yet — every
                # field genuinely unknown, not "checked and confirmed
                # empty" (that distinction matters; see the model's own
                # docstring on _updated_at columns).
                return {
                    "sector": None, "industry": None, "profile_updated_at": None,
                    "market_cap": None, "market_cap_updated_at": None,
                    "revenue_ttm": None, "net_income_ttm": None, "operating_cash_flow_ttm": None,
                    "financials_period": None, "financials_updated_at": None,
                    "next_earnings_date": None, "earnings_updated_at": None,
                }
            return {
                "sector": row.sector,
                "industry": row.industry,
                "profile_updated_at": row.profile_updated_at,
                "market_cap": float(row.market_cap) if row.market_cap is not None else None,
                "market_cap_updated_at": row.market_cap_updated_at,
                "revenue_ttm": float(row.revenue_ttm) if row.revenue_ttm is not None else None,
                "net_income_ttm": float(row.net_income_ttm) if row.net_income_ttm is not None else None,
                "operating_cash_flow_ttm": float(row.operating_cash_flow_ttm) if row.operating_cash_flow_ttm is not None else None,
                "financials_period": row.financials_period,
                "financials_updated_at": row.financials_updated_at,
                "next_earnings_date": row.next_earnings_date,
                "earnings_updated_at": row.earnings_updated_at,
            }
        finally:
            session.close()
