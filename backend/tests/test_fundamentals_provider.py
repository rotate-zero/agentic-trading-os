"""
FundamentalsProvider tests — reads symbol_fundamentals only, never calls
Finnhub. DB-backed, skipped as a whole without Postgres, same posture as
test_market_state_engine.py.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.context_engine.providers.fundamentals import FundamentalsProvider
from app.db.session import SessionLocal
from app.models.market_data import Symbol
from app.models.symbol_fundamentals import SymbolFundamentals


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM symbol_fundamentals WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


async def test_evaluate_returns_all_none_when_no_row_exists_yet():
    ticker = "TESTFUND1"
    _clean_test_symbol(ticker)
    try:
        provider = FundamentalsProvider()
        result = await provider.evaluate(ticker)
        assert result["sector"] is None
        assert result["industry"] is None
        assert result["revenue_ttm"] is None
        assert result["next_earnings_date"] is None
    finally:
        _clean_test_symbol(ticker)


async def test_evaluate_reads_back_a_populated_row():
    ticker = "TESTFUND2"
    _clean_test_symbol(ticker)
    session = SessionLocal()
    try:
        session.execute(pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"]))
        session.commit()
        symbol_id = session.execute(text("SELECT id FROM symbols WHERE ticker = :t"), {"t": ticker}).scalar_one()

        now = datetime.now(timezone.utc)
        session.add(SymbolFundamentals(
            symbol_id=symbol_id,
            sector=None,
            industry="Technology",
            profile_updated_at=now,
            market_cap=1_500_000.0,
            market_cap_updated_at=now,
            revenue_ttm=100.5,
            net_income_ttm=20.1,
            operating_cash_flow_ttm=30.2,
            financials_period="2026-Q2",
            financials_updated_at=now,
            next_earnings_date=date(2026, 10, 15),
            earnings_updated_at=now,
        ))
        session.commit()
    finally:
        session.close()

    try:
        provider = FundamentalsProvider()
        result = await provider.evaluate(ticker)
        assert result["sector"] is None  # always None — see model's own docstring
        assert result["industry"] == "Technology"
        assert result["market_cap"] == 1_500_000.0
        assert result["revenue_ttm"] == 100.5
        assert result["financials_period"] == "2026-Q2"
        assert result["next_earnings_date"] == date(2026, 10, 15)
    finally:
        _clean_test_symbol(ticker)
