"""
Scanner routes — docs/architecture/scanner-design.md §5 (state), §3
(universe management, now built rather than just designed). Still v1:
GET /scanner/state recomputes on demand, no continuous
MarketActivityScanner/ScanCadenceSchedule (§10/§11 — not built).

Universe endpoints operate on `scanner_universe_symbols` (migration
0004) via app/scanner/universe.py's functions — GET /scanner/state reads
from the SAME table by default now (DbUniverseProvider), so adding or
removing a symbol here changes what the next scan actually scores.
`?symbols=` on GET /scanner/state still overrides it ad hoc without
touching the persisted universe, same as before — but now enforces the
exact same is_valid_ticker_format rule POST /scanner/universe already
enforces (§14 of scanner-design.md), rather than only stripping/
uppercasing: an empty override, an empty comma-separated entry, or a
format-invalid ticker all get HTTP 400 instead of silently reaching
run_scan() as a symbol that can never score. Valid entries are
deduplicated preserving first-seen order. The omitted-parameter path
(persisted-universe read + TEST_UNIVERSE fallback) is untouched.

Every app/scanner/universe.py call below is synchronous SQLAlchemy (the
DB engine is sync by design, per db/session.py) and is wrapped in
asyncio.to_thread at this route boundary — same convention
candle_store.py/market.py's GET /market/candles already establish for
this codebase's async routes. Each wrapped function already opens and
closes its own Session internally (session_factory in, closed in a
`finally`), so nothing about that shape changes here — only where it
runs. run_scan()/FeatureEngine.get_snapshot() are NOT wrapped: the
latter is documented as a pure in-memory dict read with no I/O
(feature_engine/engine.py's own get_snapshot docstring), so there is
nothing blocking to move.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.scanner.runner import run_scan
from app.scanner.universe import (
    TEST_UNIVERSE,
    DbUniverseProvider,
    add_symbol_to_universe,
    is_valid_ticker_format,
    list_universe_symbols,
    remove_symbol_from_universe,
)

router = APIRouter(prefix="/scanner", tags=["scanner"])


class AddSymbolRequest(BaseModel):
    symbol: str


def _parse_symbols_override(symbols: str) -> list[str]:
    """Normalizes an explicit `?symbols=` override on GET /scanner/state
    using the exact same format rule POST /scanner/universe already
    enforces (`is_valid_ticker_format` — see that function's own
    docstring for exactly what it does and doesn't check). Previously
    this path only stripped/uppercased each entry, so a malformed
    override (a stray comma, a lowercase-but-otherwise-fine ticker
    slipping through unchecked was fine, but a genuinely malformed one
    like "AAPL,,TSLA" or "123") silently became a universe entry that
    could never match a real FeatureSet rather than failing fast.

    Trims and uppercases each comma-separated entry, then:
    - raises 400 if the whole override is empty/whitespace-only
    - raises 400 on any empty entry between commas (a stray comma)
    - raises 400 on the first entry that fails is_valid_ticker_format
    - deduplicates valid entries, preserving first-seen order

    Does not touch scoring, ranking, top_n, or universe CRUD, and adds
    no new size limit on the override — an unusually long but otherwise
    valid list is not rejected here."""
    if not symbols.strip():
        raise HTTPException(status_code=400, detail="symbols override must not be empty")

    seen: set[str] = set()
    normalized: list[str] = []
    for raw in symbols.split(","):
        item = raw.strip().upper()
        if not item:
            raise HTTPException(
                status_code=400,
                detail="symbols override contains an empty entry (check for a stray comma)",
            )
        if not is_valid_ticker_format(item):
            raise HTTPException(
                status_code=400,
                detail=f"'{item}' doesn't look like a valid ticker (expected 1-5 letters, optionally a share-class suffix like BRK.B)",
            )
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


@router.get("/state")
async def get_scanner_state(
    symbols: str | None = Query(
        None,
        description="Comma-separated symbols, e.g. 'AAPL,TSLA,NVDA'. Omit to use the persisted universe (GET /scanner/universe) — ad hoc only, doesn't change what's persisted. Each entry must match the same ticker-format rule Scanner universe management enforces (1-5 letters, optional share-class suffix like BRK.B); invalid or empty entries return 400. Duplicates are dropped, first occurrence wins.",
    ),
    top_n: int = Query(8, ge=1, le=100, description="How many top-ranked symbols to return. Default 8 matches LiveTickRelay.DEFAULT_MAX_ACTIVE_SYMBOLS."),
) -> dict[str, Any]:
    if symbols is not None:
        universe = _parse_symbols_override(symbols)
    else:
        provider = DbUniverseProvider(SessionLocal)
        universe = await asyncio.to_thread(provider.get_core_universe)
        if not universe:
            # Empty persisted universe (migration not yet run, or every
            # symbol removed) — fall back rather than silently return
            # nothing to scan.
            universe = TEST_UNIVERSE

    settings = get_settings()
    results, skipped = run_scan(
        universe,
        weight_rvol=settings.scanner_weight_rvol,
        weight_gap=settings.scanner_weight_gap,
        weight_session_change=settings.scanner_weight_session_change,
        weight_premarket_volume_ratio=settings.scanner_weight_premarket_volume_ratio,
    )

    return {
        "universe": universe,
        "results": [
            {"symbol": r.symbol, "score": r.score, "inputs_available": r.inputs_available, "features": r.features}
            for r in results[:top_n]
        ],
        "total_scored": len(results),  # how many of `universe` actually had data, before the top_n cut
        "skipped": skipped,  # cold start (no 1m FeatureSet yet) — not an error, see run_scan's own docstring
    }


@router.get("/universe")
async def get_scanner_universe() -> dict[str, Any]:
    symbols = await asyncio.to_thread(list_universe_symbols, SessionLocal)
    return {"symbols": symbols}


@router.post("/universe")
async def add_scanner_universe_symbol(payload: AddSymbolRequest) -> dict[str, Any]:
    try:
        added = await asyncio.to_thread(add_symbol_to_universe, SessionLocal, payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"symbol": added, "added": True}


@router.delete("/universe/{symbol}")
async def remove_scanner_universe_symbol(symbol: str) -> dict[str, Any]:
    removed = await asyncio.to_thread(remove_symbol_from_universe, SessionLocal, symbol)
    return {"symbol": symbol.strip().upper(), "removed": removed}
