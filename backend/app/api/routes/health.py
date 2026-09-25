from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.market_clock import get_market_clock
from app.event_bus.bus import get_event_bus

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    clock = get_market_clock()
    bus = get_event_bus()
    return {
        "status": "ok",
        "market_session": clock.current_session().value,
        "market_open": clock.is_market_open(),
        "event_bus_queue_depths": bus.queue_depths(),
    }


@router.get("/health/execution-startup")
async def execution_startup_status(request: Request) -> dict:
    """Read-only report of what happened the last time this process's
    lifespan ran the execution-pipeline startup sequence (main.py, §6.9 /
    decision #179's fail-closed contract) — NOT a live trading-readiness
    check. `status: "ready"` means the pipeline finished startup
    successfully; it is not proof that any particular opportunity will
    pass Governor rules (`governor/`), that Portfolio State will stay
    ready, or that open positions' exits are protected (`position_monitor/`
    is an observer, not a guarantee — see GET /intelligence/exit-intents).

    `status` is one of "ready", "reconciliation_blocked",
    "startup_failed", or "unavailable" — the last one covers both "this
    route was hit before startup finished or after shutdown" and "no
    lifespan is currently active" (e.g. calling this app directly without
    FastAPI's lifespan running, as a plain ASGI call would). `reason_code`
    is a fixed, safe constant (never exception text) and `discrepancy_count`
    is a plain integer count (never the discrepancy list itself) — neither
    field can carry exception detail, database credentials, or raw
    reconciliation contents.
    """
    status = getattr(request.app.state, "execution_startup_status", None)
    if status is None:
        return {"status": "unavailable", "reason_code": None, "discrepancy_count": None}
    return status
