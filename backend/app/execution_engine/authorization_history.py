"""Curated, read-only history of persisted Governor authorization attempts."""
from __future__ import annotations

from datetime import timezone
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.execution_ledger import Trade


_LIMITS = Trade.limits_snapshot

# One column list and one serializer define the authorization projection, so the
# recent-history route and the per-trade detail route can never drift apart.
AUTHORIZATION_COLUMNS = (
    Trade.trade_id, Trade.symbol, Trade.strategy_name, Trade.strategy_version,
    Trade.execution_mode, Trade.execution_venue, Trade.decision, Trade.reasons,
    Trade.created_at,
    _LIMITS["max_concurrent_positions"].astext.label("max_positions"),
    _LIMITS["fixed_notional_usd"].astext.label("fixed_notional"),
    _LIMITS["daily_loss_cap_usd"].astext.label("daily_loss_cap"),
)


def serialize_authorization(row: Any) -> dict[str, Any]:
    return {
        "trade_id": str(row.trade_id),
        "opportunity_id": str(row.trade_id) if row.decision == "approved" else None,
        "symbol": row.symbol,
        "strategy_name": row.strategy_name,
        "strategy_version": row.strategy_version,
        "execution_mode": row.execution_mode,
        "execution_venue": row.execution_venue,
        "decision": row.decision,
        "reasons": row.reasons,
        "created_at": row.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "limits_snapshot": {
            "max_concurrent_positions": int(row.max_positions) if row.max_positions is not None else None,
            "fixed_notional_usd": row.fixed_notional,
            "daily_loss_cap_usd": row.daily_loss_cap,
        },
    }


def read_execution_authorizations(
    *, symbol: str | None, decision: str | None, limit: int,
) -> list[dict[str, Any]]:
    """Read one worker-owned, repeatable-read, server-enforced read-only snapshot.

    Trade is the authorization ledger for both decisions. Rejected attempts may
    have an unsupported or absent requested mode, and never acquire an accepted
    opportunity ID or venue. The route validates arguments before calling here.
    """
    statement = (
        select(*AUTHORIZATION_COLUMNS)
        .order_by(Trade.created_at.desc(), Trade.trade_id.desc())
        .limit(limit)
    )
    if symbol is not None:
        statement = statement.where(Trade.symbol == symbol)
    if decision is not None:
        statement = statement.where(Trade.decision == decision)

    session = SessionLocal()
    try:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        rows = session.execute(statement).all()
        return [serialize_authorization(row) for row in rows]
    finally:
        session.rollback()
        session.close()
