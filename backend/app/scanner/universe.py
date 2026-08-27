"""
UniverseProvider — which symbols are even eligible to be scored.
docs/architecture/scanner-design.md §3.

Two implementations now:
- `StaticUniverseProvider` — the original, a plain in-memory list. Still
  used by `scripts/test_scanner_pipeline.py` and `tests/test_scanner.py`'s
  fixtures via `TEST_UNIVERSE` below — unchanged, not being removed.
- `DbUniverseProvider` — the real one, reading `scanner_universe_symbols`
  (migration 0004). This is what `GET /scanner/state` actually defaults
  to now. Seeded with `TEST_UNIVERSE`'s same 6 symbols on migration, so
  there's no dead-empty state on first deploy — still just a starting
  point, now editable via `add_symbol_to_universe`/
  `remove_symbol_from_universe` below instead of a hardcoded list.

Both satisfy the same `UniverseProvider` interface — swapping which one
`MarketActivityScanner` uses (once it exists, §5) needs zero changes to
that orchestrator, which was the entire point of this being an
interface from the start.
"""
from __future__ import annotations

import re
from typing import Callable, Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.market_data import Symbol
from app.models.scanner import ScannerUniverseSymbol


class UniverseProvider(Protocol):
    def get_core_universe(self) -> list[str]: ...


class StaticUniverseProvider:
    """The original implementation — takes whatever list it's given,
    doesn't know or care whether that's a real universe or a throwaway
    test set."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)

    def get_core_universe(self) -> list[str]:
        return list(self._symbols)


class DbUniverseProvider:
    """Reads `scanner_universe_symbols` — what `GET /scanner/state`
    actually uses by default now. `session_factory` matches
    `app.db.session.SessionLocal`'s own callable-that-returns-a-Session
    shape (candle_recorder.py/engine.py's `_get_or_create_symbol_id`
    already establish this as the codebase's sync-session convention;
    reused here, not reinvented)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_core_universe(self) -> list[str]:
        session = self._session_factory()
        try:
            rows = session.execute(
                select(Symbol.ticker)
                .join(ScannerUniverseSymbol, ScannerUniverseSymbol.symbol_id == Symbol.id)
                .order_by(ScannerUniverseSymbol.added_at)
            ).scalars().all()
            return list(rows)
        finally:
            session.close()


# Placeholder ONLY (see StaticUniverseProvider's docstring above) — six
# liquid, well-known names. Migration 0004 seeds scanner_universe_symbols
# with this exact list, so both providers start out identical; they can
# diverge from here once symbols are added/removed via the API below.
TEST_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "SPY"]


# --- Universe management (add/remove/list against the DB table) ---
#
# Deliberately format-only validation (see is_valid_ticker_format's own
# docstring) — NOT a confirmation the symbol actually trades anywhere or
# has live data flowing. Real existence verification would need a live
# call to Finnhub/Polygon/IBKR, which this deliberately does NOT do, same
# "don't build ahead of a demonstrated need" posture the rest of this
# system uses elsewhere. Worth revisiting if a malformed-but-technically-
# valid-looking symbol (e.g. a delisted ticker) turns out to be a real
# problem in practice — not assumed to be one preemptively.
_TICKER_FORMAT = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def is_valid_ticker_format(symbol: str) -> bool:
    """1-5 letters, optionally a share-class suffix like BRK.B. Rejects
    lowercase, numbers, stray punctuation, and empty input — does NOT
    confirm the symbol is real, tradable, or has any data behind it."""
    return bool(_TICKER_FORMAT.match(symbol))


def _get_or_create_symbol_id(session: Session, ticker: str) -> int:
    """Same get-or-create-by-ticker shape as
    candle_recorder.py/engine.py/level_interaction_engine.py's own
    private copies of this — not shared as a common utility in this
    codebase yet, so this follows that existing (if duplicated)
    convention rather than introducing a new shared one unprompted."""
    existing = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if existing is not None:
        return existing
    session.execute(pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"]))
    session.commit()
    return session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()


def list_universe_symbols(session_factory: Callable[[], Session]) -> list[dict]:
    """Returns [{"symbol": ..., "added_at": iso str}, ...] ordered by
    when each was added — oldest (the original seed) first."""
    session = session_factory()
    try:
        rows = session.execute(
            select(Symbol.ticker, ScannerUniverseSymbol.added_at)
            .join(ScannerUniverseSymbol, ScannerUniverseSymbol.symbol_id == Symbol.id)
            .order_by(ScannerUniverseSymbol.added_at)
        ).all()
        return [{"symbol": ticker, "added_at": added_at.isoformat()} for ticker, added_at in rows]
    finally:
        session.close()


def add_symbol_to_universe(session_factory: Callable[[], Session], symbol: str) -> str:
    """Idempotent — adding an already-present symbol is a no-op, not an
    error (POST /scanner/universe can be called safely more than once
    with the same symbol). Raises ValueError for a format-invalid ticker
    — see is_valid_ticker_format's own docstring for exactly what that
    does and doesn't check."""
    symbol = symbol.strip().upper()
    if not is_valid_ticker_format(symbol):
        raise ValueError(
            f"'{symbol}' doesn't look like a valid ticker (expected 1-5 letters, optionally a share-class suffix like BRK.B)"
        )

    session = session_factory()
    try:
        symbol_id = _get_or_create_symbol_id(session, symbol)
        session.execute(
            pg_insert(ScannerUniverseSymbol).values(symbol_id=symbol_id).on_conflict_do_nothing(index_elements=["symbol_id"])
        )
        session.commit()
        return symbol
    finally:
        session.close()


def remove_symbol_from_universe(session_factory: Callable[[], Session], symbol: str) -> bool:
    """Returns True if a row was actually removed, False if the symbol
    wasn't in the universe to begin with — either way not an error, so
    the route can treat this as "state is now as requested" rather than
    needing a 404 path."""
    symbol = symbol.strip().upper()
    session = session_factory()
    try:
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == symbol)).scalar_one_or_none()
        if symbol_id is None:
            return False
        result = session.execute(delete(ScannerUniverseSymbol).where(ScannerUniverseSymbol.symbol_id == symbol_id))
        session.commit()
        return result.rowcount > 0
    finally:
        session.close()
