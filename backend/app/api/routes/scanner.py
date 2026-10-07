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

GET /scanner/observation (task `scanner-observation-status`) is a separate,
read-only view of the optional scheduled observation worker's retained
snapshot. It reads only `app.state.scanner_observation_reader` through the
narrow `get_scanner_observation_reader` dependency below; nothing installs
that reader yet (lifespan wiring is a separately approved task), so the
production response is "unavailable". GET /scanner/state and universe CRUD
are unchanged and independent of it.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.scanner.runner import run_scan
from app.scanner.scanner import ObservationSnapshot
from app.scanner.universe import (
    TEST_UNIVERSE,
    DbUniverseProvider,
    add_symbol_to_universe,
    is_valid_ticker_format,
    list_universe_symbols,
    remove_symbol_from_universe,
)

logger = logging.getLogger(__name__)

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


class ScannerObservationReader(Protocol):
    """The only thing GET /scanner/observation may use from a worker: one
    synchronous, I/O-free snapshot read. No start/stop, no scan, no universe
    access -- a reader object cannot be used to change worker state through
    this route."""

    def get_snapshot(self) -> ObservationSnapshot: ...


def get_scanner_observation_reader(request: Request) -> ScannerObservationReader | None:
    """Same convention as `world_view_portfolio_reader` / `position_monitor`
    in the intelligence routes: an optional, lifespan-owned object on
    `app.state`, `None` when absent. This dependency never constructs,
    starts or looks up a worker itself."""
    return getattr(request.app.state, "scanner_observation_reader", None)


_MAX_OBSERVATION_ERROR_CHARS = 500


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)  # the worker's clock is UTC
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _finite_or_none(value: float) -> float | None:
    # Starlette refuses to encode NaN/Infinity; a status read must never 500
    # because of one odd feature value.
    return float(value) if math.isfinite(value) else None


def _latest_attempt_state(snapshot: ObservationSnapshot) -> str:
    """none | in_progress | succeeded | failed | interrupted.

    `failed` is the worker's own `last_error` (cleared when the next attempt
    starts or succeeds). `interrupted` is an attempt that began but neither
    published nor failed -- stop() invalidated it."""
    if snapshot.last_error is not None:
        return "failed"
    if snapshot.cycle_running:
        return "in_progress"
    if snapshot.last_attempt_at is None:
        return "none"
    if snapshot.last_success_at is not None and _as_utc(snapshot.last_success_at) >= _as_utc(snapshot.last_attempt_at):
        return "succeeded"
    return "interrupted"


def _project_observation(snapshot: ObservationSnapshot) -> dict[str, Any]:
    """Copy an immutable snapshot into plain JSON-ready containers. Every
    list/dict here is freshly built, so mutating the result cannot reach the
    worker's retained state (tuples and read-only mappings are never
    returned as-is)."""
    if snapshot.last_success_at is None:
        retained = "none"
    elif snapshot.results:
        retained = "populated"
    else:
        retained = "empty"
    error = snapshot.last_error
    if error is not None and len(error) > _MAX_OBSERVATION_ERROR_CHARS:
        error = error[:_MAX_OBSERVATION_ERROR_CHARS] + "…"
    return {
        "status": "available",
        "reason": None,
        "worker": {"running": snapshot.running, "cycle_running": snapshot.cycle_running},
        "observation": {
            "retained": retained,
            "latest_attempt": _latest_attempt_state(snapshot),
            "universe": list(snapshot.universe),
            "results": [
                {
                    "symbol": row.symbol,
                    "score": _finite_or_none(row.score),
                    "inputs_available": row.inputs_available,
                    "features": {key: _finite_or_none(value) for key, value in row.features.items()},
                }
                for row in snapshot.results
            ],
            "skipped": list(snapshot.skipped),
            "last_attempt_at": _utc_iso(snapshot.last_attempt_at),
            "last_success_at": _utc_iso(snapshot.last_success_at),
            "last_error": error,
        },
    }


@router.get("/observation")
async def get_scanner_observation(
    reader: ScannerObservationReader | None = Depends(get_scanner_observation_reader),
) -> dict[str, Any]:
    """Read-only view of the scheduled observation worker's retained snapshot.

    Separate from GET /scanner/state (which scans on demand). Exactly one
    `get_snapshot()` per request; it is synchronous and I/O-free, so it runs
    on the event loop. No scan, no universe or database read, no provider
    contact, no write and no worker state change happens here. With no
    reader installed the response is `status: "unavailable"`; a stopped
    worker whose reader is still installed returns its retained results.
    Worker availability says nothing about feed delivery or coverage."""
    if reader is None:
        return {
            "status": "unavailable",
            "reason": "No scheduled observation reader is installed in this backend process.",
            "worker": None,
            "observation": None,
        }
    return _project_observation(reader.get_snapshot())


@router.get("/universe")
async def get_scanner_universe() -> dict[str, Any]:
    symbols = await asyncio.to_thread(list_universe_symbols, SessionLocal)
    return {"symbols": symbols}


async def _refresh_context_after_universe_add(request: Request, symbol: str) -> None:
    """`context-universe-hot-add`: ask the running ContextEngine to start a
    per-symbol loop for what was just committed. A separate step from the
    universe commit by design -- any failure here is logged and swallowed,
    never turned into a failed addition (the symbol IS in the universe; a
    later addition or an explicit engine.refresh_symbol_loops() retries).

    The engine comes only from `app.state.context_engine`, which the
    lifespan sets while the engine is running and clears before stopping
    it. Absent (no lifespan, or shutting down) means skip -- this route
    never calls get_context_engine(), so it cannot create or start one."""
    engine = getattr(request.app.state, "context_engine", None)
    if engine is None:
        return
    try:
        await engine.refresh_symbol_loops()
    except Exception as exc:  # the engine already logged the traceback
        logger.warning(
            "Scanner universe addition of %s is committed but the ContextEngine refresh failed (%s) — "
            "its per-symbol context starts on the next refresh or restart",
            symbol, type(exc).__name__,
        )


@router.post("/universe")
async def add_scanner_universe_symbol(payload: AddSymbolRequest, request: Request) -> dict[str, Any]:
    try:
        added = await asyncio.to_thread(add_symbol_to_universe, SessionLocal, payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _refresh_context_after_universe_add(request, added)
    return {"symbol": added, "added": True}


@router.delete("/universe/{symbol}")
async def remove_scanner_universe_symbol(symbol: str) -> dict[str, Any]:
    removed = await asyncio.to_thread(remove_symbol_from_universe, SessionLocal, symbol)
    return {"symbol": symbol.strip().upper(), "removed": removed}
