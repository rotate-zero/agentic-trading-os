"""
FundamentalsRefreshJobs -- the only thing that ever writes to
symbol_fundamentals (FundamentalsProvider only reads it -- §5's explicit
constraint). Three independent, hand-rolled background loops (decision
#96 -- no new scheduling dependency, same "sleep until next occurrence"
pattern ContextEngine's own session-boundary loop already uses):

- weekly profile batch: industry, from /stock/profile2 (sector column
  stays permanently NULL -- see models/symbol_fundamentals.py's own
  docstring for why: Finnhub provides one classification field, not a
  sector-and-industry pair)
- daily market_cap refresh: marketCapitalization, same endpoint as
  above, separate write so the weekly batch's cadence doesn't gate
  market cap's daily one (§5: "market_cap on its own daily refresh since
  it moves with price rather than staying static")
- daily earnings+financials check: next_earnings_date from
  /calendar/earnings, PLUS an unconditional daily financials-reported
  check that only writes when a newer financials_period is actually
  derived (decision #94's validated TTM math, app/context_engine/
  fundamentals_derivation.py) -- simpler than gating the financials call
  on comparing next_earnings_date first, and at 6 symbols x 2 calls/day
  there's no rate-limit reason to bother with that gate. Retried daily
  until a new filing actually shows up; a day where nothing changed just
  bumps financials_updated_at without touching the TTM figures
  themselves (see models/symbol_fundamentals.py's _updated_at semantics).

Iterates the Scanner Universe (`scanner_universe_symbols`), read fresh on
every batch run (unlike ContextEngine's per-symbol loops, which snapshot
once at start() -- these jobs are cheap enough and infrequent enough that
re-reading the universe each run costs nothing and avoids that same
staleness gap here).

No rate-limit pacing/batching logic: Scanner Universe is 6 symbols today,
each job makes at most 1-2 calls per symbol, nowhere near either of
decision #94's two 60/min buckets (profile2+financials-reported share
one; calendar/earnings+company-news share the other -- this module's own
calendar/earnings + financials-reported calls land in DIFFERENT buckets
from each other, so they don't even compete with one another). Revisit
if the universe grows into the dozens.

**Field names not independently re-verified against live output** (same
caveat as fundamentals_derivation.py and providers/news.py carry): this
session has no live Finnhub key. `finnhubIndustry`, `marketCapitalization`
(profile2), and `date` (each /calendar/earnings event) are Finnhub's
documented field names, not confirmed by M0's own spike script, which
only dumped raw profile/earnings responses for a human to read rather
than parsing named fields out of them. Worth a real smoke test against a
live key before trusting in production.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import finnhub
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.context_engine.fundamentals_derivation import derive_ttm, most_recent_period_label
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.market_data import Symbol
from app.models.scanner import ScannerUniverseSymbol
from app.models.symbol_fundamentals import SymbolFundamentals

logger = logging.getLogger(__name__)

_WEEKLY_INTERVAL_SECONDS = 7 * 24 * 3600
_DAILY_INTERVAL_SECONDS = 24 * 3600
_EARNINGS_HORIZON_DAYS = 180  # matches check_finnhub_context_data.py's own generous horizon


class FundamentalsRefreshJobs:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_settings().finnhub_api_key
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if not self._api_key:
            logger.warning("FundamentalsRefreshJobs not started — no Finnhub API key configured")
            return
        self._tasks = [
            asyncio.create_task(self._loop(self._run_profile_batch, _WEEKLY_INTERVAL_SECONDS), name="fundamentals-weekly-profile"),
            asyncio.create_task(self._loop(self._run_market_cap_batch, _DAILY_INTERVAL_SECONDS), name="fundamentals-daily-market-cap"),
            asyncio.create_task(self._loop(self._run_earnings_financials_batch, _DAILY_INTERVAL_SECONDS), name="fundamentals-daily-earnings-financials"),
        ]
        logger.info("FundamentalsRefreshJobs started — 3 independent loops (weekly profile, daily market_cap, daily earnings+financials)")

    async def stop(self) -> None:
        """Plain cancel, no poison-pill drain — same reasoning as
        ContextEngine's per-symbol path (engine.py's own docstring):
        every write here is a partial UPSERT of freshly-fetched data, not
        a checkpoint anything depends on being crash-consistent. Worst
        case from a cancelled mid-flight batch is next run's UPSERT just
        overwrites the same fields again — no corruption, no orphaned
        write anyone waits on."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        logger.info("FundamentalsRefreshJobs stopped")

    async def _loop(self, run_batch, interval_seconds: int) -> None:
        await run_batch()
        while True:
            await asyncio.sleep(interval_seconds)
            await run_batch()

    # --- batch runners -------------------------------------------------------------

    async def _run_profile_batch(self) -> None:
        for symbol in await asyncio.to_thread(self._load_universe_symbols):
            await asyncio.to_thread(self._refresh_profile, symbol)

    async def _run_market_cap_batch(self) -> None:
        for symbol in await asyncio.to_thread(self._load_universe_symbols):
            await asyncio.to_thread(self._refresh_market_cap, symbol)

    async def _run_earnings_financials_batch(self) -> None:
        for symbol in await asyncio.to_thread(self._load_universe_symbols):
            await asyncio.to_thread(self._refresh_earnings, symbol)
            await asyncio.to_thread(self._refresh_financials, symbol)

    # --- per-symbol work (sync, run via to_thread) ----------------------------------

    def _client(self) -> finnhub.Client:
        return finnhub.Client(api_key=self._api_key)

    def _refresh_profile(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        try:
            profile = self._client().company_profile2(symbol=symbol) or {}
        except finnhub.exceptions.FinnhubAPIException:
            logger.exception("Profile refresh failed for %s", symbol)
            return
        industry = profile.get("finnhubIndustry")
        self._upsert(symbol, {"industry": industry, "profile_updated_at": now})

    def _refresh_market_cap(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        try:
            profile = self._client().company_profile2(symbol=symbol) or {}
        except finnhub.exceptions.FinnhubAPIException:
            logger.exception("Market cap refresh failed for %s", symbol)
            return
        market_cap = profile.get("marketCapitalization")
        self._upsert(symbol, {"market_cap": market_cap, "market_cap_updated_at": now})

    def _refresh_earnings(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        today = date.today()
        horizon = today + timedelta(days=_EARNINGS_HORIZON_DAYS)
        try:
            result = self._client().earnings_calendar(_from=today.isoformat(), to=horizon.isoformat(), symbol=symbol) or {}
        except finnhub.exceptions.FinnhubAPIException:
            logger.exception("Earnings calendar refresh failed for %s", symbol)
            return
        events = result.get("earningsCalendar", []) if isinstance(result, dict) else []
        dated = [e.get("date") for e in events if e.get("date")]
        next_date = min(dated) if dated else None
        self._upsert(symbol, {"next_earnings_date": next_date, "earnings_updated_at": now})

    def _refresh_financials(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        try:
            quarterly = (self._client().financials_reported(symbol=symbol, freq="quarterly") or {}).get("data", [])
            annual = (self._client().financials_reported(symbol=symbol, freq="annual") or {}).get("data", [])
        except finnhub.exceptions.FinnhubAPIException:
            logger.exception("Financials refresh failed for %s", symbol)
            return

        period = most_recent_period_label(quarterly)
        ttm = derive_ttm(quarterly, annual)

        # Always record that a check happened today, regardless of
        # outcome — _updated_at's job is "when did we last confirm this
        # data" (models/symbol_fundamentals.py's own docstring).
        values: dict = {"financials_updated_at": now}
        if period is not None and any(v is not None for v in ttm.values()):
            # Only overwrite the TTM figures/period when something was
            # actually derivable this run — a day where derive_ttm
            # couldn't confidently compute anything (see its own honest-
            # state posture) must not blank out yesterday's good numbers.
            values.update({"financials_period": period, **ttm})
        self._upsert(symbol, values)

    # --- persistence ------------------------------------------------------------------

    def _load_universe_symbols(self) -> list[str]:
        session = SessionLocal()
        try:
            rows = session.execute(
                select(Symbol.ticker).join(ScannerUniverseSymbol, ScannerUniverseSymbol.symbol_id == Symbol.id)
            ).scalars().all()
            return list(rows)
        finally:
            session.close()

    def _upsert(self, symbol: str, values: dict) -> None:
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            stmt = pg_insert(SymbolFundamentals).values(symbol_id=symbol_id, data_source="finnhub", **values)
            stmt = stmt.on_conflict_do_update(index_elements=["symbol_id"], set_=values)
            session.execute(stmt)
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as every other engine's persist path
            logger.exception("FundamentalsRefreshJobs failed to persist %s for %s", list(values.keys()), symbol)
            session.rollback()
        finally:
            session.close()

    def _get_or_create_symbol_id(self, session, ticker: str) -> int:
        existing = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if existing is not None:
            return existing
        stmt = pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"])
        session.execute(stmt)
        session.commit()
        return session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()


_fundamentals_refresh_jobs: FundamentalsRefreshJobs | None = None


def get_fundamentals_refresh_jobs() -> FundamentalsRefreshJobs:
    """Lazy singleton, same pattern as every other engine's getter."""
    global _fundamentals_refresh_jobs
    if _fundamentals_refresh_jobs is None:
        _fundamentals_refresh_jobs = FundamentalsRefreshJobs()
    return _fundamentals_refresh_jobs
