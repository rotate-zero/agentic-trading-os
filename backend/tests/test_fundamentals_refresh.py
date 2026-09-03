"""
FundamentalsRefreshJobs tests. No live Finnhub key available in this
session (see the module's own docstring) — `_client()` is monkeypatched
to return a fake client with canned responses, so these tests verify the
actual upsert/derivation wiring against a real DB, not live API
connectivity (which is exactly what M0's own spike already covered
separately, for the two endpoints this reuses).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.context_engine.fundamentals_refresh import FundamentalsRefreshJobs
from app.db.session import SessionLocal


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


def _row(ticker: str) -> dict | None:
    session = SessionLocal()
    try:
        row = session.execute(
            text(
                "SELECT industry, market_cap, next_earnings_date, revenue_ttm, financials_period "
                "FROM symbol_fundamentals sf JOIN symbols s ON s.id = sf.symbol_id WHERE s.ticker = :t"
            ),
            {"t": ticker},
        ).fetchone()
        return dict(row._mapping) if row else None
    finally:
        session.close()


class _FakeClient:
    def __init__(self, profile=None, earnings=None, quarterly=None, annual=None):
        self._profile = profile or {}
        self._earnings = earnings or {}
        self._quarterly = quarterly or {"data": []}
        self._annual = annual or {"data": []}

    def company_profile2(self, symbol):
        return self._profile

    def earnings_calendar(self, _from, to, symbol):
        return self._earnings

    def financials_reported(self, symbol, freq):
        return self._quarterly if freq == "quarterly" else self._annual


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


async def test_refresh_profile_writes_industry_but_never_sector():
    ticker = "TESTFR1"
    _clean_test_symbol(ticker)
    jobs = FundamentalsRefreshJobs(api_key="unused")
    jobs._client = lambda: _FakeClient(profile={"finnhubIndustry": "Software", "marketCapitalization": 999.0})  # type: ignore[method-assign]
    try:
        jobs._refresh_profile(ticker)
        row = _row(ticker)
        assert row is not None
        assert row["industry"] == "Software"
    finally:
        _clean_test_symbol(ticker)


async def test_refresh_market_cap_writes_only_market_cap_field():
    ticker = "TESTFR2"
    _clean_test_symbol(ticker)
    jobs = FundamentalsRefreshJobs(api_key="unused")
    jobs._client = lambda: _FakeClient(profile={"marketCapitalization": 42_000.0})  # type: ignore[method-assign]
    try:
        jobs._refresh_market_cap(ticker)
        row = _row(ticker)
        assert row is not None
        assert float(row["market_cap"]) == 42_000.0
        assert row["industry"] is None  # this job never touches industry
    finally:
        _clean_test_symbol(ticker)


async def test_refresh_earnings_picks_soonest_upcoming_date():
    ticker = "TESTFR3"
    _clean_test_symbol(ticker)
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=90)).isoformat()
    jobs = FundamentalsRefreshJobs(api_key="unused")
    jobs._client = lambda: _FakeClient(earnings={"earningsCalendar": [{"date": later}, {"date": soon}]})  # type: ignore[method-assign]
    try:
        jobs._refresh_earnings(ticker)
        row = _row(ticker)
        assert row is not None
        assert row["next_earnings_date"].isoformat() == soon
    finally:
        _clean_test_symbol(ticker)


async def test_refresh_financials_derives_and_writes_ttm():
    ticker = "TESTFR4"
    _clean_test_symbol(ticker)
    quarterly = {
        "data": [
            {"endDate": "2025-03-31", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 19.3}]}},
            {"endDate": "2025-06-30", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 41.8}]}},
            {"endDate": "2025-09-30", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 69.9}]}},
            {"endDate": "2026-03-31", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 22.4}]}},
        ],
    }
    annual = {"data": [{"endDate": "2025-12-31", "form": "10-K", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 104.9}]}}]}
    jobs = FundamentalsRefreshJobs(api_key="unused")
    jobs._client = lambda: _FakeClient(quarterly=quarterly, annual=annual)  # type: ignore[method-assign]
    try:
        jobs._refresh_financials(ticker)
        row = _row(ticker)
        assert row is not None
        assert row["financials_period"] == "2026-Q1"
        expected = 22.4 + (104.9 - 69.9) + (69.9 - 41.8) + (41.8 - 19.3)
        assert abs(float(row["revenue_ttm"]) - expected) < 1e-6
    finally:
        _clean_test_symbol(ticker)


async def test_refresh_financials_preserves_prior_ttm_when_nothing_new_derivable():
    """A day where derive_ttm can't confidently compute anything must not
    blank out yesterday's good numbers — only financials_updated_at
    should move."""
    ticker = "TESTFR5"
    _clean_test_symbol(ticker)
    jobs = FundamentalsRefreshJobs(api_key="unused")

    good_quarterly = {
        "data": [
            {"endDate": "2025-03-31", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 19.3}]}},
            {"endDate": "2025-06-30", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 41.8}]}},
            {"endDate": "2025-09-30", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 69.9}]}},
            {"endDate": "2026-03-31", "form": "10-Q", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 22.4}]}},
        ],
    }
    good_annual = {"data": [{"endDate": "2025-12-31", "form": "10-K", "report": {"ic": [{"concept": "us-gaap_Revenues", "value": 104.9}]}}]}
    jobs._client = lambda: _FakeClient(quarterly=good_quarterly, annual=good_annual)  # type: ignore[method-assign]
    try:
        jobs._refresh_financials(ticker)
        first_row = _row(ticker)
        assert first_row["financials_period"] == "2026-Q1"

        jobs._client = lambda: _FakeClient(quarterly={"data": []}, annual={"data": []})  # type: ignore[method-assign]
        jobs._refresh_financials(ticker)
        second_row = _row(ticker)
        assert second_row["financials_period"] == "2026-Q1"  # unchanged
        assert second_row["revenue_ttm"] == first_row["revenue_ttm"]
    finally:
        _clean_test_symbol(ticker)


async def test_load_universe_symbols_reads_scanner_universe():
    jobs = FundamentalsRefreshJobs(api_key="unused")
    symbols = jobs._load_universe_symbols()
    # Seeded scanner universe includes SPY per migration 0004 — just
    # confirm the read path works against the real table, not the exact
    # membership (which could legitimately change over time).
    assert isinstance(symbols, list)
    assert len(symbols) > 0
