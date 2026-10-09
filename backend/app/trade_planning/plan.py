"""Immutable in-process planning values; these are not event payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ReferenceObservation:
    price: float
    observed_at: datetime | None  # Local PriceUpdated envelope time.
    exchange_ts: datetime | None  # Source time; absence is not replaced with local time.


@dataclass(frozen=True)
class FixedNotionalSizing:
    fixed_notional_usd: float
    method: Literal["fixed_notional"] = "fixed_notional"


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float | None
    size: int
    r_multiple: float | None
    planned_risk_usd: Decimal
    max_hold_seconds: int | None
    origin: Literal["auto", "manual"]
    corroboration: tuple[str, ...]
    planned_at: datetime
    reference_observed_at: datetime | None
    reference_exchange_ts: datetime | None
    target_on_profit_side: bool
    sizing: FixedNotionalSizing


@dataclass(frozen=True)
class PlanningRefusal:
    reason: Literal[
        "no_reference_price",
        "invalid_stop_geometry",
        "notional_below_one_share",
        "invalid_target_geometry",
    ]
