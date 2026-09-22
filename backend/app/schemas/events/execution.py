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
    # Added by decision #171 (EX-14). `order_id`
    # is documented (I10, decision #170) as *being* the client-order ID —
    # deterministic, "<trade_id>:entry" for an entry leg. This task's scope
    # is entry-only, so every instance this codebase currently constructs
    # has position_effect == "open"; "close" is reserved for the (not yet
    # built) exit path. §6.3 also lists execution_mode/opportunity_id/origin
    # as eventual additions here — deliberately NOT added in this delivery
    # (out of this task's exhaustive scope item 3); Execution Engine derives
    # trade_id from order_id instead (see execution_engine/ports.py).
    position_effect: Literal["open", "close"]


class PlanRejected(BaseModel):
    symbol: str
    reasons: list[str]


class TradePlanned(BaseModel):
    """
    New in decision #171 (R2): system-design.md
    §10.3's TradePlanned prose and trading-intelligence-architecture.md
    §18.3's TradePlan prose disagreed on the field set. Reconciled per this
    task's own instruction — trading-intelligence-architecture.md's
    `TradePlan` (the more recent document) is the base, adapted two ways,
    both flagged as judgment calls, overridable:

    - `symbol` is dropped from the payload. `TradePlan`'s own sketch
      includes it, but every other payload in this file (and
      OpportunityCreated, FeaturesUpdated, MarketStateChanged, ...) keeps
      symbol on the EventEnvelope only, never duplicated onto the payload
      — kept consistent with that established convention rather than
      `TradePlan`'s literal shape.
    - `direction` stays the planning-layer `long`/`short` vocabulary
      (matching `TradePlan` exactly), distinct from `OrderApproved.side`'s
      order-layer `BUY`/`SELL` — the authorizer stub translates one to the
      other when it mints `OrderApproved` (EX-14 option (a) in the design
      doc: the two vocabularies are allowed to differ by layer).

    This task's authorizer stub always publishes `origin="auto"` and
    `corroboration=[]` (Opportunity Engine ranking / D4 and the manual
    Trade Planning path, §18, are both out of scope here) and leaves
    `max_hold_seconds`/`scaling_plan`/`trailing_stop_rule` at their `None`
    defaults (scaling/trailing-stop management is not built this slice).
    """

    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float | None = None
    size: int
    r_multiple: float | None = None
    max_hold_seconds: int | None = None
    scaling_plan: list[str] | None = None
    trailing_stop_rule: str | None = None
    origin: Literal["auto", "manual"] = "auto"
    corroboration: list[str] = []


class OrderStatusChanged(BaseModel):
    """
    New in decision #171 — EX-9's recommended
    "one new venue-level order-status/rejection event rather than
    stretching PlanRejected" (PlanRejected is plan-level, fired before any
    order exists; this is order-level, fired by the Execution Engine after
    an OrderApproved has an order_id/client-order ID). Name/shape are a
    judgment call, overridable.

    This task only ever publishes `status="rejected"` — for an order that
    never reaches a venue (the mode/venue check, §6.2 step 3) or one a
    venue itself rejects (§6.2 step 5). Non-terminal transitions
    (submitted, partially_filled, filled, cancelled) belong to fill
    processing / Position Monitor-lite, which is out of scope here
    (`status` is typed narrowly to what this delivery actually produces
    rather than widened speculatively — widen when that later work needs
    to).
    """

    order_id: str  # the client-order ID (OrderApproved.order_id)
    status: Literal["rejected"]
    reason: str
    execution_venue: str | None = None  # None when rejected before reaching a venue


class OrderFilled(BaseModel):
    order_id: str
    side: Literal["BUY", "SELL"]
    qty: int
    fill_price: float
    fill_ts: datetime
