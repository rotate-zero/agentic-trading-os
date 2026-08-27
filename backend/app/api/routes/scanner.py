"""
GET /scanner/state — on-demand v1 scan run (docs/architecture/scanner-design.md
§5's "build now" resolution, §10). NOT the continuous MarketActivityScanner
described there — that needs ScanCadenceSchedule + LiveTickRelay wiring,
neither built yet. This route recomputes on every request, scoring
whatever Feature Engine has ALREADY computed for the requested universe
right now — cheap, in-memory-only (same posture GET /intelligence/state's
own docstring already states for its read), safe to poll from the
frontend on an interval.

Defaults to app/scanner/universe.py's TEST_UNIVERSE — a 6-symbol
placeholder, explicitly NOT Saqib's real Core-100 (§10 open question #1,
still his call). `?symbols=` lets a caller override it for testing
without redeploying.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.config import get_settings
from app.scanner.runner import run_scan
from app.scanner.universe import TEST_UNIVERSE

router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.get("/state")
async def get_scanner_state(
    symbols: str | None = Query(
        None,
        description="Comma-separated symbols, e.g. 'AAPL,TSLA,NVDA'. Omit for the placeholder TEST_UNIVERSE — NOT the real Core-100, which doesn't exist yet.",
    ),
) -> dict[str, Any]:
    universe = [s.strip().upper() for s in symbols.split(",")] if symbols else TEST_UNIVERSE
    settings = get_settings()

    results, skipped = run_scan(
        universe,
        weight_rvol=settings.scanner_weight_rvol,
        weight_gap=settings.scanner_weight_gap,
        weight_session_change=settings.scanner_weight_session_change,
    )

    return {
        "universe": universe,
        "results": [
            {
                "symbol": r.symbol,
                "score": r.score,
                "inputs_available": r.inputs_available,
                "features": r.features,
            }
            for r in results
        ],
        "skipped": skipped,  # cold start (no 1m FeatureSet yet) — not an error, see run_scan's own docstring
    }
