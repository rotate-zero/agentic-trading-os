"""Fixed-notional entry planning with no clock, I/O, or authorization effects."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from app.strategy_engine.base_strategy import Opportunity
from app.trade_planning.plan import FixedNotionalSizing, PlanningRefusal, ReferenceObservation, TradePlan


def plan_entry(
    symbol: str,
    opportunity: Opportunity,
    reference: ReferenceObservation | None,
    fixed_notional_usd: float,
    now: datetime,
) -> TradePlan | PlanningRefusal:
    """Plan an already-validated auto Opportunity using its envelope symbol.

    A finite positive configured notional is a caller precondition. Its
    violation is a technical error, not a planning refusal or fallback.
    """
    if not math.isfinite(fixed_notional_usd) or fixed_notional_usd <= 0:
        raise ValueError("fixed_notional_usd must be finite and positive")

    if reference is None or not math.isfinite(reference.price) or reference.price <= 0:
        return PlanningRefusal("no_reference_price")

    entry = reference.price
    stop = opportunity.structural_invalidation
    is_long = opportunity.direction == "BUY"
    if not math.isfinite(stop) or stop <= 0 or (stop >= entry if is_long else stop <= entry):
        return PlanningRefusal("invalid_stop_geometry")

    # Preserve the current Governor expression and float operands for valid inputs.
    size = math.floor(fixed_notional_usd / entry)
    if size < 1:
        return PlanningRefusal("notional_below_one_share")

    target = opportunity.structural_target
    if not math.isfinite(target) or target <= 0 or (target <= entry if is_long else target >= entry):
        return PlanningRefusal("invalid_target_geometry")

    risk_per_share = abs(Decimal(str(entry)) - Decimal(str(stop)))
    planned_risk_usd = Decimal(size) * risk_per_share
    r_multiple = round(abs(target - entry) / abs(entry - stop), 4)

    return TradePlan(
        symbol=symbol,
        direction="long" if is_long else "short",
        entry=entry,
        stop=stop,
        target=target,
        size=size,
        r_multiple=r_multiple,
        planned_risk_usd=planned_risk_usd,
        max_hold_seconds=None,
        origin="auto",
        corroboration=(),
        planned_at=now,
        reference_observed_at=reference.observed_at,
        reference_exchange_ts=reference.exchange_ts,
        target_on_profit_side=True,
        sizing=FixedNotionalSizing(fixed_notional_usd=fixed_notional_usd),
    )
