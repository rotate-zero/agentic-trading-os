"""
Payload schemas for execution-critical events — these ride the critical
dispatch lane (docs/decisions/confirmed-decisions.md #9). See
system-design.md §10.3 and trading-intelligence-architecture.md §12.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class GovernorDecision(BaseModel):
    """
    Widened schema per confirmed decision #6 — v1 rule logic only ever
    produces approved/rejected; the other branches exist so this isn't a
    breaking change later.
    """

    action: Literal["approved", "approved_reduced", "delayed", "watch_only", "rejected"]
    size_multiplier: float | None = None  # used only when action == "approved_reduced"
    delay_seconds: int | None = None  # used only when action == "delayed"
    reasons: list[str] = []


class OrderApproved(BaseModel):
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None


class PlanRejected(BaseModel):
    symbol: str
    reasons: list[str]


class OrderFilled(BaseModel):
    order_id: str
    side: Literal["BUY", "SELL"]
    qty: int
    fill_price: float
    fill_ts: datetime
