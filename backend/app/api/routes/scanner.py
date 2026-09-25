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
touching the persisted universe, same as before.

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
    list_universe_symbols,
    remove_symbol_from_universe,
)

router = APIRouter(prefix="/scanner", tags=["scanner"])


class AddSymbolRequest(BaseModel):
    symbol: str


@router.get("/state")
async def get_scanner_state(
    symbols: str | None = Query(
        None,
        description="Comma-separated symbols, e.g. 'AAPL,TSLA,NVDA'. Omit to use the persisted universe (GET /scanner/universe) — ad hoc only, doesn't change what's persisted.",
    ),
    top_n: int = Query(8, ge=1, le=100, description="How many top-ranked symbols to return. Default 8 matches LiveTickRelay.DEFAULT_MAX_ACTIVE_SYMBOLS."),
) -> dict[str, Any]:
    if symbols:
        universe = [s.strip().upper() for s in symbols.split(",")]
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
